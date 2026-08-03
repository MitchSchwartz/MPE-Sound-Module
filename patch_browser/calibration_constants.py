"""Shared calibration handoff constants (touch browser exec → loader teardown).

Invariant when ``MPE_CALIB_FROM_BROWSER=1``:

- ``stop_mpe_audio_services`` must **not** stop ``touch-patch-browser`` (the loader
  runs as the service main process; stopping it deadlocks teardown).
- ``restore_mpe_audio_services`` must **not** ``systemctl restart touch-patch-browser`` —
  the loader ``exec``s back into ``touch_patch_browser.py`` (same service PID chain).

Set by ``touch_patch_browser`` before ``exec`` into ``calibrate-with-loader.sh``.
Read by ``calibration_teardown`` and ``scripts/calibrate-with-loader.sh``.
"""

from __future__ import annotations

import os
from pathlib import Path

# Environment variable name (value must be "1" when set).
MPE_CALIB_FROM_BROWSER = "MPE_CALIB_FROM_BROWSER"
MPE_CALIB_FROM_BROWSER_ACTIVE = "1"

REPO_ROOT = Path(__file__).resolve().parent.parent
CALIBRATE_WITH_LOADER_SCRIPT = REPO_ROOT / "scripts" / "calibrate-with-loader.sh"
CALIBRATION_LOADER_SCRIPT = REPO_ROOT / "patch_browser" / "calibration_loader.py"
TOUCH_PATCH_BROWSER_SCRIPT = REPO_ROOT / "touch_patch_browser.py"

# Pi-measured average per patch (loopback cal, Aug 2026): full gesture + light-touch
# capture, settle, ffmpeg measure, and ~25% needing progressive retries.
CALIBRATION_SECONDS_PER_PATCH_ESTIMATE = 45.0


def estimate_calibration_duration_seconds(patch_count: int) -> float:
    """Rough wall-clock for confirm modal / dry-run (not a guarantee)."""
    if patch_count <= 0:
        return 0.0
    return patch_count * CALIBRATION_SECONDS_PER_PATCH_ESTIMATE


def format_calibration_duration_hint(patch_count: int) -> str:
    """Human-readable duration for touch UI confirm modal."""
    if patch_count <= 0:
        return "Nothing to calibrate — all patches already have entries."
    seconds = estimate_calibration_duration_seconds(patch_count)
    if seconds < 60:
        return f"Approx. {int(seconds)} sec ({patch_count} patch(es))."
    if seconds < 3600:
        return f"Approx. {seconds / 60.0:.0f} min ({patch_count} patch(es))."
    hours = int(seconds // 3600)
    minutes = int(round((seconds % 3600) / 60.0))
    if minutes == 0:
        return f"Approx. {hours} hr ({patch_count} patch(es))."
    return f"Approx. {hours} hr {minutes} min ({patch_count} patch(es))."


def calibration_from_browser() -> bool:
    """True when calibration was launched via touch browser exec handoff."""
    return os.environ.get(MPE_CALIB_FROM_BROWSER) == MPE_CALIB_FROM_BROWSER_ACTIVE
