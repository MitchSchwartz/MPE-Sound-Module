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
from dataclasses import dataclass
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
from patch_browser.calibration_constants import (  # noqa: E402
    CALIBRATION_SECONDS_PER_PATCH_ESTIMATE,
    estimate_calibration_duration_seconds,
)
from patch_browser.calibration_teardown import (  # noqa: E402
    restore_mpe_audio_services,
    stop_mpe_audio_services,
    unload_snd_aloop_if_idle,
)
from patch_browser.patch_normalization import (  # noqa: E402
    PatchNormalizationStore,
    SAFE_PEAK_DBTP,
    compute_gain_db,
    compute_gain_db_dual_anchor,
    db_to_linear,
    default_normalization_path,
    repo_starter_path,
)
from patch_browser.patch_pressure import (  # noqa: E402
    PatchPressureStore,
    LIGHT_TOUCH_GESTURE_SECONDS,
    LIGHT_TOUCH_HOLD_SECONDS,
    LIGHT_TOUCH_PRESSURE,
    compute_touch_calibration_floor,
    default_pressure_path,
    resolve_light_touch_target,
)
from patch_browser.patch_loader import PatchLoader
from patch_browser.surge_audio import DEFAULT_BUFFER, DEFAULT_SAMPLE_RATE  # noqa: E402
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
# Progressive retry: slow-attack/filter-sweep patches (e.g. long acid filter opens)
# don't reach real loudness in a 3s gesture. Each retry after an invalid measurement
# holds the note longer instead of just re-trying the same short gesture — capped at
# MEASURE_MAX_ATTEMPTS entries. First value must equal GESTURE_SECONDS (base case).
GESTURE_DURATIONS_SECONDS = (3.0, 5.0, 8.0, 12.0)
NOTE = 60
MPE_CHANNEL = 2  # Surge MPE: channel 2 = first note channel
STRIKE_VELOCITY = 96
STRIKE_ANCHOR_VELOCITY = 127
STRIKE_ANCHOR_PRESSURE = 8
SUSTAIN_ANCHOR_VELOCITY = 80
SUSTAIN_ANCHOR_PRESSURE = 127
ANCHOR_HOLD_SECONDS = 1.5
ANCHOR_GESTURE_SECONDS = 2.5
SUSTAIN_ANCHOR_GESTURE_SECONDS = (2.5, 4.0, 6.0, 8.0)
CLOSED_LOOP_VERIFY_PASSES = 3
DEFAULT_PI_CAPTURE = "plughw:1,0"
MIN_VALID_LUFS = -39.0  # legacy reference only — validity uses peak floor below
MIN_VALID_TRUE_PEAK_DBTP = -45.0  # reject captures with no audible Surge output
PATCH_LOAD_SETTLE_SECONDS = 0.75
MEASURE_RETRY_INTERVAL_SECONDS = 3.0
MEASURE_MAX_ATTEMPTS = len(GESTURE_DURATIONS_SECONDS)  # ~0, 3, 8, 16s within ~27s total

_interrupted = False
FAILURE_REPORT_PATH = Path("/tmp/calibration-last-failure.json")


