#!/usr/bin/env python3
"""
Offline per-patch loudness calibration for Surge XT patches.

Renders a fixed MPE performance gesture per patch, measures integrated LUFS via
ffmpeg loudnorm, and writes gain offsets to patch_normalization.json.

Surge is measured alone, at the patch's authored volume, outside mpe-fx-host:
the stage gain is linear, so the gain is applied arithmetically (LUFS and true
peak move by exactly gain_db) and the entry is marked CAL_PATH. A capture that
clips is measured again with CAPTURE_PAD_DB taken off Surge's master volume
(`set_capture_volume`, the one place this tool sets a Surge volume).
`--controls PATCH` runs the instrument controls first: a known pad must read as
that pad, a repeat must read the same, and muting the stage must change nothing.

Requires: Surge XT CLI running with OSC on port 53280, ffmpeg, python-osc,
python-rtmidi (for live calibration). Use --dry-run to list targets without
Surge/ffmpeg.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import os
import re
import signal
import statistics
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
    calibration_audio_scope,
    refresh_maintenance_flag,
    unload_snd_aloop_if_idle,
)
from patch_browser.patch_normalization import (  # noqa: E402
    CAL_PATH,
    CAPTURE_PAD_DB,
    CLIP_SAMPLE_PEAK_DBFS,
    NORM_TRIM_DB_MIN,
    POST_GAIN_VERIFY_PEAK_MAX_DBTP,
    PatchNormalizationStore,
    post_gain_verify_passes,
    SAFE_PEAK_DBTP,
    compute_gain_db,
    compute_gain_db_dual_anchor,
    default_normalization_path,
    padded_master_volume,
    plausible_authored_volume,
    repo_starter_path,
    stage_db,
)
from patch_browser import calibration_controls  # noqa: E402
from patch_browser.patch_pressure import (  # noqa: E402
    PatchPressureStore,
    LIGHT_TOUCH_GESTURE_SECONDS,
    LIGHT_TOUCH_HOLD_SECONDS,
    LIGHT_TOUCH_PRESSURE,
    compute_touch_calibration_floor,
    default_pressure_path,
    resolve_light_touch_target,
)
from patch_browser.patch_loader import OSC_OUT_PORT, PatchLoader
from patch_browser.patch_sidecar_key import (
    resolve_storage_key,
    stable_key_from_absolute_path,
)
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
# Surge's master volume: read by `read_master_volume`, written only by
# `set_capture_volume`. After the patch's effects, so a pad there is exact.
MASTER_VOLUME_PATH = "/param/global/volume"
CONTROL_PAD_DB = 6.0
# The isolation control's mute is believed only once mpe-fx-host has written it
# to its state file (fx_host_protocol.read_gain_state): polled this often, this
# many times. A mute that reached no insert proves nothing.
ISOLATION_ACK_POLL_S = 0.02
ISOLATION_ACK_POLLS = 25
# When the stage is already muted, the isolation mute is this instead (-120 dB),
# so the insert has a new value to write and the acknowledgement can be seen.
ISOLATION_PROBE_GAIN = 1e-6
# A row whose heard level rises by more than this on recalibration is marked
# LOUDER in the report.
LEVEL_REPORT_LOUDER_DB = 0.5
STORE_BACKUP_DIR = Path.home() / ".patch_browser_calibration_backups"
DEFAULT_PI_CAPTURE = "plughw:1,0"
MIN_VALID_LUFS = -39.0  # legacy reference only — validity uses peak floor below
MIN_VALID_TRUE_PEAK_DBTP = -45.0  # reject captures with no audible Surge output
PATCH_LOAD_SETTLE_SECONDS = 0.75
# A measurement starts only once Surge reports the patch's own path as loaded.
# Large wavetable patches take a while on the Pi; past this the load has failed.
PATCH_LOAD_CONFIRM_TIMEOUT_S = 10.0
# Loads failing back to back mean Surge is not loading anything: stop the run.
LOAD_FAILURES_TO_HALT = 3
MEASURE_RETRY_INTERVAL_SECONDS = 3.0
MEASURE_MAX_ATTEMPTS = len(GESTURE_DURATIONS_SECONDS)  # ~0, 3, 8, 16s within ~27s total

_interrupted = False
FAILURE_REPORT_PATH = Path("/tmp/calibration-last-failure.json")


@dataclass
class CalibrateResult:
    ok: bool
    verify_failed: bool = False
    load_failed: bool = False
    lufs_light: float | None = None
    lufs_strike: float | None = None
    lufs_sustain: float | None = None
    trim_migration: dict | None = None
    # The norm part of the stage (dB re authored) before and after this
    # calibration was written: what the instrument plays this patch at.
    level_before_db: float | None = None
    level_after_db: float | None = None


@dataclass
class VerifyResult:
    ok: bool
    skipped: bool = False
    peak_dbtp: float | None = None
    reason: str = ""


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
        "--audit",
        nargs=2,
        type=Path,
        metavar=("PATCH", "WAV"),
        default=None,
        help="Stage audit: judge WAV, the live mpe-fx-host output at depth 0 while the "
        "sustain anchor gesture played PATCH, against the stored calibration. "
        "Touches nothing; exit 0 on pass.",
    )
    parser.add_argument(
        "--audit-headroom-db",
        type=float,
        default=0.0,
        help="Device mode's stage headroom during the --audit capture (0 in trim mode)",
    )
    parser.add_argument(
        "--controls",
        type=Path,
        metavar="PATCH",
        default=None,
        help="Run the instrument controls on this reference patch (pad, repeat, stage "
        "isolation) and exit; 0 only if all pass. Run before a full calibration.",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Audit calibrated patches in scope: replay gesture at stored gain_db only",
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
        help="Max patches to process, counted after the missing/--force filter (0 = all)",
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
        "--touch-target-lufs",
        type=float,
        default=None,
        help=(
            "Fixed light-touch target: write each patch's Touch floor as soon as it is "
            "measured, so an interrupted run keeps them (default: cohort median, written "
            "at the end of the run)"
        ),
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


# A run whose readings stop depending on the patch is measuring something
# else: a patch load that no longer takes, or a note that never released.
# The last PLATEAU_PATCHES distinct patches all reading within PLATEAU_DB of
# their own median, on both anchors, halt the run and roll those entries back.
# The median, not the first reading: measured against the first, one jittery
# reading 0.32 dB off it restarted the count and a 5-patch plateau got through.
PLATEAU_PATCHES = 4
PLATEAU_DB = 0.3


class PlateauGuard:
    def __init__(self, patches: int = PLATEAU_PATCHES, tolerance_db: float = PLATEAU_DB) -> None:
        self.patches = patches
        self.tolerance_db = tolerance_db
        self._recent: list[tuple[Path, float, float]] = []

    def _flat(self, values: list[float]) -> bool:
        centre = statistics.median(values)
        return all(abs(v - centre) <= self.tolerance_db for v in values)

    def add(self, path: Path, strike: float, sustain: float) -> list[Path] | None:
        """Record one reading; the plateau's patches once it is long enough to halt."""
        self._recent = (self._recent + [(path, strike, sustain)])[-self.patches :]
        if len(self._recent) < self.patches:
            return None
        if self._flat([s for _p, s, _u in self._recent]) and self._flat(
            [u for _p, _s, u in self._recent]
        ):
            return [p for p, _s, _u in self._recent]
        return None


