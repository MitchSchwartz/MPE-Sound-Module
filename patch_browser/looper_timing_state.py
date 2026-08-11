"""Publish on-device looper timing for touch header HUD (~/.mpe_looper_timing.json)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

TIMING_STATE_FILE = Path.home() / ".mpe_looper_timing.json"


def write_timing_state(
    *,
    active: bool,
    bpm: float | None = None,
    beat_in_bar: int | None = None,
    beats_per_bar: int = 4,
    bar_in_loop: int | None = None,
    bars_per_loop: int = 4,
    beat_phase: float | None = None,
    path: Path | None = None,
) -> None:
    target = path or TIMING_STATE_FILE
    payload = {
        "active": active,
        "bpm": bpm,
        "beat_in_bar": beat_in_bar,
        "beats_per_bar": beats_per_bar,
        "bar_in_loop": bar_in_loop,
        "bars_per_loop": bars_per_loop,
        "beat_phase": beat_phase,
        "updated_at": time.monotonic(),
    }
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, target)


def clear_timing_state(*, path: Path | None = None) -> None:
    write_timing_state(
        active=False,
        bpm=None,
        beat_in_bar=None,
        bar_in_loop=None,
        path=path,
    )


def read_timing_state(
    *,
    path: Path | None = None,
    stale_after_s: float = 5.0,
    now: float | None = None,
) -> dict:
    target = path or TIMING_STATE_FILE
    now = time.monotonic() if now is None else now
    empty: dict = {
        "active": False,
        "online": False,
        "bpm": None,
        "beat_in_bar": None,
        "beats_per_bar": 4,
        "bar_in_loop": None,
        "bars_per_loop": 4,
        "beat_phase": 0.0,
    }
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return empty

    updated = float(data.get("updated_at", 0.0))
    online = (now - updated) <= stale_after_s
    return {
        "active": bool(data.get("active")) and online,
        "online": online,
        "bpm": data.get("bpm") if online else None,
        "beat_in_bar": data.get("beat_in_bar") if online else None,
        "beats_per_bar": int(data.get("beats_per_bar") or 4),
        "bar_in_loop": data.get("bar_in_loop") if online else None,
        "bars_per_loop": int(data.get("bars_per_loop") or 4),
        "beat_phase": float(data.get("beat_phase") or 0.0) if online else 0.0,
    }
