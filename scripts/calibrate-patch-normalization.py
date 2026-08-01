#!/usr/bin/env python3
"""
Offline per-patch loudness calibration for Surge XT patches.

Renders a fixed MPE performance gesture per patch, measures integrated LUFS via
ffmpeg loudnorm, and writes gain offsets to patch_normalization.json.

Requires: Surge XT CLI running with OSC on port 53280, ffmpeg, python-osc,
python-rtmidi (for live calibration). Use --dry-run to list targets without
Surge/ffmpeg.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def load_mpe_env() -> None:
    """Load /etc/mpe/mpe.env (or local config) so MPE_FAVORITES_NAME matches the appliance."""
    for candidate in (
        Path("/etc/mpe/mpe.env"),
        Path.home() / ".config" / "mpe" / "mpe.env",
        REPO_ROOT / "config" / "mpe.env",
    ):
        if not candidate.is_file():
            continue
        for line in candidate.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("\"'")
            if key and key not in os.environ:
                os.environ[key] = value
        break


load_mpe_env()

from patch_browser.calibration_loopback import (  # noqa: E402
    ensure_snd_aloop,
    resolve_loopback_capture_device,
    resolve_surge_loopback_interface,
)
from patch_browser.calibration_standalone import (  # noqa: E402
    detect_script_path,
    resolve_standalone_capture_device,
    resolve_surge_standalone_interface,
    should_restart_surge_for_standalone,
)
from patch_browser.calibration_teardown import (  # noqa: E402
    restore_mpe_audio_services,
    stop_mpe_audio_services,
    unload_snd_aloop_if_idle,
)
from patch_browser.patch_normalization import (  # noqa: E402
    PatchNormalizationStore,
    compute_gain_db,
    default_normalization_path,
    repo_starter_path,
)
from patch_browser.patch_loader import PatchLoader
from patch_browser.patch_scanner import (
    FAVORITES_NAME,
    PatchScanner,
    SURGE_PATCH_DIRS,
    favorites_display_name,
    resolve_user_patches_dir,
)

OSC_HOST = "127.0.0.1"
OSC_PORT = 53280
GESTURE_SECONDS = 3.0
NOTE = 60
MPE_CHANNEL = 2  # Surge MPE: channel 2 = first note channel
STRIKE_VELOCITY = 96
DEFAULT_PI_CAPTURE = "plughw:1,0"
MIN_VALID_LUFS = -39.0
MIN_VALID_TRUE_PEAK_DBTP = -35.0  # fallback when LUFS integration is dominated by capture silence
PATCH_LOAD_SETTLE_SECONDS = 0.75
MEASURE_RETRY_INTERVAL_SECONDS = 3.0
MEASURE_MAX_ATTEMPTS = 4  # ~0, 3, 6, 9s within 10s total

_interrupted = False
FAILURE_REPORT_PATH = Path("/tmp/calibration-last-failure.json")


def _handle_interrupt(_signum: int, _frame: object | None) -> None:
    global _interrupted
    _interrupted = True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Calibrate per-patch volume normalization")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument(
        "--favorites-only",
        action="store_true",
        help="Only patches in the Quick Select / favorites folder",
    )
    scope.add_argument(
        "--folder",
        metavar="NAME",
        help='Only patches under a category folder (e.g. "Quick Select")',
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output JSON path (default: MPE_NORMALIZATION_FILE or ~/.patch_browser_normalization.json)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List patches that would be calibrated; no Surge/ffmpeg required",
    )
    parser.add_argument(
        "--mock-lufs",
        type=float,
        metavar="LUFS",
        help="Skip render; write calibration using this measured LUFS (testing)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max patches to process (0 = all)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-calibrate patches that already have entries (overwrites gain_db)",
    )
    parser.add_argument(
        "--patch",
        metavar="NAME",
        help='Only calibrate patch(es) whose stem matches NAME (e.g. "Bowed String")',
    )
    parser.add_argument(
        "--use-loopback",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Route Surge through ALSA snd-aloop for digital capture (default: on when /etc/mpe/mpe.env exists)",
    )
    parser.add_argument(
        "--audio-device",
        default=None,
        help=(
            "ALSA device for ffmpeg capture (default: auto-detect Sound Blaster / plughw:3,0 on Pi)"
        ),
    )
    parser.add_argument(
        "--osc-host",
        default=OSC_HOST,
        help=f"Surge OSC host (default: {OSC_HOST})",
    )
    parser.add_argument(
        "--osc-port",
        type=int,
        default=OSC_PORT,
        help=f"Surge OSC port (default: {OSC_PORT})",
    )
    parser.add_argument(
        "--progress-json",
        action="store_true",
        help="Emit machine-readable progress lines on stdout (human logs go to stderr)",
    )
    parser.add_argument(
        "--no-restore-services",
        action="store_true",
        help="Skip systemd restore in finally (loader UI releases DRM first, then restores)",
    )
    return parser.parse_args()


def emit_progress(args: argparse.Namespace, payload: dict) -> None:
    """Print a JSON progress event when --progress-json is set."""
    if not args.progress_json:
        return
    try:
        print(json.dumps(payload), flush=True)
    except BrokenPipeError:
        # Loader exited or closed stdout — keep calibrating; teardown still runs.
        pass


def write_failure_report(
    *,
    patch_index: int,
    patch_name: str,
    total: int,
    reason: str,
    exit_code: int,
) -> None:
    """Persist last failure for post-mortems on the Pi (/tmp survives until reboot)."""
    payload = {
        "patch_index": patch_index,
        "patch_name": patch_name,
        "total": total,
        "reason": reason,
        "exit_code": exit_code,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    try:
        FAILURE_REPORT_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"Warning: could not write {FAILURE_REPORT_PATH}: {exc}", file=sys.stderr)


def favorites_folder_on_disk(parent: Path | None = None) -> Path:
    base = parent or resolve_user_patches_dir()
    return base / FAVORITES_NAME.lstrip("!")


def collect_patch_paths(args: argparse.Namespace) -> list[Path]:
    scanner = PatchScanner(SURGE_PATCH_DIRS)
    scanner.scan_patches()

    paths: list[Path] = []
    if args.favorites_only:
        fav_dir = favorites_folder_on_disk()
        if fav_dir.is_dir():
            paths = sorted(fav_dir.glob("*.fxp"))
    elif args.folder:
        folder_name = args.folder.lstrip("!")
        user_root = scanner.get_favorites_folder_path().parent
        target = user_root / folder_name
        if not target.is_dir():
            for patch_dir in SURGE_PATCH_DIRS:
                candidate = patch_dir / folder_name
                if candidate.is_dir():
                    target = candidate
                    break
        if target.is_dir():
            paths = sorted(target.rglob("*.fxp"))
        else:
            print(f"Warning: folder not found: {args.folder}", file=sys.stderr)
    else:
        with scanner.scan_lock:
            for patches in scanner.patches.values():
                for patch in patches:
                    paths.append(Path(patch["path"]))

    # Deduplicate by patch stem (favorites copies share calibration key)
    by_name: dict[str, Path] = {}
    for path in paths:
        by_name[path.stem] = path
    result = [by_name[k] for k in sorted(by_name)]

    if args.patch:
        needle = args.patch.strip()
        result = [p for p in result if p.stem == needle or needle.lower() in p.stem.lower()]
        if not result:
            print(f"Warning: no patches matched --patch {needle!r}", file=sys.stderr)

    return result


def find_surge_midi_port(*, announce: bool = True) -> int | None:
    try:
        import rtmidi
    except ImportError:
        print("Error: python-rtmidi required for live calibration", file=sys.stderr)
        return None

    midi_out = rtmidi.MidiOut()
    try:
        ports = midi_out.get_ports()

        def match_port(predicate) -> int | None:
            for index, name in enumerate(ports):
                if predicate(name.lower()):
                    return index
            return None

        # Prefer Surge's direct MIDI input port.
        port = match_port(lambda n: "surge" in n and "input" in n)
        if port is not None:
            return port

        port = match_port(lambda n: "surge" in n)
        if port is not None:
            return port

        # Pi fallback: route through ALSA Midi Through when Surge has no named port.
        port = match_port(lambda n: "midi through" in n or "through port" in n)
        if port is not None:
            if announce:
                print(
                    f"Using MIDI Through port {port!r} ({ports[port]}) — ensure Surge listens on Through",
                    file=sys.stderr,
                )
            return port

        if announce:
            print(f"Available MIDI ports: {ports}", file=sys.stderr)
        return None
    finally:
        try:
            midi_out.close_port()
        except Exception:
            pass


def wait_for_surge_midi_port(*, timeout_s: float = 8.0) -> int | None:
    """Retry MIDI port discovery while calibration Surge is starting."""
    deadline = time.monotonic() + timeout_s
    last_ports: list[str] = []
    while time.monotonic() < deadline:
        port = find_surge_midi_port(announce=False)
        if port is not None:
            return port
        try:
            import rtmidi

            last_ports = rtmidi.MidiOut().get_ports()
        except Exception:
            pass
        time.sleep(0.5)
    print(f"Available MIDI ports after wait: {last_ports}", file=sys.stderr)
    return find_surge_midi_port(announce=True)


def surge_cli_path() -> Path:
    env = os.environ.get("SURGE_CLI", "").strip()
    if env:
        return Path(env).expanduser()
    return Path.home() / "surge" / "build" / "surge_xt_products" / "surge-xt-cli"


def should_use_loopback(explicit: bool | None) -> bool:
    if explicit is not None:
        return explicit
    # A/B override: force one route regardless of MPE_AUDIO_PROFILE, for
    # comparing loopback vs. standalone/dsnoop capture on the same hardware.
    route_override = os.environ.get("MPE_CAL_ROUTE", "").strip().lower()
    if route_override == "loopback":
        return True
    if route_override == "standalone":
        return False
    profile = os.environ.get("MPE_AUDIO_PROFILE", "standalone").strip().lower()
    if profile == "standalone":
        # Dedicated cal Surge on Sound Blaster + dsnoop capture (see standalone setup).
        return False
    return Path("/etc/mpe/mpe.env").is_file()


def start_surge_loopback() -> str:
    cli = surge_cli_path()
    if not cli.is_file():
        raise RuntimeError(f"Surge CLI not found: {cli}")
    ensure_snd_aloop()
    interface = resolve_surge_loopback_interface(cli)
    buffer_size = os.environ.get("MPE_SURGE_BUFFER_SIZE", "1024")
    log_path = Path.home() / "surge-cli-calibration.log"
    with log_path.open("a") as log:
        log.write(
            f"\n{time.strftime('%Y-%m-%d %H:%M:%S')}: calibration loopback start "
            f"(interface={interface}, buffer={buffer_size})\n"
        )
        subprocess.Popen(
            [
                str(cli),
                "--all-midi-inputs",
                "--mpe-enable",
                "--mpe-pitch-bend-range=48",
                f"--audio-interface={interface}",
                f"--buffer-size={buffer_size}",
                f"--osc-in-port={OSC_PORT}",
                "--no-stdin",
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    time.sleep(2.5)
    return interface


def start_surge_standalone() -> str:
    cli = surge_cli_path()
    if not cli.is_file():
        raise RuntimeError(f"Surge CLI not found: {cli}")
    script = detect_script_path(REPO_ROOT)
    if not script.is_file():
        raise RuntimeError(f"detect-audio-device.sh not found: {script}")
    interface = resolve_surge_standalone_interface(cli, detect_script=script)
    buffer_size = os.environ.get("MPE_SURGE_BUFFER_SIZE", "1024")
    log_path = Path.home() / "surge-cli-calibration.log"
    with log_path.open("a") as log:
        log.write(
            f"\n{time.strftime('%Y-%m-%d %H:%M:%S')}: calibration standalone start "
            f"(interface={interface}, buffer={buffer_size})\n"
        )
        subprocess.Popen(
            [
                str(cli),
                "--all-midi-inputs",
                "--mpe-enable",
                "--mpe-pitch-bend-range=48",
                f"--audio-interface={interface}",
                f"--buffer-size={buffer_size}",
                f"--osc-in-port={OSC_PORT}",
                "--no-stdin",
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    time.sleep(2.5)
    return interface


def detect_capture_device(explicit: str | None, *, use_loopback: bool) -> str:
    """Resolve ALSA capture device for Surge output monitoring on the Pi."""
    if explicit:
        return explicit
    if use_loopback:
        ensure_snd_aloop()
        capture = resolve_loopback_capture_device()
        print(f"Using ALSA loopback capture: {capture}", file=sys.stderr)
        return capture

    sb_capture = resolve_standalone_capture_device()
    if sb_capture:
        print(f"Auto-detected Sound Blaster snoop capture: {sb_capture}", file=sys.stderr)
        return sb_capture

    try:
        result = subprocess.run(
            ["arecord", "-l"],
            capture_output=True,
            text=True,
            check=False,
        )
        for line in result.stdout.splitlines():
            lower = line.lower()
            if "sound blaster" in lower and "card" in lower:
                match = re.search(r"card\s+(\d+):", line, re.IGNORECASE)
                if match:
                    capture = f"plughw:{match.group(1)},0"
                    print(f"Auto-detected Sound Blaster capture (fallback): {capture}", file=sys.stderr)
                    return capture
            if "card" in lower and "usb audio" in lower and "sound blaster" not in lower:
                match = re.search(r"card\s+(\d+):", line, re.IGNORECASE)
                if match:
                    capture = f"plughw:{match.group(1)},0"
                    print(f"Auto-detected USB capture: {capture}", file=sys.stderr)
                    return capture
    except OSError as exc:
        print(f"Warning: arecord probe failed: {exc}", file=sys.stderr)

    print(f"Using default Pi capture device: {DEFAULT_PI_CAPTURE}", file=sys.stderr)
    return DEFAULT_PI_CAPTURE


def open_midi_out(port_index: int):
    """Open one ALSA sequencer client for the whole calibration run."""
    import rtmidi

    midi_out = rtmidi.MidiOut()
    midi_out.open_port(port_index)
    return midi_out


def close_midi_out(midi_out: object | None) -> None:
    if midi_out is None:
        return
    try:
        midi_out.close_port()  # type: ignore[union-attr]
    except Exception:
        pass


def send_performance_gesture(midi_out: object, pre_roll: float = 0.25) -> None:
    time.sleep(pre_roll)

    ch = MPE_CHANNEL - 1
    note_on = 0x90 | ch
    note_off = 0x80 | ch
    pressure_cc = 0xE0 | ch

    midi_out.send_message([note_on, NOTE, STRIKE_VELOCITY])  # type: ignore[union-attr]

    steps = 24
    hold_seconds = 1.8
    step_sleep = hold_seconds / steps
    for step in range(steps + 1):
        pressure = int(127 * step / steps)
        midi_out.send_message([pressure_cc, pressure & 0x7F, (pressure >> 7) & 0x7F])  # type: ignore[union-attr]
        time.sleep(step_sleep)

    time.sleep(0.15)
    midi_out.send_message([note_off, NOTE, 0])  # type: ignore[union-attr]
    time.sleep(0.2)


def is_invalid_measurement(lufs: float, true_peak: float) -> bool:
    """True when capture is silent or loudnorm returned unusable values (-inf LUFS, etc.)."""
    if math.isfinite(true_peak) and true_peak >= MIN_VALID_TRUE_PEAK_DBTP:
        return False
    if not math.isfinite(lufs) or lufs < MIN_VALID_LUFS:
        return True
    if not math.isfinite(true_peak):
        return True
    return False


def capture_gesture_wav(midi_out: object, audio_device: str) -> Path:
    """Record the standard MPE gesture to a temporary WAV; caller must unlink the path."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav = Path(tmp.name)
    capture = subprocess.Popen(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "alsa",
            "-i",
            audio_device,
            "-t",
            str(GESTURE_SECONDS),
            "-ac",
            "2",
            str(wav),
        ]
    )
    time.sleep(0.15)
    send_performance_gesture(midi_out)
    capture.wait()
    if capture.returncode != 0:
        wav.unlink(missing_ok=True)
        raise RuntimeError("ffmpeg capture failed")
    return wav