def roll_back_entries(store_path: Path, snapshot: dict, keys: list[str]) -> None:
    """Put each key back to its run-start entry, or remove it if it had none."""
    data = json.loads(store_path.read_text()) if store_path.exists() else {}
    for key in keys:
        if key in snapshot:
            data[key] = snapshot[key]
        else:
            data.pop(key, None)
    tmp = store_path.with_suffix(store_path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(store_path)


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


def collect_patch_paths(args: argparse.Namespace) -> list[Path]:
    scanner = PatchScanner(SURGE_PATCH_DIRS)
    scanner.scan_patches()

    paths: list[Path] = []
    if args.favorites_only:
        # Favorites are pointers: calibrate the patches the index names, nested
        # folders included. A missing source has no file to measure.
        with scanner.scan_lock:
            by_key = dict(scanner.patches_by_stable_key)
        paths = sorted(
            Path(by_key[key]["path"])
            for key in scanner.favorites_index.entries
            if key in by_key
        )
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
                    if patch.get("favorite_view"):
                        continue  # a view of a patch listed under its own category
                    paths.append(Path(patch["path"]))

    result = dedupe_by_patch_key(paths, SURGE_PATCH_DIRS)

    if args.patch:
        needle = args.patch.strip()
        result = [p for p in result if p.stem == needle or needle.lower() in p.stem.lower()]
        if not result:
            print(f"Warning: no patches matched --patch {needle!r}", file=sys.stderr)

    return result


def patch_target_key(path: Path, patch_dirs) -> str:
    """The key a calibration of ``path`` is stored under: its stable key.

    A path outside every root has no stable key; its resolved path stands in, so
    two such files never collapse just because their names match.
    """
    key = stable_key_from_absolute_path(path, patch_dirs)
    return key if key else str(path.resolve())


def dedupe_by_patch_key(paths: list[Path], patch_dirs) -> list[Path]:
    """One target per stable key, sorted by name.

    Same-named patches in different roots or folders (factory ``Keys/Church`` and
    a third-party ``Church``) are different patches with different store entries,
    so both stay.
    """
    by_key: dict[str, Path] = {}
    for path in paths:
        by_key.setdefault(patch_target_key(path, patch_dirs), path)
    return sorted(by_key.values(), key=lambda p: (p.stem, patch_target_key(p, patch_dirs)))


def select_targets(
    patch_paths: list[Path],
    store: PatchNormalizationStore,
    *,
    verify_only: bool,
    force: bool,
    limit: int,
    patch_dirs=SURGE_PATCH_DIRS,
) -> list[Path]:
    """Patches this run will measure, decided per stable key, then capped.

    ``store`` must have ``patch_dirs`` set too, so each lookup is by the stable
    key derived from the path. ``limit`` counts targets after the filter, so a pilot
    of N on a mostly calibrated library still measures N patches.
    """
    if verify_only:
        targets = [
            p
            for p in patch_paths
            if store.get_calibrated_gain_db(p.stem, patch_path=str(p)) is not None
        ]
    elif force:
        targets = list(patch_paths)
    else:
        missing_keys = set(store.list_missing(patch_path_records(patch_paths)))
        targets = [
            p
            for p in patch_paths
            if resolve_storage_key(p.stem, patch_path=str(p), patch_dirs=patch_dirs)
            in missing_keys
        ]
    if limit > 0:
        targets = targets[:limit]
    return targets


def patch_path_records(paths: list[Path]) -> list[dict]:
    """Shape expected by PatchNormalizationStore.list_missing."""
    return [{"name": p.stem, "path": str(p)} for p in paths]


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
                # Replies to /q/patch, which confirms each load (PatchLoader).
                f"--osc-out-port={OSC_OUT_PORT}",
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
                # Replies to /q/patch, which confirms each load (PatchLoader).
                f"--osc-out-port={OSC_OUT_PORT}",
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


def pressure_message(ch: int, value: int) -> list[int]:
    """Channel pressure (0xD0), Surge's per-note pressure on an MPE member channel.
    Until 2026-10-22 the gestures sent 0xE0 here, which is pitch bend: with the
    48-semitone bend range it played the anchor note about two octaves low and
    never pressed, so every calibration measured the wrong pitch."""
    return [0xD0 | ch, max(0, min(127, int(value)))]


def bend_centre_message(ch: int) -> list[int]:
    """Pitch bend at centre (8192). Surge keeps bend per member channel, so a
    gesture starts from it rather than from whatever the channel last had."""
    return [0xE0 | ch, 0x00, 0x40]


def send_performance_gesture(
    midi_out: object, pre_roll: float = 0.25, hold_seconds: float = 1.8
) -> None:
    time.sleep(pre_roll)

    ch = MPE_CHANNEL - 1
    note_on = 0x90 | ch
    note_off = 0x80 | ch

    midi_out.send_message(bend_centre_message(ch))  # type: ignore[union-attr]
    midi_out.send_message([note_on, NOTE, STRIKE_VELOCITY])  # type: ignore[union-attr]

    steps = 24
    step_sleep = hold_seconds / steps
    for step in range(steps + 1):
        pressure = int(127 * step / steps)
        midi_out.send_message(pressure_message(ch, pressure))  # type: ignore[union-attr]
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

    midi_out.send_message(bend_centre_message(ch))  # type: ignore[union-attr]
    midi_out.send_message([note_on, NOTE, STRIKE_VELOCITY])  # type: ignore[union-attr]
    midi_out.send_message(pressure_message(ch, LIGHT_TOUCH_PRESSURE))  # type: ignore[union-attr]
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
    vel = max(1, min(127, int(velocity)))
    midi_out.send_message(bend_centre_message(ch))  # type: ignore[union-attr]
    midi_out.send_message([note_on, NOTE, vel])  # type: ignore[union-attr]
    midi_out.send_message(pressure_message(ch, pressure))  # type: ignore[union-attr]
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


class Anchor(tuple):
    """(lufs, true_peak_dbtp), with the capture's sample peak as `.sample_peak_dbfs`
    (None when it could not be read)."""

    def __new__(cls, lufs: float, true_peak: float, sample_peak_dbfs: float | None = None):
        reading = super().__new__(cls, (lufs, true_peak))
        reading.sample_peak_dbfs = sample_peak_dbfs
        return reading


def wav_sample_peak_dbfs(wav_path: Path) -> float | None:
    """Largest sample magnitude in a 16-bit PCM WAV, in dBFS; None if unreadable."""
    import array
    import wave

    try:
        with wave.open(str(wav_path), "rb") as wav:
            if wav.getsampwidth() != 2:
                return None
            samples = array.array("h", wav.readframes(wav.getnframes()))
    except (OSError, EOFError, wave.Error):
        return None
    if sys.byteorder == "big":
        samples.byteswap()
    peak = max((abs(v) for v in samples), default=0)
    return 20.0 * math.log10(peak / 32768.0) if peak else -math.inf


def is_clipped(anchor) -> bool:
    """A capture at or past CLIP_SAMPLE_PEAK_DBFS. Without a sample peak, the true
    peak stands in: it is never below the sample peak, so this errs toward a pad."""
    peak = getattr(anchor, "sample_peak_dbfs", None)
    if peak is None:
        peak = anchor[1]
    return peak >= CLIP_SAMPLE_PEAK_DBFS


def measure_anchor_lufs(
    midi_out: object,
    audio_device: str,
    *,
    velocity: int,
    pressure: int,
    gesture_seconds: float = ANCHOR_GESTURE_SECONDS,
    hold_seconds: float = ANCHOR_HOLD_SECONDS,
) -> Anchor | None:
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
        sample_peak = wav_sample_peak_dbfs(wav)
    except RuntimeError:
        return None
    finally:
        if wav is not None:
            wav.unlink(missing_ok=True)
    if is_invalid_measurement(lufs, true_peak):
        return None
    return Anchor(lufs, true_peak, sample_peak)


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


def read_master_volume(loader: PatchLoader) -> float | None:
    """The loaded patch's master volume (0..1), or None when Surge does not say."""
    value = loader._query_osc_float(MASTER_VOLUME_PATH)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        return None
    return float(value)


def set_capture_volume(loader: PatchLoader, volume: float) -> bool:
    """Set Surge's master volume for a capture: the pad, and putting the authored
    value back after it. The only Surge volume write in the product
    (tests/test_stage_gain_guard.py), here because the measured Surge is the
    standalone one, outside the stage. Callers put the authored value back in a
    ``finally``, and a patch load resets it anyway (it is part of the patch)."""
    if not loader.osc_enabled or loader.osc_client is None:
        return False
    loader.osc_client.send_message(MASTER_VOLUME_PATH, max(0.0, min(1.0, float(volume))))
    return True


@dataclass
class UnityMeasurement:
    """Both anchors at the authored volume, with any capture pad added back."""

    strike: tuple[float, float]
    sustain: tuple[float, float]
    padded_db: float = 0.0


def measure_unity_anchors(
    loader: PatchLoader,
    midi_out: object,
    audio_device: str,
    master_volume: float | None,
    *,
    name: str = "",
) -> UnityMeasurement | None:
    """Strike and sustain at authored volume. A clipped capture is re-measured with
    CAPTURE_PAD_DB off Surge's master volume (``master_volume``, the patch's own),
    and the pad added back."""
    strike = measure_strike_anchor_lufs(midi_out, audio_device)
    if strike is None:
        print(f"  [fail] {name}: strike anchor inaudible", file=sys.stderr)
        return None
    sustain = measure_sustain_anchor_lufs(midi_out, audio_device)
    if sustain is None:
        print(f"  [fail] {name}: sustain anchor inaudible", file=sys.stderr)
        return None
    if not (is_clipped(strike) or is_clipped(sustain)):
        return UnityMeasurement(tuple(strike), tuple(sustain))
    if master_volume is None:
        print(f"  [fail] {name}: capture clipped and Surge did not report its master "
              f"volume (no pad possible)", file=sys.stderr)
        return None
    pad = CAPTURE_PAD_DB
    padded = padded_master_volume(master_volume, pad)
    if padded is None:
        print(f"  [fail] {name}: capture clipped and the master volume is too low "
              f"to take {pad:.0f} dB off", file=sys.stderr)
        return None
    print(f"  [pad] {name}: capture clipped, measuring again {pad:.0f} dB down")
    set_capture_volume(loader, padded)
    try:
        time.sleep(0.35)
        strike = measure_strike_anchor_lufs(midi_out, audio_device)
        sustain = measure_sustain_anchor_lufs(midi_out, audio_device)
    finally:
        set_capture_volume(loader, master_volume)
    if strike is None or sustain is None:
        print(f"  [fail] {name}: padded capture inaudible", file=sys.stderr)
        return None
    if is_clipped(strike) or is_clipped(sustain):
        print(f"  [fail] {name}: capture still clipped {pad:.0f} dB down", file=sys.stderr)
        return None
    return UnityMeasurement(
        (strike[0] + pad, strike[1] + pad), (sustain[0] + pad, sustain[1] + pad), pad
    )


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


def load_patch_confirmed(loader: PatchLoader, patch_path: Path) -> bool:
    """Load by full path; True only once Surge reports that same path loaded."""
    return bool(
        loader.load_patch(
            str(patch_path),
            apply_normalization=False,
            capture_hold=False,
            confirm_timeout_s=PATCH_LOAD_CONFIRM_TIMEOUT_S,
        )
    )


def verify_patch(
    patch_path: Path,
    loader: PatchLoader,
    store: PatchNormalizationStore,
    *,
    audio_device: str,
    midi_out: object,
) -> VerifyResult:
    name = patch_path.stem
    gain_db = store.get_calibrated_gain_db(name, patch_path=str(patch_path))
    if gain_db is None:
        return VerifyResult(ok=False, skipped=True, reason="not calibrated")
    if not load_patch_confirmed(loader, patch_path):
        print(f"  [fail] load not confirmed: {patch_path}", file=sys.stderr)
        return VerifyResult(ok=False, reason="load not confirmed")
    time.sleep(PATCH_LOAD_SETTLE_SECONDS)
    unity = measure_unity_anchors(
        loader, midi_out, audio_device, read_master_volume(loader), name=name
    )
    if unity is None:
        return VerifyResult(ok=False, reason="verify capture inaudible")
    # The stage is linear: the stored gain moves the peak by exactly gain_db.
    peak = max(unity.strike[1], unity.sustain[1]) + gain_db
    ok = post_gain_verify_passes(peak)
    return VerifyResult(ok=ok, peak_dbtp=peak)


def write_touch_calibrations(
    pressure_path: Path,
    measurements: list[tuple[str, float, float, float, Path]],
    patch_dirs,
    target_lufs: float | None = None,
) -> tuple[float, int]:
    """Write each measured patch's Touch floor under the key the browser reads.

    The key comes from the patch's path. A name alone resolves to the bare stem,
    which the browser only falls back to, so a patch with a keyed entry would get
    a second, never-read entry and keep its old floor.

    target_lufs None takes the cohort median of ``measurements``.
    """
    if target_lufs is None:
        target_lufs = resolve_light_touch_target([m[1] for m in measurements])
    pressure_store = PatchPressureStore(pressure_path)
    pressure_store.set_patch_dirs(patch_dirs)
    for name, lufs_light, lufs_strike, lufs_sustain, path in measurements:
        floor = compute_touch_calibration_floor(
            lufs_light, target_lufs, lufs_strike, lufs_sustain
        )
        pressure_store.set_calibration(name, floor, lufs_light, patch_path=str(path))
    pressure_store.save()
    return target_lufs, len(measurements)


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
    cal_route: str = "soundblaster",
) -> CalibrateResult:
    name = patch_path.stem
    if dry_run:
        calibrated = store.get_calibrated_gain_db(name, patch_path=str(patch_path)) is not None
        status = "skip (has entry)" if calibrated else "would calibrate"
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
        if not load_patch_confirmed(loader, patch_path):
            # Nothing is measured: the patch playing may not be this one.
            print(f"  [fail] load not confirmed: {patch_path}", file=sys.stderr)
            return CalibrateResult(ok=False, load_failed=True)

        if midi_out is None:
            return CalibrateResult(ok=False)

        # Surge plays the patch at its authored volume: nothing here sets it.
        authored_volume = loader._authored_volume
        authored_volume_b = plausible_authored_volume(getattr(loader, "_authored_volume_b", None))
        time.sleep(PATCH_LOAD_SETTLE_SECONDS)

        unity = measure_unity_anchors(
            loader, midi_out, audio_device, read_master_volume(loader), name=name
        )
        if unity is None:
            return CalibrateResult(ok=False)
        lufs_strike, peak_strike = unity.strike
        lufs_sustain, peak_sustain = unity.sustain
        capture_padded_db = unity.padded_db

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

    if mock_lufs is not None:
        print(
            f"  [fail] {name}: --mock-lufs cannot save cal entries (no capture)",
            file=sys.stderr,
        )
        return CalibrateResult(ok=False)
    # The stage is linear, so the peak it will play at is the unity peak + gain.
    post_gain_peak = true_peak + gain_db
    if not post_gain_verify_passes(post_gain_peak):
        print(
            f"  [fail] {name}: post-gain peak {post_gain_peak:.1f} dBTP "
            f"exceeds {POST_GAIN_VERIFY_PEAK_MAX_DBTP:.1f} — not saved",
            file=sys.stderr,
        )
        return CalibrateResult(ok=False, verify_failed=True)

    def heard() -> float:
        norm_db, ceiling_db = store.stage_norm(
            name, patch_path=str(patch_path), authored_volume=authored_volume,
            authored_volume_b=authored_volume_b,
        )
        return stage_db(norm_db=norm_db, ceiling_db=ceiling_db, headroom_db=0.0, vol_db=0.0)

    level_before = heard()
    migration = store.set_calibration(
        name,
        gain_db,
        lufs,
        true_peak_dbtp=true_peak,
        post_gain_peak_dbtp=post_gain_peak,
        cal_route=cal_route,
        strike_lufs=lufs_strike,
        sustain_lufs=lufs_sustain,
        cal_path=CAL_PATH,
        lufs_unity={"strike": lufs_strike, "sustain": lufs_sustain},
        true_peak_unity_dbtp={"strike": peak_strike, "sustain": peak_sustain},
        authored_volume=authored_volume,
        authored_volume_b=authored_volume_b,
        capture_padded_db=capture_padded_db,
        patch_path=str(patch_path),
    )
    store.save()
    level_after = heard()
    print(level_report_line(name, level_before, level_after))
    if migration is not None:
        clamp_note = " (CLAMPED: raise held to the fader's top, plays quieter)" if migration["clamped"] else ""
        below = " (below the Norm fader: it reads min)" if migration["new"] < NORM_TRIM_DB_MIN else ""
        print(f"  [trim] {name}: {migration['old']:+.1f} -> {migration['new']:+.1f} dB{clamp_note}{below}")
    touch_note = ""
    if lufs_light is not None:
        touch_note = f", light {lufs_light:.1f} LUFS"
    post_note = ""
    if post_gain_peak is not None:
        post_note = f", post-gain {post_gain_peak:.1f} dBTP"
    print(
        f"  [ok] {name}: strike {lufs_strike:.1f} / sustain {lufs_sustain:.1f} LUFS, "
        f"peak {true_peak:.1f} dBTP -> gain {gain_db:+.2f} dB{post_note}{touch_note}"
    )
    return CalibrateResult(
        ok=True,
        lufs_light=lufs_light,
        lufs_strike=lufs_strike,
        lufs_sustain=lufs_sustain,
        trim_migration=migration,
        level_before_db=level_before,
        level_after_db=level_after,
    )


