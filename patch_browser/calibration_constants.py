"""Shared calibration handoff constants (touch browser exec → loader teardown).

Invariant when ``MPE_CALIB_FROM_BROWSER=1``:

- ``stop_mpe_audio_services`` must **not** stop ``touch-patch-browser`` (the loader
  runs as the service main process; stopping it deadlocks teardown).
- ``restore_mpe_audio_services`` must **schedule** an async ``systemctl restart``
  instead of a synchronous ``systemctl start`` on ``touch-patch-browser``.

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


def calibration_from_browser() -> bool:
    """True when calibration was launched via touch browser exec handoff."""
    return os.environ.get(MPE_CALIB_FROM_BROWSER) == MPE_CALIB_FROM_BROWSER_ACTIVE
