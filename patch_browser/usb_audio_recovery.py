"""Read UAC2 stall-recovery state written by uac2-stall-watchdog.sh."""

from __future__ import annotations

import os
import time
from pathlib import Path

RECOVERY_STATE_PATH = Path(
    os.environ.get("MPE_UAC2_RECOVERY_STATE", "/tmp/mpe-uac2-recovery.state")
)
# Hide stale flags if the watchdog crashed mid-restart.
STALE_SECONDS = 90.0


def is_recovering() -> bool:
    """True while the Pi is restarting Surge for a wedged USB gadget writer."""
    if not RECOVERY_STATE_PATH.is_file():
        return False
    try:
        text = RECOVERY_STATE_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    if not text.startswith("recovering"):
        return False
    parts = text.split()
    if len(parts) >= 2:
        try:
            since = float(parts[1])
        except ValueError:
            since = RECOVERY_STATE_PATH.stat().st_mtime
    else:
        since = RECOVERY_STATE_PATH.stat().st_mtime
    return (time.time() - since) < STALE_SECONDS


def status_subtitle() -> str | None:
    if is_recovering():
        return "Recovering USB audio for DAW…"
    return None