def level_report_line(name: str, before_db: float, after_db: float) -> str:
    """One line per calibrated row: the level it played at before and after, dB re
    the patch as authored, so the owner sees every patch that gets louder."""
    delta = after_db - before_db
    flag = "  LOUDER" if delta > LEVEL_REPORT_LOUDER_DB else ""
    return f"  [level] {name}: {before_db:+.1f} -> {after_db:+.1f} dB re authored ({delta:+.1f}){flag}"


def run_controls(
    loader: PatchLoader,
    midi_out: object,
    audio_device: str,
    patch_path: Path,
    *,
    stage_sender=None,
    gain_state=None,
) -> list[calibration_controls.ControlResult]:
    """The instrument controls on one reference patch, in the order to trust them.

    Positive: CONTROL_PAD_DB taken off Surge's master volume reads as that pad.
    Repeatability: the same capture twice reads the same. Isolation: the stage gain
    muted during a capture changes nothing, so the stage is outside this path.

    The mute is the safe direction for the live trunk. It counts only once
    mpe-fx-host has taken it (its state file shows it): with no insert running the
    capture would match for the wrong reason, so the control fails. The gain the
    insert had before is put back in a ``finally``, held to unity (a boost was for
    the touch UI's patch, not the control patch Surge now holds); with none on
    record the stage is left muted. Either way the touch UI's next send restores
    the level.
    """
    from patch_browser import fx_host_protocol
    from patch_browser.patch_loader import UdpStageSender

    read_state = gain_state or fx_host_protocol.read_gain_state

    if not load_patch_confirmed(loader, patch_path):
        raise RuntimeError(f"Surge did not confirm the control patch loaded: {patch_path}")
    authored = read_master_volume(loader)
    if authored is None:
        raise RuntimeError("Surge did not report the control patch's master volume")
    padded_master = padded_master_volume(authored, CONTROL_PAD_DB)
    if padded_master is None:
        raise RuntimeError("the control patch's master volume is too low to pad")
    time.sleep(PATCH_LOAD_SETTLE_SECONDS)

    def sustain() -> tuple[float, float]:
        reading = measure_sustain_anchor_lufs(midi_out, audio_device)
        if reading is None:
            raise RuntimeError("control capture inaudible")
        if is_clipped(reading):
            raise RuntimeError("control capture clipped: choose a quieter control patch")
        return reading[0], reading[1]

    first = sustain()
    set_capture_volume(loader, padded_master)
    try:
        time.sleep(0.35)
        padded = sustain()
    finally:
        set_capture_volume(loader, authored)
    time.sleep(0.35)
    second = sustain()
    send = stage_sender or UdpStageSender()
    before = read_state()
    mute = ISOLATION_PROBE_GAIN if before == 0.0 else 0.0
    send(fx_host_protocol.encode_gain(mute))
    try:
        if stage_took(read_state, mute):
            isolation = calibration_controls.isolation_control(second, sustain())
        else:
            isolation = calibration_controls.ControlResult(
                "isolation", False,
                "mpe-fx-host did not take the mute (is it running?): the capture "
                "cannot show the stage is outside the calibration path",
            )
    finally:
        if before is not None:
            # Held to unity: the gain on record may be a boost for the patch the
            # touch UI had loaded, and Surge now holds the control patch. A cut
            # goes back as it was; a boost waits for the UI's next send.
            send(fx_host_protocol.encode_gain(min(before, 1.0)))
        else:
            print("  [isolation] no earlier stage gain on record: the stage stays muted "
                  "until the touch UI sends its level")
    return [
        calibration_controls.positive_control(first, padded, pad_db=CONTROL_PAD_DB),
        calibration_controls.repeatability_control(first, second),
        isolation,
    ]


