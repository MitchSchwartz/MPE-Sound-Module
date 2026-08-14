"""Read SooperLooper HUD state written by sl-hud-monitor.py."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

SL_HUD_STATE_FILE = Path(
    os.environ.get("MPE_SL_HUD_STATE_FILE", str(Path.home() / ".mpe_sl_hud_state.json"))
)
MASTER_CLOCK_FILE = Path(
    os.environ.get(
        "MPE_SL_MASTER_CLOCK_FILE",
        str(Path.home() / ".mpe_sl_master_clock.json"),
    )
)
STALE_AFTER_S = float(os.environ.get("MPE_SL_HUD_STALE_S", "2.0"))


def _read_master_clock() -> dict | None:
    try:
        raw = json.loads(MASTER_CLOCK_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    if float(raw.get("cycle_len") or 0.0) <= 0.0:
        return None
    return raw


def _beat_from_elapsed(elapsed: float, cycle_len: float) -> tuple[int, int]:
    if cycle_len <= 0.0:
        return 1, 1
    pos = elapsed % cycle_len
    beat = int((pos / cycle_len) * 4.0) % 4 + 1
    bar = int(elapsed / cycle_len) + 1
    return beat, bar


def read_sl_hud_state(*, now: float | None = None) -> dict:
    """Return normalized SL HUD snapshot (empty dict if missing/stale)."""
    now = time.time() if now is None else now
    clock = _read_master_clock()

    try:
        raw = json.loads(SL_HUD_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}

    if not isinstance(raw, dict):
        raw = {}

    updated = float(raw.get("updated_at") or 0.0)
    hud_fresh = updated > 0 and (now - updated) <= STALE_AFTER_S

    cycle_len = float(raw.get("cycle_len") or 0.0) if hud_fresh else 0.0
    loop_len = float(raw.get("loop_len") or 0.0) if hud_fresh else 0.0
    state = int(raw.get("state") or -1) if hud_fresh else -1
    loop_pos = float(raw.get("loop_pos") or 0.0) if hud_fresh else 0.0
    beat = raw.get("beat") if hud_fresh else None
    bar = raw.get("bar") if hud_fresh else None

    playing = state in (4, 5)
    has_master = loop_len > 0.05
    sync_source = str(clock.get("source") or "") if clock else ""

    if not has_master and clock and sync_source == "internal":
        cycle_len = float(clock["cycle_len"])
        epoch = float(clock.get("sync_epoch") or 0.0)
        if epoch > 0:
            beat, bar = _beat_from_elapsed(now - epoch, cycle_len)
            has_master = True
            playing = True
            updated = now

    if not hud_fresh and not has_master:
        return {}

    return {
        "active": has_master and playing,
        "has_master": has_master,
        "playing": playing,
        "state": state,
        "cycle_len": cycle_len,
        "loop_len": loop_len,
        "loop_pos": loop_pos,
        "beat": int(beat) if beat is not None else None,
        "bar": int(bar) if bar is not None else None,
        "updated_at": updated,
        "sync_source": sync_source or ("loop0" if loop_len > 0.05 else ""),
    }
