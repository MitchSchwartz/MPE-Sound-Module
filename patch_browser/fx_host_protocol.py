"""The wire format to `native/mpe-fx-host`, and nothing else.

`mpe-fx-host` is the effects insert on the Surge trunk. It holds no policy: it
is told an effect id and a depth, and it applies them. Whatever decides those
(the FX policy module) sends them through `encode` here, so the datagram has
one definition on the Python side. The C side's parser is `fx_parse_control`
in `native/mpe-fx-host/fx_dsp.c`; `tests/test_fx_host.py` runs every encoding
through it.

One ASCII line per UDP datagram to 127.0.0.1, in the live monitor's style
(`gain 0.500000`), extended with the effect:

    fx <effect id> <depth>          e.g.  fx 0 0.500000

Depth 0.0–1.0 is the wet/dry mix; 0 is bypass, so there is no separate enable.
The client smooths depth (~40 ms) and crossfades an effect swap (~30 ms), so a
sender may send raw controller steps. Malformed datagrams and unknown effect ids
are ignored by the client — they change nothing.
"""

from __future__ import annotations

import math
import os

DEFAULT_HOST = "127.0.0.1"
#: Must equal CONTROL_PORT_DEFAULT in native/mpe-fx-host/mpe-fx-host.c.
#: `tests/test_fx_host.py` reads the C source and fails if they drift.
DEFAULT_PORT = 9958

#: Effect ids, in the order of `FX_EFFECTS[]` in fx_dsp.c. Append only: an id is
#: a number on the wire, and renumbering would silently change what an old
#: sender selects.
EFFECT_CHORUS = 0
EFFECT_TUBE_DRIVE = 1
EFFECT_PHASER = 2
EFFECT_TREMOLO = 3
EFFECT_IDS: tuple[int, ...] = (EFFECT_CHORUS, EFFECT_TUBE_DRIVE, EFFECT_PHASER, EFFECT_TREMOLO)
EFFECT_NAMES: dict[int, str] = {
    EFFECT_CHORUS: "Chorus",
    EFFECT_TUBE_DRIVE: "Drive",
    EFFECT_PHASER: "Phaser",
    EFFECT_TREMOLO: "Tremolo",
}


def enabled(value: str | None = None) -> bool:
    """One law for "is the FX host on", shared with the shell.

    `scripts/start-mpe-fx-host.sh` lowercases the same variable and matches the
    same four words — the law `live_monitor.enabled` and its start script
    share, for the same reason: half a feature running is worse than either
    state.
    """
    raw = value if value is not None else os.environ.get("MPE_FX_HOST", "0")
    return (raw or "").strip().lower() in ("1", "true", "yes", "on")


def resolve_port() -> int:
    raw = os.environ.get("MPE_FX_HOST_PORT", "")
    try:
        port = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_PORT
    return port if 1 <= port <= 65535 else DEFAULT_PORT


def encode(effect_id: int, depth: float) -> bytes:
    """The datagram that sets `effect_id` at `depth` (clamped to 0..1).

    Raises ValueError for an effect id this build does not ship, and for a
    non-finite depth: a caller holding NaN has a bug, and sending 0 or 1 in its
    place would hide it behind an audible guess.
    """
    if isinstance(effect_id, bool) or not isinstance(effect_id, int):
        raise ValueError(f"effect id must be an int, got {effect_id!r}")
    if effect_id not in EFFECT_IDS:
        raise ValueError(f"unknown effect id {effect_id} (known: {EFFECT_IDS})")
    value = float(depth)
    if not math.isfinite(value):
        raise ValueError(f"depth must be finite, got {depth!r}")
    value = max(0.0, min(1.0, value))
    return f"fx {effect_id} {value:.6f}".encode("ascii")