@dataclass
class CalibrateResult:
    ok: bool
    lufs_light: float | None = None
    lufs_strike: float | None = None
    lufs_sustain: float | None = None


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
        "--no-touch-cal",
        action="store_true",
        help="Skip light-touch measurement and Touch floor writes",
    )
    parser.add_argument(
        "--pressure-output",
        type=Path,
        default=None,
        help="Touch calibration JSON (default: MPE_PRESSURE_FILE or ~/.patch_browser_pressure.json)",
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
    # Escape hatch: force the Sound Blaster/dsnoop path if loopback ever
    # regresses again. Loopback is the default — A/B on 2026-08-01 measured
    # it 4-14 dB hotter than dsnoop on the same patches (see PATCH_NORMALIZATION.md).
    route_override = os.environ.get("MPE_CAL_ROUTE", "").strip().lower()
    if route_override == "standalone":
        return False
    if route_override == "loopback":
        return True
    return True


def start_surge_loopback() -> str:
    cli = surge_cli_path()
    if not cli.is_file():
        raise RuntimeError(f"Surge CLI not found: {cli}")
    ensure_snd_aloop()
    interface = resolve_surge_loopback_interface(cli)
    buffer_size = os.environ.get("MPE_SURGE_BUFFER_SIZE", str(DEFAULT_BUFFER))
    sample_rate = os.environ.get("MPE_SURGE_SAMPLE_RATE", str(DEFAULT_SAMPLE_RATE))
    log_path = Path.home() / "surge-cli-calibration.log"
    with log_path.open("a") as log:
        log.write(
            f"\n{time.strftime('%Y-%m-%d %H:%M:%S')}: calibration loopback start "
            f"(interface={interface}, buffer={buffer_size}, rate={sample_rate})\n"
        )
        subprocess.Popen(
            [
                str(cli),
                "--all-midi-inputs",
                "--mpe-enable",
                "--mpe-pitch-bend-range=48",
                f"--audio-interface={interface}",
                f"--buffer-size={buffer_size}",
                f"--sample-rate={sample_rate}",
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
    buffer_size = os.environ.get("MPE_SURGE_BUFFER_SIZE", str(DEFAULT_BUFFER))
    sample_rate = os.environ.get("MPE_SURGE_SAMPLE_RATE", str(DEFAULT_SAMPLE_RATE))
    log_path = Path.home() / "surge-cli-calibration.log"
    with log_path.open("a") as log:
        log.write(
            f"\n{time.strftime('%Y-%m-%d %H:%M:%S')}: calibration standalone start "
            f"(interface={interface}, buffer={buffer_size}, rate={sample_rate})\n"
        )
        subprocess.Popen(
            [
                str(cli),
                "--all-midi-inputs",
                "--mpe-enable",
                "--mpe-pitch-bend-range=48",
                f"--audio-interface={interface}",
                f"--buffer-size={buffer_size}",
                f"--sample-rate={sample_rate}",
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


def hold_seconds_for_gesture(gesture_seconds: float, pre_roll: float = 0.25) -> float:
    """Note-hold duration that fits inside gesture_seconds with pre-roll/tail margin."""
    base_hold = 1.8
    if gesture_seconds <= GESTURE_DURATIONS_SECONDS[0]:
        return base_hold
    # Tail overhead: pre_roll + final 0.15s pressure step + 0.2s post note-off.
    return max(base_hold, gesture_seconds - pre_roll - 0.35 - 0.3)


def send_performance_gesture(
    midi_out: object, pre_roll: float = 0.25, hold_seconds: float = 1.8
) -> None:
    time.sleep(pre_roll)

    ch = MPE_CHANNEL - 1
    note_on = 0x90 | ch
    note_off = 0x80 | ch
    pressure_cc = 0xE0 | ch

    midi_out.send_message([note_on, NOTE, STRIKE_VELOCITY])  # type: ignore[union-attr]

    steps = 24
    step_sleep = hold_seconds / steps
    for step in range(steps + 1):
        pressure = int(127 * step / steps)
        midi_out.send_message([pressure_cc, pressure & 0x7F, (pressure >> 7) & 0x7F])  # type: ignore[union-attr]
        time.sleep(step_sleep)

    time.sleep(0.15)
    midi_out.send_message([note_off, NOTE, 0])  # type: ignore[union-attr]
    time.sleep(0.2)


def send_light_touch_gesture(
    midi_out: object, pre_roll: float = 0.25, hold_seconds: float = LIGHT_TOUCH_HOLD_SECONDS
) -> None:
    """Strike at fixed low pressure — measures light-touch loudness per patch."""
    time.sleep(pre_roll)

    ch = MPE_CHANNEL - 1
    note_on = 0x90 | ch
    note_off = 0x80 | ch
    pressure_cc = 0xE0 | ch
    pressure = max(0, min(127, int(LIGHT_TOUCH_PRESSURE)))

    midi_out.send_message([note_on, NOTE, STRIKE_VELOCITY])  # type: ignore[union-attr]
    midi_out.send_message(  # type: ignore[union-attr]
        [pressure_cc, pressure & 0x7F, (pressure >> 7) & 0x7F]
    )
    time.sleep(hold_seconds)
    midi_out.send_message([note_off, NOTE, 0])  # type: ignore[union-attr]
    time.sleep(0.2)


def capture_light_touch_wav(midi_out: object, audio_device: str) -> Path:
    """Record the light-touch gesture to a temporary WAV; caller must unlink."""
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
            str(LIGHT_TOUCH_GESTURE_SECONDS),
            "-ac",
            "2",
            str(wav),
        ]
    )
    time.sleep(0.15)
    send_light_touch_gesture(midi_out)
    capture.wait()
    if capture.returncode != 0:
        wav.unlink(missing_ok=True)
        raise RuntimeError("ffmpeg light-touch capture failed")
    return wav


def measure_light_touch_lufs(
    midi_out: object, audio_device: str
) -> tuple[float, float] | None:
    """Return (lufs, true_peak) for light-touch gesture, or None when inaudible."""
    wav: Path | None = None
    try:
        wav = capture_light_touch_wav(midi_out, audio_device)
        lufs, true_peak = measure_lufs(wav)
    except RuntimeError:
        return None
    finally:
        if wav is not None:
            wav.unlink(missing_ok=True)
    if is_invalid_measurement(lufs, true_peak):
        return None
    return lufs, true_peak


def send_anchor_gesture(
    midi_out: object,
    *,
    velocity: int,
    pressure: int,
    hold_seconds: float = ANCHOR_HOLD_SECONDS,
    pre_roll: float = 0.25,
) -> None:
    time.sleep(pre_roll)
    ch = MPE_CHANNEL - 1
    note_on = 0x90 | ch
    note_off = 0x80 | ch
    pressure_cc = 0xE0 | ch
    press = max(0, min(127, int(pressure)))
    vel = max(1, min(127, int(velocity)))
    midi_out.send_message([note_on, NOTE, vel])  # type: ignore[union-attr]
    midi_out.send_message(  # type: ignore[union-attr]
        [pressure_cc, press & 0x7F, (press >> 7) & 0x7F]
    )
    time.sleep(hold_seconds)
    midi_out.send_message([note_off, NOTE, 0])  # type: ignore[union-attr]
    time.sleep(0.2)


def capture_anchor_wav(
    midi_out: object,
    audio_device: str,
    *,
    velocity: int,
    pressure: int,
    gesture_seconds: float = ANCHOR_GESTURE_SECONDS,
    hold_seconds: float = ANCHOR_HOLD_SECONDS,
) -> Path:
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
            str(gesture_seconds),
            "-ac",
            "2",
            str(wav),
        ]
    )
    time.sleep(0.15)
    send_anchor_gesture(
        midi_out, velocity=velocity, pressure=pressure, hold_seconds=hold_seconds
    )
    capture.wait()
    if capture.returncode != 0:
        wav.unlink(missing_ok=True)
        raise RuntimeError("ffmpeg anchor capture failed")
    return wav


def measure_anchor_lufs(
    midi_out: object,
    audio_device: str,
    *,
    velocity: int,
    pressure: int,
    gesture_seconds: float = ANCHOR_GESTURE_SECONDS,
    hold_seconds: float = ANCHOR_HOLD_SECONDS,
) -> tuple[float, float] | None:
    wav: Path | None = None
    try:
        wav = capture_anchor_wav(
            midi_out,
            audio_device,
            velocity=velocity,
            pressure=pressure,
            gesture_seconds=gesture_seconds,
            hold_seconds=hold_seconds,
        )
        lufs, true_peak = measure_lufs(wav)
    except RuntimeError:
        return None
    finally:
        if wav is not None:
            wav.unlink(missing_ok=True)
    if is_invalid_measurement(lufs, true_peak):
        return None
    return lufs, true_peak


def measure_strike_anchor_lufs(midi_out: object, audio_device: str) -> tuple[float, float] | None:
    return measure_anchor_lufs(
        midi_out,
        audio_device,
        velocity=STRIKE_ANCHOR_VELOCITY,
        pressure=STRIKE_ANCHOR_PRESSURE,
    )


def measure_sustain_anchor_lufs(midi_out: object, audio_device: str) -> tuple[float, float] | None:
    for gesture_seconds in SUSTAIN_ANCHOR_GESTURE_SECONDS:
        hold = max(ANCHOR_HOLD_SECONDS, gesture_seconds - 0.8)
        result = measure_anchor_lufs(
            midi_out,
            audio_device,
            velocity=SUSTAIN_ANCHOR_VELOCITY,
            pressure=SUSTAIN_ANCHOR_PRESSURE,
            gesture_seconds=gesture_seconds,
            hold_seconds=hold,
        )
        if result is not None:
            return result
    return None


def apply_measurement_gain(loader: PatchLoader, gain_db: float) -> None:
    linear = db_to_linear(gain_db)
    loader.user_volume_trim = 1.0
    if not loader.osc_enabled or loader.osc_client is None:
        return
    loader.osc_client.send_message("/param/a/amp/volume", linear)
    loader.osc_client.send_message("/param/b/amp/volume", linear)


def finalize_gain_with_closed_loop(
    loader: PatchLoader,
    midi_out: object,
    audio_device: str,
    gain_db: float,
) -> tuple[float, float, float]:
    trial = float(gain_db)
    last_lufs = float("-inf")
    last_peak = float("-inf")
    for _ in range(CLOSED_LOOP_VERIFY_PASSES):
        apply_measurement_gain(loader, trial)
        time.sleep(0.35)
        measured = measure_sustain_anchor_lufs(midi_out, audio_device)
        if measured is None:
            break
        last_lufs, last_peak = measured
        if last_peak <= SAFE_PEAK_DBTP + 0.5:
            return trial, last_lufs, last_peak
        trial = compute_gain_db(last_lufs, last_peak)
    return trial, last_lufs, last_peak


def is_invalid_measurement(lufs: float, true_peak: float) -> bool:
    """True when capture is silent or loudnorm returned unusable values.

    Validity is peak-based: quiet-but-real patches (e.g. -47 LUFS, -29 dBTP) are
    accepted so compute_gain_db can apply the large boost they need. Reject only
    when true peak shows no audible signal (broken routing / failed gesture).
    """
    if not math.isfinite(lufs) or not math.isfinite(true_peak):
        return True
    return true_peak < MIN_VALID_TRUE_PEAK_DBTP


def capture_gesture_wav(
    midi_out: object, audio_device: str, gesture_seconds: float = GESTURE_SECONDS
) -> Path:
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
            str(gesture_seconds),
            "-ac",
            "2",
            str(wav),
        ]
    )
    time.sleep(0.15)
    send_performance_gesture(midi_out, hold_seconds=hold_seconds_for_gesture(gesture_seconds))
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
    touch_cal: bool = True,
) -> CalibrateResult:
    name = patch_path.stem
    if dry_run:
        existing = store.get_entry(name)
        status = "skip (has entry)" if existing and existing.get("gain_db") is not None else "would calibrate"
        print(f"  [{status}] {name}")
        return CalibrateResult(ok=False)

    if mock_lufs is not None:
        lufs_strike = mock_lufs
        lufs_sustain = mock_lufs - 2.0
        true_peak = mock_lufs + 6.0
        peak_strike = true_peak
        peak_sustain = true_peak
        lufs_light = (mock_lufs - 12.0) if touch_cal else None
    else:
        if not loader.load_patch(str(patch_path), apply_normalization=False):
            print(f"  [fail] OSC load failed: {name}", file=sys.stderr)
            return CalibrateResult(ok=False)

        if midi_out is None:
            return CalibrateResult(ok=False)

        loader.user_volume_trim = 1.0
        loader._patch_gain_linear = 1.0
        loader._send_combined_volume()
        time.sleep(PATCH_LOAD_SETTLE_SECONDS)

        strike = measure_strike_anchor_lufs(midi_out, audio_device)
        if strike is None:
            print(f"  [fail] {name}: strike anchor inaudible", file=sys.stderr)
            return CalibrateResult(ok=False)
        lufs_strike, peak_strike = strike

        sustain = measure_sustain_anchor_lufs(midi_out, audio_device)
        if sustain is None:
            print(f"  [fail] {name}: sustain anchor inaudible", file=sys.stderr)
            return CalibrateResult(ok=False)
        lufs_sustain, peak_sustain = sustain

        true_peak = max(peak_strike, peak_sustain)
        lufs = lufs_sustain

        lufs_light: float | None = None
        if touch_cal:
            light = measure_light_touch_lufs(midi_out, audio_device)
            if light is None:
                print(
                    f"  [warn] {name}: light-touch capture inaudible — Norm saved, Touch skipped",
                    file=sys.stderr,
                )
            else:
                lufs_light = light[0]

    gain_db = compute_gain_db_dual_anchor(
        lufs_strike,
        peak_strike if mock_lufs is None else true_peak,
        lufs_sustain,
        peak_sustain if mock_lufs is None else true_peak,
    )
    if min(lufs_strike, lufs_sustain) < -48.0:
        print(
            f"  [warn] {name}: quiet patch (strike {lufs_strike:.1f}, sustain {lufs_sustain:.1f} LUFS)",
            file=sys.stderr,
        )
    if gain_db > 20.0:
        print(
            f"  [warn] {name}: gain {gain_db:+.1f} dB is very high — re-check ALSA routing before trusting",
            file=sys.stderr,
        )

    if mock_lufs is None and midi_out is not None:
        gain_db, verify_lufs, verify_peak = finalize_gain_with_closed_loop(
            loader, midi_out, audio_device, gain_db
        )
        if math.isfinite(verify_lufs):
            lufs = verify_lufs
            true_peak = verify_peak

    store.set_calibration(
        name,
        gain_db,
        lufs,
        true_peak_dbtp=true_peak,
        strike_lufs=lufs_strike,
        sustain_lufs=lufs_sustain,
    )
    store.save()
    touch_note = ""
    if lufs_light is not None:
        touch_note = f", light {lufs_light:.1f} LUFS"
    print(
        f"  [ok] {name}: strike {lufs_strike:.1f} / sustain {lufs_sustain:.1f} LUFS, "
        f"peak {true_peak:.1f} dBTP -> gain {gain_db:+.2f} dB{touch_note}"
    )
    return CalibrateResult(
        ok=True,
        lufs_light=lufs_light,
        lufs_strike=lufs_strike,
        lufs_sustain=lufs_sustain,
    )


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
    pressure_path = args.pressure_output or default_pressure_path()
    touch_cal = not args.no_touch_cal
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
        est = estimate_calibration_duration_seconds(len(targets))
        print(
            f"Estimated time: ~{est / 60:.1f} min "
            f"({CALIBRATION_SECONDS_PER_PATCH_ESTIMATE:.0f}s per patch, typical Pi loopback run)"
        )
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
    light_measurements: list[tuple[str, float, float, float]] = []
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
                result = calibrate_patch(
                    path,
                    loader,
                    store,
                    audio_device=audio_device,
                    mock_lufs=args.mock_lufs,
                    dry_run=False,
                    midi_out=midi_out,
                    touch_cal=touch_cal,
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
                    "ok": result.ok,
                },
            )
            if result.ok:
                updated += 1
                if (
                    result.lufs_light is not None
                    and result.lufs_strike is not None
                    and result.lufs_sustain is not None
                ):
                    light_measurements.append(
                        (name, result.lufs_light, result.lufs_strike, result.lufs_sustain)
                    )
        if light_measurements and touch_cal and not _interrupted:
            target_lufs = resolve_light_touch_target([v for _, v, _, _ in light_measurements])
            pressure_store = PatchPressureStore(pressure_path)
            touch_updated = 0
            for name, lufs_light, lufs_strike, lufs_sustain in light_measurements:
                floor = compute_touch_calibration_floor(
                    lufs_light, target_lufs, lufs_strike, lufs_sustain
                )
                pressure_store.set_calibration(name, floor, lufs_light)
                touch_updated += 1
            pressure_store.save()
            print(
                f"Wrote {touch_updated} touch calibration entries to {pressure_path} "
                f"(light target {target_lufs:.1f} LUFS)",
                file=sys.stderr if args.progress_json else sys.stdout,
            )
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
