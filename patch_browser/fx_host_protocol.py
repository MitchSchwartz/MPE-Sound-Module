"""The wire format to `native/mpe-fx-host`, and nothing else.

`mpe-fx-host` is the effects insert on the Surge trunk. It holds no policy: it
is told which effects are on and one depth, and it applies them. Whatever
decides those (the FX policy module) sends them through `encode_chain` here,
so the datagram has one definition on the Python side. The C side's parser is
`fx_parse_control` in `native/mpe-fx-host/fx_dsp.c`; `tests/test_fx_host.py`
runs every encoding through it.

One ASCII line per UDP datagram to 127.0.0.1, in the live monitor's style
(`gain 0.500000`):

    chain <mask> <depth>            e.g.  chain 5 0.500000

The mask is a decimal integer with bit `1 << effect_id` set for each enabled
effect (5 = chorus + phaser). The effects run in series in the fixed order
`CHAIN_ORDER`, whatever order the bits are in; the mask only says which. One
depth, 0.0-1.0, applies to every enabled effect. Depth 0 or an empty mask is
bypass: the output is the input, bit for bit. The client smooths depth
(~40 ms) and crossfades each effect in or out when its bit changes (~30 ms),
so a sender may send raw controller steps and toggles. Malformed datagrams and
masks naming an effect this build does not ship are ignored by the client:
they change nothing.

The single-effect datagram of the first protocol, `fx <effect id> <depth>`, is
still accepted and means exactly `chain <1 << id> <depth>`. `encode` writes it.
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

#: The order the enabled effects run in, first to last. A property of the DSP,
#: not of the wire: the mask's bit order is the id order above, never this.
#: Must equal FX_CHAIN_ORDER in fx_dsp.c; `tests/test_fx_host.py` reads it.
CHAIN_ORDER: tuple[int, ...] = (EFFECT_TUBE_DRIVE, EFFECT_PHASER, EFFECT_CHORUS, EFFECT_TREMOLO)

#: No effect enabled, and every effect this build ships enabled.
MASK_NONE = 0
MASK_ALL = (1 << len(EFFECT_IDS)) - 1


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
    """The single-effect datagram: `effect_id` alone at `depth` (clamped to
    0..1). The client reads it as `encode_chain(1 << effect_id, depth)`.

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


def _check_id(effect_id: int) -> int:
    if isinstance(effect_id, bool) or not isinstance(effect_id, int):
        raise ValueError(f"effect id must be an int, got {effect_id!r}")
    if effect_id not in EFFECT_IDS:
        raise ValueError(f"unknown effect id {effect_id} (known: {EFFECT_IDS})")
    return effect_id


def _check_mask(mask: int) -> int:
    if isinstance(mask, bool) or not isinstance(mask, int):
        raise ValueError(f"mask must be an int, got {mask!r}")
    if mask < 0 or mask & ~MASK_ALL:
        raise ValueError(f"mask {mask} names an effect this build does not ship (all: {MASK_ALL})")
    return mask


def mask_of(effect_ids) -> int:
    """The mask with exactly these effects enabled (any order, repeats allowed)."""
    mask = MASK_NONE
    for effect_id in effect_ids:
        mask |= 1 << _check_id(effect_id)
    return mask


def effects_in(mask: int) -> tuple[int, ...]:
    """The enabled effect ids, in the order they run (`CHAIN_ORDER`)."""
    _check_mask(mask)
    return tuple(effect_id for effect_id in CHAIN_ORDER if mask & (1 << effect_id))


def is_enabled(mask: int, effect_id: int) -> bool:
    return bool(_check_mask(mask) & (1 << _check_id(effect_id)))


def toggled(mask: int, effect_id: int) -> int:
    """`mask` with `effect_id` flipped: a tap on that effect's row."""
    return _check_mask(mask) ^ (1 << _check_id(effect_id))


def encode_chain(mask: int, depth: float) -> bytes:
    """The datagram that enables the effects in `mask` at one `depth` (clamped
    to 0..1).

    Raises ValueError for a mask naming an effect this build does not ship, and
    for a non-finite depth, for the same reason `encode` does.
    """
    _check_mask(mask)
    value = float(depth)
    if not math.isfinite(value):
        raise ValueError(f"depth must be finite, got {depth!r}")
    value = max(0.0, min(1.0, value))
    return f"chain {mask} {value:.6f}".encode("ascii")
