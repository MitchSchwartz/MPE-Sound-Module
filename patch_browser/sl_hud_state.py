"""Read SooperLooper HUD state written by sl-hud-monitor.py."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

SL_HUD_STATE_FILE = Path(
    os.environ.get("MPE_SL_HUD_STATE_FILE", str(Path.home() / ".mpe_sl_hud_state.json"))
)
STALE_AFTER_S = float(os.environ.get("MPE_SL_HUD_STALE_S", "2.0"))


def read_sl_hud_state(*, now: float | None = None) -> dict:
    """Return normalized SL HUD snapshot (empty dict if missing/stale)."""
    now = time.time() if now is None else now
    try:
        raw = json.loads(SL_HUD_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    if not isinstance(raw, dict):
        return {}

    updated = float(raw.get("updated_at") or 0.0)
    if updated <= 0 or (now - updated) > STALE_AFTER_S:
        return {}

    cycle_len = float(raw.get("cycle_len") or 0.0)
    loop_len = float(raw.get("loop_len") or 0.0)
    state = int(raw.get("state") or -1)
    beat = raw.get("beat")
    bar = raw.get("bar")

    playing = state in (4, 5)  # Playing, Overdubbing
    has_master = loop_len > 0.05

    return {
        "active": has_master and playing,
        "has_master": has_master,
        "playing": playing,
        "state": state,
        "cycle_len": cycle_len,
        "loop_len": loop_len,
        "loop_pos": float(raw.get("loop_pos") or 0.0),
        "beat": int(beat) if beat is not None else None,
        "bar": int(bar) if bar is not None else None,
        "updated_at": updated,
    }