def measure_lufs(wav_path: Path) -> tuple[float, float]:
    """Return (integrated_lufs, true_peak_dbtp) from ffmpeg loudnorm."""
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-i",
        str(wav_path),
        "-af",
        "loudnorm=print_format=json",
        "-f",
        "null",
        "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    blob = result.stderr + result.stdout
    match = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", blob, re.DOTALL)
    if not match:
        raise RuntimeError(f"Could not parse loudnorm output from ffmpeg:\n{blob[-2000:]}")
    data = json.loads(match.group(0))
    lufs = float(data["input_i"])
    true_peak = float(data["input_tp"])
    return lufs, true_peak


def calibrate_patch(
    patch_path: Path,
    loader: PatchLoader,
    store: PatchNormalizationStore,
    *,
    audio_device: str,
    mock_lufs: float | None,
    dry_run: bool,
    midi_out: object | None = None,
) -> bool:
    name = patch_path.stem
    if dry_run:
        existing = store.get_entry(name)
        status = "skip (has entry)" if existing and existing.get("gain_db") is not None else "would calibrate"
        print(f"  [{status}] {name}")
        return False

    if mock_lufs is not None:
        lufs = mock_lufs
        true_peak = mock_lufs + 6.0
    else:
        if not loader.load_patch(str(patch_path), apply_normalization=False):
            print(f"  [fail] OSC load failed: {name}", file=sys.stderr)
            return False

        if midi_out is None:
            return False

        # Unity gain for measurement — stored calibration must not skew capture.
        loader.user_volume_trim = 1.0
        loader._patch_gain_linear = 1.0
        loader._send_combined_volume()
        time.sleep(PATCH_LOAD_SETTLE_SECONDS)

        lufs = float("-inf")
        true_peak = float("-inf")
        for attempt in range(1, MEASURE_MAX_ATTEMPTS + 1):
            if attempt > 1:
                print(
                    f"  [retry] {name}: waiting for patch/load "
                    f"(attempt {attempt}/{MEASURE_MAX_ATTEMPTS})...",
                    file=sys.stderr,
                )
                time.sleep(MEASURE_RETRY_INTERVAL_SECONDS)

            wav: Path | None = None
            try:
                wav = capture_gesture_wav(midi_out, audio_device)
                lufs, true_peak = measure_lufs(wav)
            except RuntimeError:
                print(f"  [fail] ffmpeg capture: {name}", file=sys.stderr)
                return False
            finally:
                if wav is not None:
                    wav.unlink(missing_ok=True)

            if not is_invalid_measurement(lufs, true_peak):
                break

        if is_invalid_measurement(lufs, true_peak):
            lufs_display = f"{lufs:.1f}" if math.isfinite(lufs) else str(lufs)
            peak_display = f"{true_peak:.1f}" if math.isfinite(true_peak) else str(true_peak)
            print(
                f"  [fail] {name}: measured {lufs_display} LUFS (peak {peak_display} dBTP) after "
                f"{MEASURE_MAX_ATTEMPTS} attempt(s) — patch may still be loading or capture chain "
                f"did not record Surge output; check MIDI routing and --audio-device",
                file=sys.stderr,
            )
            return False

    gain_db = compute_gain_db(lufs, true_peak)
    if lufs < -40.0:
        print(
            f"  [warn] {name}: measured {lufs:.1f} LUFS — capture may be wrong device "
            f"(expect roughly -30 to -10 LUFS for audible Surge output)",
            file=sys.stderr,
        )
    if gain_db > 20.0:
        print(
            f"  [warn] {name}: gain {gain_db:+.1f} dB is very high — re-check ALSA routing before trusting",
            file=sys.stderr,
        )
    store.set_calibration(name, gain_db, lufs, true_peak_dbtp=true_peak)
    store.save()
    print(f"  [ok] {name}: {lufs:.1f} LUFS, peak {true_peak:.1f} dBTP -> gain {gain_db:+.2f} dB")
    return True


