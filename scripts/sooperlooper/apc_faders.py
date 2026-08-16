"""APC mini faders ↔ control identity (eval + future product).

Nine physical faders: eight above the grid columns, one master on the right.
This module answers exactly one question — "which fader is this CC?" — and
answers it as a pure function. No MIDI, no OSC, no clock.

Variant-aware on purpose. The mk1/mk2 divergence is a known hazard here:
apc_transport.py exists solely because Shift and Stop-All sit on different
notes on the two surfaces. The fader CCs are *believed* to agree (48–55 plus
56) but that has not been confirmed against hardware, so the two variants get
separate constants and go through the same resolve-by-port-name path. If they
turn out to differ, one constant changes and nothing else does.

Confirm with: sooperlooper-apc-bench.py --dump-midi, then move each fader.

What a fader is *bound to* is not decided here — see loop_mix.py. A fader is a
control identity; the binding is a separate layer, because these faders are
going to mean pan or filter as well as level.
"""

from __future__ import annotations

# The master fader is not fader index 8 — it is a different kind of thing,
# addressing the loop bus rather than a grid column. Naming it as a string
# keeps that distinction in the type instead of in a comment.
MASTER = "master"

FaderId = int | str  # 0–7, or MASTER

# APC mini mk2 (Communication Protocol v1.0)
CC_FADER_BASE_MK2 = 48
CC_MASTER_MK2 = 56

# APC mini mk1 (original)
CC_FADER_BASE_MK1 = 48
CC_MASTER_MK1 = 56

NUM_LOOP_FADERS = 8
CC_MIN = 0
CC_MAX = 127


def resolve_fader_ccs(
    port_name: str,
    *,
    variant: str | None = None,
) -> tuple[tuple[int, ...], int, str]:
    """Return (loop_fader_ccs, master_cc, label) for the connected APC.

    Mirrors resolve_apc_transport_notes() deliberately: same argument shape,
    same explicit-variant-then-port-name precedence, same mk1 default. One
    surface, one way of asking what it is.
    """
    explicit = (variant or "").strip().lower()
    if explicit in ("mk2", "mkii", "2"):
        return _ccs(CC_FADER_BASE_MK2), CC_MASTER_MK2, "mk2"
    if explicit in ("mk1", "1", "original", "mini"):
        return _ccs(CC_FADER_BASE_MK1), CC_MASTER_MK1, "mk1"

    name = port_name.lower()
    if "mk2" in name or "mkii" in name:
        return _ccs(CC_FADER_BASE_MK2), CC_MASTER_MK2, "mk2"
    return _ccs(CC_FADER_BASE_MK1), CC_MASTER_MK1, "mk1"


def _ccs(base: int) -> tuple[int, ...]:
    return tuple(base + i for i in range(NUM_LOOP_FADERS))


def fader_for_cc(
    cc: int,
    *,
    loop_fader_ccs: tuple[int, ...],
    master_cc: int,
) -> FaderId | None:
    """Fader index 0–7, MASTER, or None if this CC is not a fader."""
    if cc == master_cc:
        return MASTER
    try:
        return loop_fader_ccs.index(cc)
    except ValueError:
        return None


def is_control_change(status: int) -> bool:
    return (status & 0xF0) == 0xB0
