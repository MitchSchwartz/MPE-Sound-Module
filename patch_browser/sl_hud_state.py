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
TRANSPORT_STALE_AFTER_S = float(os.environ.get("MPE_SL_HUD_TRANSPORT_STALE_S", "5.0"))


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
    source = str(raw.get("source") or "")
    stale_s = TRANSPORT_STALE_AFTER_S if source in ("jack_transport", "sl_internal") else STALE_AFTER_S
    if updated <= 0 or (now - updated) > stale_s:
        return {}

    beat = raw.get("beat")
    bar = raw.get("bar")

    # Both live-clock producers publish their own truth; trust it rather than
    # re-deriving from loop_len, which is 0.0 when the clock is not a loop.
    if source in ("jack_transport", "sl_internal"):
        playing = bool(raw.get("playing"))
        has_master = bool(raw.get("has_master"))
        active = bool(raw.get("active"))
        state = int(raw.get("state") or (4 if playing else 0))
        return {
            "active": active,
            "has_master": has_master,
            "playing": playing,
            "state": state,
            "source": source,
            "bpm": raw.get("bpm"),
            # Pass the live position through. Hardcoding 0.0 here meant the UI
            # could only accumulate time since the last file write (~0.5 s), so
            # the sweep filled ~11% of a 4.5 s bar and never completed.
            "cycle_len": float(raw.get("cycle_len") or 0.0),
            "loop_len": float(raw.get("loop_len") or 0.0),
            "loop_pos": float(raw.get("loop_pos") or 0.0),
            "phrase_len": float(raw.get("phrase_len") or 0.0),
            "phrase_pos": float(raw.get("phrase_pos") or 0.0),
            "bars_in_phrase": int(raw.get("bars_in_phrase") or 1),
            "beat": int(beat) if beat is not None else None,
            "bar": int(bar) if bar is not None else None,
            "updated_at": updated,
        }

    cycle_len = float(raw.get("cycle_len") or 0.0)
    loop_len = float(raw.get("loop_len") or 0.0)
    state = int(raw.get("state") or -1)
    playing = state in (4, 5)
    has_master = loop_len > 0.05

    return {
        "active": has_master and playing,
        "has_master": has_master,
        "playing": playing,
        "state": state,
        "source": source or "loop",
        "cycle_len": cycle_len,
        "loop_len": loop_len,
        "loop_pos": float(raw.get("loop_pos") or 0.0),
        "beat": int(beat) if beat is not None else None,
        "bar": int(bar) if bar is not None else None,
        "updated_at": updated,
    }