def main() -> int:
    args = parse_args()
    output_path = args.output or default_normalization_path()
    use_loopback = should_use_loopback(args.use_loopback)
    standalone_restart = should_restart_surge_for_standalone(
        use_loopback=use_loopback,
        dry_run=args.dry_run,
        mock_lufs=args.mock_lufs,
    )
    audio_device = detect_capture_device(args.audio_device, use_loopback=use_loopback)
    if (use_loopback or standalone_restart) and not args.dry_run and args.mock_lufs is None:
        # Resolve capture after production Surge stops (and snd-aloop loads for loopback).
        audio_device = None  # filled in setup below

    patch_paths = collect_patch_paths(args)
    if args.limit > 0:
        patch_paths = patch_paths[: args.limit]

    if not patch_paths:
        msg = "No patches matched the selection."
        print(msg, file=sys.stderr)
        emit_progress(args, {"type": "error", "message": msg})
        emit_progress(args, {"type": "done", "updated": 0, "exit_code": 1})
        return 1

    store = PatchNormalizationStore(output_path)
    if args.force or args.mock_lufs is not None:
        targets = patch_paths
    else:
        missing = store.list_missing([p.stem for p in patch_paths])
        targets = [p for p in patch_paths if p.stem in missing]

    print(f"Output: {output_path}")
    capture_label = audio_device if audio_device else "(resolved after Surge restart)"
    print(f"Capture device: {capture_label}")
    if use_loopback:
        print("Surge routing: ALSA loopback (temporary service restart for calibration)")
    elif standalone_restart:
        print("Surge routing: Sound Blaster (temporary service restart for calibration)")
    if args.favorites_only:
        print(f"Scope: favorites ({favorites_display_name()})")
    elif args.folder:
        print(f"Scope: folder {args.folder!r}")
    else:
        print("Scope: all scanned patches")
    missing_count = len(store.list_missing([p.stem for p in patch_paths]))
    print(
        f"Targets: {len(targets)} patch(es) ({len(patch_paths)} in scope, "
        f"{missing_count} missing entries"
        f"{', force overwrite' if args.force else ''})"
    )

    scope_label = (
        f"Quick Select ({favorites_display_name()})"
        if args.favorites_only
        else (f'Folder "{args.folder}"' if args.folder else "All patches")
    )
    emit_progress(
        args,
        {"type": "start", "total": len(targets), "scope": scope_label},
    )

    if not targets:
        print("No patches need calibration (all entries present). Use --force to re-run.", file=sys.stderr)
        emit_progress(args, {"type": "done", "updated": 0, "exit_code": 0})
        return 0

    if args.dry_run:
        for path in targets:
            calibrate_patch(
                path, None, store, audio_device=audio_device, mock_lufs=None, dry_run=True
            )
        est = len(targets) * (GESTURE_SECONDS + 1.5)
        print(f"Estimated time: ~{est / 60:.1f} min at {GESTURE_SECONDS}s capture per patch")
        print(f"Starter file in repo: {repo_starter_path()}")
        emit_progress(args, {"type": "done", "updated": 0, "exit_code": 0})
        return 0

    loader = PatchLoader(osc_host=args.osc_host, osc_port=args.osc_port)
    if not loader.osc_enabled:
        msg = "OSC client unavailable — is Surge running on the OSC port?"
        print(f"Error: {msg}", file=sys.stderr)
        emit_progress(args, {"type": "error", "message": msg})
        emit_progress(args, {"type": "done", "updated": 0, "exit_code": 1})
        return 1

    cal_surge_started = False
    if use_loopback and args.mock_lufs is None:
        try:
            emit_progress(
                args,
                {"type": "setup", "message": "Stopping patch browser and Surge…"},
            )
            stop_mpe_audio_services()
            audio_device = detect_capture_device(args.audio_device, use_loopback=True)
            print(f"Capture device: {audio_device}", file=sys.stderr)
            emit_progress(args, {"type": "setup", "message": "Starting Surge for measurement…"})
            loopback_interface = start_surge_loopback()
            cal_surge_started = True
            print(f"Surge loopback interface: {loopback_interface}", file=sys.stderr)
            loader = PatchLoader(osc_host=args.osc_host, osc_port=args.osc_port)
            if not loader.osc_enabled:
                raise RuntimeError("OSC unavailable after loopback Surge start")
        except Exception as exc:
            print(f"Error: loopback calibration setup failed: {exc}", file=sys.stderr)
            emit_progress(args, {"type": "error", "message": str(exc)})
            if cal_surge_started:
                restore_mpe_audio_services()
            emit_progress(args, {"type": "done", "updated": 0, "exit_code": 1})
            return 1
    elif standalone_restart:
        try:
            emit_progress(
                args,
                {"type": "setup", "message": "Stopping production Surge for measurement…"},
            )
            stop_mpe_audio_services()
            emit_progress(args, {"type": "setup", "message": "Starting Surge on Sound Blaster…"})
            standalone_interface = start_surge_standalone()
            cal_surge_started = True
            audio_device = detect_capture_device(args.audio_device, use_loopback=False)
            print(f"Capture device: {audio_device}", file=sys.stderr)
            print(f"Surge standalone interface: {standalone_interface}", file=sys.stderr)
            loader = PatchLoader(osc_host=args.osc_host, osc_port=args.osc_port)
            if not loader.osc_enabled:
                raise RuntimeError("OSC unavailable after standalone Surge start")
        except Exception as exc:
            print(f"Error: standalone calibration setup failed: {exc}", file=sys.stderr)
            emit_progress(args, {"type": "error", "message": str(exc)})
            if cal_surge_started:
                restore_mpe_audio_services()
            emit_progress(args, {"type": "done", "updated": 0, "exit_code": 1})
            return 1

    signal.signal(signal.SIGTERM, _handle_interrupt)
    signal.signal(signal.SIGINT, _handle_interrupt)

    midi_port = wait_for_surge_midi_port() if args.mock_lufs is None else None
    if midi_port is None and args.mock_lufs is None:
        msg = "Surge MIDI port not found — is Surge running with MIDI inputs?"
        print(f"Error: {msg}", file=sys.stderr)
        emit_progress(args, {"type": "error", "message": msg})
        if cal_surge_started:
            restore_mpe_audio_services()
        emit_progress(args, {"type": "done", "updated": 0, "exit_code": 1})
        return 1

    updated = 0
    exit_code = 0
    midi_out: object | None = None
    last_patch_index = 0
    last_patch_name = ""
    try:
        if args.mock_lufs is None:
            midi_out = open_midi_out(midi_port)
        for index, path in enumerate(targets, start=1):
            if _interrupted:
                print("Calibration interrupted — keeping partial progress.", file=sys.stderr)
                break
            name = path.stem
            last_patch_index = index
            last_patch_name = name
            print(f"[{index}/{len(targets)}] {name}", file=sys.stderr if args.progress_json else sys.stdout)
            emit_progress(
                args,
                {"type": "patch", "index": index, "total": len(targets), "name": name},
            )
            try:
                ok = calibrate_patch(
                    path,
                    loader,
                    store,
                    audio_device=audio_device,
                    mock_lufs=args.mock_lufs,
                    dry_run=False,
                    midi_out=midi_out,
                )
            except Exception as exc:
                msg = f"{name}: {exc}"
                print(f"  [fail] {msg}", file=sys.stderr)
                write_failure_report(
                    patch_index=index,
                    patch_name=name,
                    total=len(targets),
                    reason=str(exc),
                    exit_code=1,
                )
                emit_progress(args, {"type": "error", "message": msg})
                exit_code = 1
                break
            emit_progress(
                args,
                {
                    "type": "patch_done",
                    "index": index,
                    "total": len(targets),
                    "name": name,
                    "ok": ok,
                },
            )
            if ok:
                updated += 1
    finally:
        close_midi_out(midi_out)
        if cal_surge_started and not args.no_restore_services:
            emit_progress(
                args,
                {"type": "setup", "message": "Restarting patch browser and Surge…"},
            )
            print("Restoring surge-xt-cli and touch-patch-browser services...", file=sys.stderr)
            restore_mpe_audio_services()

    if updated:
        print(f"Wrote {updated} calibration entries to {output_path}")
    else:
        print("No entries updated.")
        exit_code = 0
    if _interrupted:
        exit_code = 130
        write_failure_report(
            patch_index=last_patch_index,
            patch_name=last_patch_name,
            total=len(targets),
            reason="interrupted",
            exit_code=exit_code,
        )
    emit_progress(args, {"type": "done", "updated": updated, "exit_code": exit_code})
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