def stage_took(read_state, value: float) -> bool:
    """True once mpe-fx-host's state file shows `value`: the insert is running and
    applied it. Polled ISOLATION_ACK_POLLS times, ISOLATION_ACK_POLL_S apart."""
    for _ in range(ISOLATION_ACK_POLLS):
        now = read_state()
        if now is not None and abs(now - value) < 1e-9:
            return True
        time.sleep(ISOLATION_ACK_POLL_S)
    return False


def audit_patch(
    store: PatchNormalizationStore,
    patch_path: Path,
    wav: Path,
    *,
    headroom_db: float = 0.0,
) -> calibration_controls.ControlResult:
    """The stage audit for one capture: predicted = the calibration's unity sustain
    LUFS + the stage gain the loader applies to it at full fader."""
    from patch_browser.patch_normalization import stage_db

    name = patch_path.stem
    entry = store.get_entry(name, patch_path=str(patch_path))
    lufs_unity = (entry or {}).get("lufs_unity") or {}
    if store.get_calibrated_gain_db(name, patch_path=str(patch_path)) is None or "sustain" not in lufs_unity:
        return calibration_controls.ControlResult("stage audit", False, f"{name}: not calibrated for the stage")
    norm_db, ceiling_db = store.stage_norm(
        name, patch_path=str(patch_path), authored_volume=entry.get("authored_volume")
    )
    predicted = float(lufs_unity["sustain"]) + stage_db(
        norm_db=norm_db, ceiling_db=ceiling_db, headroom_db=headroom_db, vol_db=0.0
    )
    return calibration_controls.stage_audit(predicted, measure_lufs(wav))


