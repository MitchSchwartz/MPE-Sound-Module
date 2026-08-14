"""Saved grid reference — internal tempo when master clip is cleared.

SooperLooper sync_source values (OSC /set):
  -3 internal, -2 midi, -1 jack, 0 none, >0 loop (1-indexed)
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Callable

MASTER_CLOCK_FILE = Path(
    os.environ.get(
        "MPE_SL_MASTER_CLOCK_FILE",
        str(Path.home() / ".mpe_sl_master_clock.json"),
    )
)
HUD_STATE_FILE = Path(
    os.environ.get(
        "MPE_SL_HUD_STATE_FILE",
        str(Path.home() / ".mpe_sl_hud_state.json"),
    )
)
DEFAULT_EIGHTH_PER_CYCLE = 8


def tempo_from_cycle_len(cycle_len: float, *, eighth_per_cycle: int = DEFAULT_EIGHTH_PER_CYCLE) -> float | None:
    """BPM for a 4/4 bar when cycle_len is one bar (8 eighths)."""
    if cycle_len <= 0.0:
        return None
    beats_per_cycle = eighth_per_cycle / 2.0
    return beats_per_cycle * 60.0 / cycle_len


def load_master_clock() -> dict | None:
    try:
        raw = json.loads(MASTER_CLOCK_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    tempo = raw.get("tempo")
    cycle_len = raw.get("cycle_len")
    if tempo is None or cycle_len is None:
        return None
    if float(cycle_len) <= 0.0 or float(tempo) <= 0.0:
        return None
    return raw


def save_master_clock(
    *,
    tempo: float,
    cycle_len: float,
    loop_len: float | None = None,
    source: str = "loop0",
    eighth_per_cycle: int = DEFAULT_EIGHTH_PER_CYCLE,
) -> dict:
    payload = {
        "updated_at": time.time(),
        "tempo": float(tempo),
        "cycle_len": float(cycle_len),
        "loop_len": float(loop_len) if loop_len is not None else float(cycle_len),
        "eighth_per_cycle": int(eighth_per_cycle),
        "source": source,
        "sync_epoch": time.time(),
    }
    tmp = MASTER_CLOCK_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(MASTER_CLOCK_FILE)
    return payload


def clear_master_clock() -> None:
    try:
        MASTER_CLOCK_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def capture_from_hud_snapshot(snapshot: dict) -> dict | None:
    """Persist grid reference from live loop-0 measurements."""
    cycle_len = float(snapshot.get("cycle_len") or 0.0)
    loop_len = float(snapshot.get("loop_len") or 0.0)
    if cycle_len <= 0.0 and loop_len > 0.0:
        cycle_len = loop_len
    if cycle_len <= 0.0:
        return None
    tempo = tempo_from_cycle_len(cycle_len)
    if tempo is None:
        return None
    return save_master_clock(
        tempo=tempo,
        cycle_len=cycle_len,
        loop_len=loop_len if loop_len > 0.0 else cycle_len,
        source="loop0",
    )


def capture_from_hud_file(path: Path | None = None) -> dict | None:
    path = path or HUD_STATE_FILE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    return capture_from_hud_snapshot(raw)


def apply_internal_master(
    send: Callable[[str, list], None],
    clock: dict,
    *,
    num_loops: int = 16,
    master_loop: int = 0,
) -> None:
    """Quantize to saved tempo/cycle — no live sync_source loop required."""
    tempo = float(clock["tempo"])
    eighth = int(clock.get("eighth_per_cycle") or DEFAULT_EIGHTH_PER_CYCLE)
    send("/set", ["sync_source", -3.0])
    send("/set", ["tempo", tempo])
    send("/set", ["eighth_per_cycle", float(eighth)])
    send("/set", ["tap_tempo", 0.0])  # noop pulse — anchors UI if needed

    for loop in range(num_loops):
        prefix = f"/sl/{loop}/set"
        if loop == master_loop:
            send(prefix, ["quantize", 0.0])
            send(prefix, ["sync", 0.0])
            send(prefix, ["relative_sync", 0.0])
            send(prefix, ["round", 0.0])
        else:
            send(prefix, ["quantize", 1.0])
            send(prefix, ["sync", 1.0])
            send(prefix, ["relative_sync", 0.0])
            send(prefix, ["round", 0.0])
            send(prefix, ["playback_sync", 1.0])

    clock["source"] = "internal"
    clock["sync_epoch"] = time.time()
    clock["updated_at"] = time.time()
    tmp = MASTER_CLOCK_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(clock), encoding="utf-8")
    tmp.replace(MASTER_CLOCK_FILE)


def master_sync_mode() -> str | None:
    clock = load_master_clock()
    if not clock:
        return None
    return str(clock.get("source") or "loop0")
