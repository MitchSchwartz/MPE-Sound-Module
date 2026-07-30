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
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

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
)

OSC_HOST = "127.0.0.1"
OSC_PORT = 53280
GESTURE_SECONDS = 3.0
NOTE = 60
MPE_CHANNEL = 2  # Surge MPE: channel 2 = first note channel
STRIKE_VELOCITY = 96
DEFAULT_PI_CAPTURE = "plughw:3,0"


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
    return parser.parse_args()


def favorites_folder_on_disk(parent: Path) -> Path:
    return parent / FAVORITES_NAME.lstrip("!")


def collect_patch_paths(args: argparse.Namespace) -> list[Path]:
    scanner = PatchScanner(SURGE_PATCH_DIRS)
    scanner.scan_patches()

    paths: list[Path] = []
    if args.favorites_only:
        user_patches_dir = Path.home() / "Documents" / "Surge XT" / "Patches"
        fav_dir = favorites_folder_on_disk(user_patches_dir)
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
    return [by_name[k] for k in sorted(by_name)]


def find_surge_midi_port() -> int | None:
    try:
        import rtmidi
    except ImportError:
        print("Error: python-rtmidi required for live calibration", file=sys.stderr)
        return None

    midi_out = rtmidi.MidiOut()
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
        print(
            f"Using MIDI Through port {port!r} ({ports[port]}) — ensure Surge listens on Through",
            file=sys.stderr,
        )
        return port

    print(f"Available MIDI ports: {ports}", file=sys.stderr)
    return None


def detect_capture_device(explicit: str | None) -> str:
    """Resolve ALSA capture device for Surge output monitoring."""
    if explicit:
        return explicit

    detect_script = REPO_ROOT / "scripts" / "detect-audio-device.sh"
    surge_cli = Path.home() / "surge" / "build" / "surge_xt_products" / "surge-xt-cli"
    if detect_script.is_file() and surge_cli.is_file():
        try:
            result = subprocess.run(
                [str(detect_script), str(surge_cli)],
                capture_output=True,
                text=True,
                check=False,
            )
            for line in result.stdout.splitlines():
                if line.startswith("DEVICE_ID="):
                    device_id = line.split("=", 1)[1].strip()
                    if device_id:
                        card, dev = device_id.split(".", 1)
                        capture = f"plughw:{card},{dev}"
                        print(f"Auto-detected capture device: {capture}", file=sys.stderr)
                        return capture
        except (OSError, ValueError) as exc:
            print(f"Warning: audio auto-detect failed: {exc}", file=sys.stderr)

    # Common Pi + Sound Blaster Play! 3 card index when detect script unavailable.
    print(f"Using default Pi capture device: {DEFAULT_PI_CAPTURE}", file=sys.stderr)
    return DEFAULT_PI_CAPTURE


def send_performance_gesture(port_index: int, pre_roll: float = 0.25) -> None:
    import rtmidi

    midi_out = rtmidi.MidiOut()
    midi_out.open_port(port_index)

    time.sleep(pre_roll)

    ch = MPE_CHANNEL - 1
    note_on = 0x90 | ch
    note_off = 0x80 | ch
    pressure_cc = 0xE0 | ch

    midi_out.send_message([note_on, NOTE, STRIKE_VELOCITY])

    steps = 24
    hold_seconds = 1.8
    step_sleep = hold_seconds / steps
    for step in range(steps + 1):
        pressure = int(127 * step / steps)
        midi_out.send_message([pressure_cc, pressure & 0x7F, (pressure >> 7) & 0x7F])
        time.sleep(step_sleep)

    time.sleep(0.15)
    midi_out.send_message([note_off, NOTE, 0])
    time.sleep(0.2)


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

        port = find_surge_midi_port()
        if port is None:
            return False

        # Unity gain for measurement — stored calibration must not skew capture.
        loader.user_volume_trim = 1.0
        loader._patch_gain_linear = 1.0
        loader._send_combined_volume()
        time.sleep(0.2)

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            wav = Path(tmp.name)
        try:
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
            send_performance_gesture(port)
            capture.wait()
            if capture.returncode != 0:
                print(f"  [fail] ffmpeg capture: {name}", file=sys.stderr)
                return False
            lufs, true_peak = measure_lufs(wav)
        finally:
            wav.unlink(missing_ok=True)

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
    print(f"  [ok] {name}: {lufs:.1f} LUFS, peak {true_peak:.1f} dBTP -> gain {gain_db:+.2f} dB")
    return True


def main() -> int:
    args = parse_args()
    output_path = args.output or default_normalization_path()
    audio_device = detect_capture_device(args.audio_device)

    patch_paths = collect_patch_paths(args)
    if args.limit > 0:
        patch_paths = patch_paths[: args.limit]

    if not patch_paths:
        print("No patches matched the selection.", file=sys.stderr)
        return 1

    store = PatchNormalizationStore(output_path)
    if args.force or args.mock_lufs is not None:
        targets = patch_paths
    else:
        missing = store.list_missing([p.stem for p in patch_paths])
        targets = [p for p in patch_paths if p.stem in missing]

    print(f"Output: {output_path}")
    print(f"Capture device: {audio_device}")
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

    if args.dry_run:
        for path in targets:
            calibrate_patch(
                path, None, store, audio_device=audio_device, mock_lufs=None, dry_run=True
            )
        est = len(targets) * (GESTURE_SECONDS + 1.5)
        print(f"Estimated time: ~{est / 60:.1f} min at {GESTURE_SECONDS}s capture per patch")
        print(f"Starter file in repo: {repo_starter_path()}")
        return 0

    loader = PatchLoader(osc_host=args.osc_host, osc_port=args.osc_port)
    if not loader.osc_enabled:
        print("Error: OSC client unavailable — is Surge running on the OSC port?", file=sys.stderr)
        return 1

    updated = 0
    for index, path in enumerate(targets, start=1):
        print(f"[{index}/{len(targets)}] {path.stem}")
        if calibrate_patch(
            path,
            loader,
            store,
            audio_device=audio_device,
            mock_lufs=args.mock_lufs,
            dry_run=False,
        ):
            updated += 1
            store.save()

    if updated:
        store.save()
        print(f"Wrote {updated} calibration entries to {output_path}")
    else:
        print("No entries updated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