def backup_store(*paths: Path, root: Path = STORE_BACKUP_DIR) -> Path | None:
    """Copy the stores into root/<timestamp>/ before a run writes them."""
    import shutil

    existing = [p for p in paths if p.is_file()]
    if not existing:
        return None
    dest = root / datetime.now().strftime("%Y%m%d-%H%M%S")
    dest.mkdir(parents=True, exist_ok=True)
    for path in existing:
        shutil.copy2(path, dest / path.name)
    return dest


def main() -> int:
    args = parse_args()
    output_path = args.output or default_normalization_path()
    if args.audit is not None:
        audit_store = PatchNormalizationStore(output_path)
        audit_store.set_patch_dirs(SURGE_PATCH_DIRS)
        result = audit_patch(
            audit_store, args.audit[0], args.audit[1], headroom_db=args.audit_headroom_db
        )
        print(f"  [{'pass' if result.ok else 'FAIL'}] {args.audit[0].stem}: {result.detail}")
        return 0 if result.ok else 1
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

    if not patch_paths and args.controls is None:
        msg = "No patches matched the selection."
        print(msg, file=sys.stderr)
        emit_progress(args, {"type": "error", "message": msg})
        emit_progress(args, {"type": "done", "updated": 0, "exit_code": 1})
        return 1

    store = PatchNormalizationStore(output_path)
    store.set_patch_dirs(SURGE_PATCH_DIRS)
    pressure_path = args.pressure_output or default_pressure_path()
    touch_cal = not args.no_touch_cal
    norm_snapshot = json.loads(output_path.read_text()) if output_path.exists() else {}
    pressure_snapshot = json.loads(pressure_path.read_text()) if pressure_path.exists() else {}
    plateau = PlateauGuard()
    patch_records = patch_path_records(patch_paths)
    targets = select_targets(
        patch_paths,
        store,
        verify_only=args.verify_only,
        force=args.force or args.mock_lufs is not None,
        limit=args.limit,
        patch_dirs=SURGE_PATCH_DIRS,
    )

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
    missing_count = len(store.list_missing(patch_records))
    print(
        f"Targets: {len(targets)} patch(es) ({len(patch_paths)} in scope, "
        f"{missing_count} missing entries"
        f"{', force overwrite' if args.force else ''}"
        f"{f', limit {args.limit}' if args.limit > 0 else ''})"
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

    if not targets and args.controls is None:
        if args.verify_only:
            print("No calibrated patches in scope to verify.", file=sys.stderr)
        else:
            print(
                "No patches need calibration (all entries present). Use --force to re-run.",
                file=sys.stderr,
            )
        emit_progress(args, {"type": "done", "updated": 0, "exit_code": 0})
        return 0

    if args.verify_only and args.dry_run:
        for path in targets:
            entry = store.get_entry(path.stem, patch_path=str(path))
            peak = entry.get("post_gain_peak_dbtp") if entry else None
            label = f"post_gain_peak={peak}" if peak is not None else "stored cal"
            print(f"  [would verify] {path.stem} ({label})")
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
    updated = 0
    exit_code = 0
    peak_verify_failed = 0
    load_failures_in_a_row = 0
    cal_attempted = 0
    verify_failed_count = 0
    cal_route = "loopback" if use_loopback else "soundblaster"
    last_patch_index = 0
    last_patch_name = ""
    needs_service_handoff = (use_loopback and args.mock_lufs is None) or standalone_restart
    audio_scope = (
        calibration_audio_scope(restore=not args.no_restore_services)
        if needs_service_handoff
        else contextlib.nullcontext()
    )

    with audio_scope:
        if needs_service_handoff:
            try:
                if use_loopback and args.mock_lufs is None:
                    emit_progress(
                        args,
                        {"type": "setup", "message": "Stopping patch browser and Surge…"},
                    )
                    audio_device = detect_capture_device(args.audio_device, use_loopback=True)
                    print(f"Capture device: {audio_device}", file=sys.stderr)
                    emit_progress(args, {"type": "setup", "message": "Starting Surge for measurement…"})
                    loopback_interface = start_surge_loopback()
                    cal_surge_started = True
                    print(f"Surge loopback interface: {loopback_interface}", file=sys.stderr)
                    loader = PatchLoader(osc_host=args.osc_host, osc_port=args.osc_port)
                    if not loader.osc_enabled:
                        raise RuntimeError("OSC unavailable after loopback Surge start")
                elif standalone_restart:
                    emit_progress(
                        args,
                        {"type": "setup", "message": "Stopping production Surge for measurement…"},
                    )
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
                label = "loopback" if use_loopback and args.mock_lufs is None else "standalone"
                print(f"Error: {label} calibration setup failed: {exc}", file=sys.stderr)
                emit_progress(args, {"type": "error", "message": str(exc)})
                emit_progress(args, {"type": "done", "updated": 0, "exit_code": 1})
                return 1

        signal.signal(signal.SIGTERM, _handle_interrupt)
        signal.signal(signal.SIGINT, _handle_interrupt)

        midi_port = wait_for_surge_midi_port() if args.mock_lufs is None else None
        if midi_port is None and args.mock_lufs is None:
            msg = "Surge MIDI port not found — is Surge running with MIDI inputs?"
            print(f"Error: {msg}", file=sys.stderr)
            emit_progress(args, {"type": "error", "message": msg})
            emit_progress(args, {"type": "done", "updated": 0, "exit_code": 1})
            return 1

        midi_out: object | None = None
        light_measurements: list[tuple[str, float, float, float, Path]] = []
        try:
            if args.mock_lufs is None:
                midi_out = open_midi_out(midi_port)
            if args.controls is not None:
                if midi_out is None:
                    print("Error: Surge MIDI required for --controls", file=sys.stderr)
                    return 1
                results = run_controls(loader, midi_out, audio_device, args.controls)
                for result in results:
                    print(f"  [{'pass' if result.ok else 'FAIL'}] {result.name}: {result.detail}")
                exit_code = 0 if all(r.ok for r in results) else 1
                emit_progress(args, {"type": "done", "updated": 0, "exit_code": exit_code})
                return exit_code
            if not args.verify_only and args.mock_lufs is None:
                backup = backup_store(output_path, pressure_path)
                if backup is not None:
                    print(f"Stores backed up to {backup}")
            if args.verify_only:
                if midi_out is None:
                    msg = "Surge MIDI required for --verify-only"
                    print(f"Error: {msg}", file=sys.stderr)
                    emit_progress(args, {"type": "error", "message": msg})
                    emit_progress(args, {"type": "done", "updated": 0, "exit_code": 1})
                    return 1
                for index, path in enumerate(targets, start=1):
                    if _interrupted:
                        break
                    if needs_service_handoff:
                        refresh_maintenance_flag()
                    name = path.stem
                    print(
                        f"[{index}/{len(targets)}] verify {name}",
                        file=sys.stderr if args.progress_json else sys.stdout,
                    )
                    result = verify_patch(
                        path, loader, store, audio_device=audio_device, midi_out=midi_out
                    )
                    if result.skipped:
                        print(f"  [skip] {name}: {result.reason}", file=sys.stderr)
                        continue
                    if result.ok:
                        print(
                            f"  [pass] {name}: post-gain peak {result.peak_dbtp:.1f} dBTP",
                        )
                    else:
                        verify_failed_count += 1
                        peak_label = (
                            f"{result.peak_dbtp:.1f}"
                            if result.peak_dbtp is not None
                            else "?"
                        )
                        print(
                            f"  [fail] {name}: post-gain peak {peak_label} dBTP "
                            f"(max {POST_GAIN_VERIFY_PEAK_MAX_DBTP:.1f})",
                            file=sys.stderr,
                        )
                exit_code = 1 if verify_failed_count else 0
                emit_progress(
                    args,
                    {
                        "type": "done",
                        "updated": len(targets) - verify_failed_count,
                        "exit_code": exit_code,
                        "verify_failed": verify_failed_count,
                    },
                )
                return exit_code
            for index, path in enumerate(targets, start=1):
                if _interrupted:
                    print("Calibration interrupted — keeping partial progress.", file=sys.stderr)
                    break
                if needs_service_handoff:
                    refresh_maintenance_flag()
                name = path.stem
                last_patch_index = index
                last_patch_name = name
                print(f"[{index}/{len(targets)}] {name}", file=sys.stderr if args.progress_json else sys.stdout)
                emit_progress(
                    args,
                    {"type": "patch", "index": index, "total": len(targets), "name": name},
                )
                try:
                    cal_attempted += 1
                    result = calibrate_patch(
                        path,
                        loader,
                        store,
                        audio_device=audio_device,
                        mock_lufs=args.mock_lufs,
                        dry_run=False,
                        midi_out=midi_out,
                        touch_cal=touch_cal,
                        cal_route=cal_route,
                    )
                    if result.verify_failed:
                        peak_verify_failed += 1
                    load_failures_in_a_row = (
                        load_failures_in_a_row + 1 if result.load_failed else 0
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
                if load_failures_in_a_row >= LOAD_FAILURES_TO_HALT:
                    msg = (
                        f"{load_failures_in_a_row} patch loads in a row were not confirmed "
                        f"by Surge: it is not loading what is asked. Halted."
                    )
                    print(f"Error: {msg}", file=sys.stderr)
                    write_failure_report(
                        patch_index=index,
                        patch_name=name,
                        total=len(targets),
                        reason="load not confirmed",
                        exit_code=1,
                    )
                    emit_progress(args, {"type": "error", "message": msg})
                    exit_code = 1
                    break
                if result.ok and result.lufs_strike is not None and result.lufs_sustain is not None:
                    stuck = plateau.add(path, result.lufs_strike, result.lufs_sustain)
                    if stuck:
                        keys = [
                            resolve_storage_key(p.stem, patch_path=str(p), patch_dirs=SURGE_PATCH_DIRS)
                            for p in stuck
                        ]
                        roll_back_entries(output_path, norm_snapshot, keys)
                        roll_back_entries(pressure_path, pressure_snapshot, keys)
                        msg = (
                            f"{len(stuck)} patches in a row read the same loudness "
                            f"({result.lufs_strike:.1f} / {result.lufs_sustain:.1f} LUFS): "
                            f"the capture is not following the patch. Halted; rolled back "
                            f"{', '.join(keys)}"
                        )
                        print(f"Error: {msg}", file=sys.stderr)
                        write_failure_report(
                            patch_index=index,
                            patch_name=name,
                            total=len(targets),
                            reason="plateau",
                            exit_code=1,
                        )
                        emit_progress(args, {"type": "error", "message": msg})
                        updated -= len(stuck) - 1
                        light_measurements = [m for m in light_measurements if m[4] not in stuck]
                        exit_code = 1
                        break
                if result.ok:
                    updated += 1
                    if (
                        result.lufs_light is not None
                        and result.lufs_strike is not None
                        and result.lufs_sustain is not None
                    ):
                        measurement = (
                            name, result.lufs_light, result.lufs_strike, result.lufs_sustain, path
                        )
                        if touch_cal and args.touch_target_lufs is not None:
                            write_touch_calibrations(
                                pressure_path,
                                [measurement],
                                SURGE_PATCH_DIRS,
                                target_lufs=args.touch_target_lufs,
                            )
                        else:
                            light_measurements.append(measurement)
            if light_measurements and touch_cal and not _interrupted:
                target_lufs, touch_updated = write_touch_calibrations(
                    pressure_path, light_measurements, SURGE_PATCH_DIRS
                )
                print(
                    f"Wrote {touch_updated} touch calibration entries to {pressure_path} "
                    f"(light target {target_lufs:.1f} LUFS)",
                    file=sys.stderr if args.progress_json else sys.stdout,
                )
        finally:
            close_midi_out(midi_out)
            if needs_service_handoff and cal_surge_started and not args.no_restore_services:
                emit_progress(
                    args,
                    {"type": "setup", "message": "Restarting patch browser and Surge…"},
                )
                print("Restoring surge-xt-cli and touch-patch-browser services...", file=sys.stderr)

    if cal_attempted > 0 and peak_verify_failed / cal_attempted > 0.10:
        print(
            f"Error: {peak_verify_failed}/{cal_attempted} patches failed post-gain verify (>10%)",
            file=sys.stderr,
        )
        exit_code = 1
    if updated:
        print(f"Wrote {updated} calibration entries to {output_path}")
    elif cal_attempted == 0:
        print("No entries updated.")
        exit_code = 0
    else:
        print("No entries updated.")
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
