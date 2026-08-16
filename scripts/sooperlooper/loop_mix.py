"""Fader → loop parameter: the gain store, the taper, and the emission plan.

Same contract as loop_model.py, and for the same reason: everything that
decides *what to send* is a pure function over held state, and the caller does
the I/O. No OSC, no MIDI, no clock in this module. Tests drive it with plain
integers and assert on the returned messages.

Three rules hold this together.

**State lives per (loop, parameter), never on the fader.** A fader is a
write-only *view* onto a set of loops. Fader 0 writes loops 0 and 8 because
apc_grid.loops_for_column(0) says those share the column — not because the
fader owns them. This is what keeps the future cheap: banking, per-loop
deviation, a touch-screen mixer and "this fader means pan now" are all new
views over the same store rather than a rewrite of it.

**`wet` is derived, never modified.** SooperLooper's per-loop `wet` has two
claimants: the user's fader, and the `loop_gain/N` backstop law planned in
DECISIONS.md:468 ("arithmetic in the control layer, not DSP"). Two writers on
one control drift, and the drift looks like a hardware fault rather than a
design error. So there is exactly one composition point,

    wet_for(loop) = taper(user_gain[loop]) * auto_law(active_loops)

and it is always recomputed in full from state we own. We never read the
engine's current `wet` and adjust it. When the active-loop count changes, every
loop is re-emitted rather than nudged.

**Physical position is not truth.** The faders have no motors, so they send
nothing until moved and their positions at startup are unknown. Taking the
first CC at face value means the first touch of a fader mid-jam jumps the level
— loud, and exactly when you least want it. So gains are seeded from what the
engine reports, and a fader is ignored until it *crosses* the value it is
supposed to be at (pickup). Until then it is a lie about a number we already
know.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from enum import Enum

from apc_faders import CC_MAX, MASTER, FaderId
from apc_grid import loops_for_column

# The loop-bus master. UNVERIFIED against the engine — SooperLooper source is
# not available off the Pi. Kept as one named constant so confirming it is a
# one-line change. Check src/control_osc.cpp on the Pi before trusting it.
SL_MASTER_CONTROL = os.environ.get("MPE_SL_MASTER_CONTROL", "wet")

# Bottom of fader travel must mean silence. A log taper cannot reach zero, so
# the bottom of the range is snapped to it explicitly. Without this the fader
# bottoms out at a quiet-but-audible level, which reads as a bug.
SILENCE_CC = int(os.environ.get("MPE_APC_FADER_SILENCE_CC", "1"))

FADER_FLOOR_DB = float(os.environ.get("MPE_APC_FADER_FLOOR_DB", "-40.0"))
FADER_CEIL_DB = float(os.environ.get("MPE_APC_FADER_CEIL_DB", "0.0"))

# Pickup tolerance in CC steps. Faders are noisy near the detent; requiring an
# exact crossing can leave a fader permanently inert.
PICKUP_TOLERANCE_CC = int(os.environ.get("MPE_APC_FADER_PICKUP_CC", "2"))

# Off by default: the backstop law is planned, not yet agreed as always-on.
# The seam exists from the first commit so that turning it on is a config
# change rather than a refactor of who writes `wet`.
AUTO_LAW_ENABLED = os.environ.get("MPE_SL_LOOP_GAIN_LAW", "0").strip() not in (
    "", "0", "off", "false",
)
LOOP_GAIN = float(os.environ.get("MPE_SL_LOOP_GAIN", "1.0"))


class FaderMode(Enum):
    """What the eight loop faders currently mean.

    One member today. Pan and LFO filter are named in the roadmap and land as
    new members plus new Parameter descriptors — not as new writers of `wet`.
    """

    LEVEL = "level"


@dataclass(frozen=True)
class Parameter:
    """An SL control a fader can be bound to, and how travel maps onto it."""

    control: str
    floor_db: float = FADER_FLOOR_DB
    ceil_db: float = FADER_CEIL_DB
    default: float = 1.0


PARAMETERS: dict[FaderMode, Parameter] = {
    FaderMode.LEVEL: Parameter(control="wet"),
}


def fader_taper(raw: int, *, floor_db: float, ceil_db: float) -> float:
    """CC 0–127 → linear 0.0–1.0, even dB per unit of travel.

    Same shape as the touch browser's Vol fader
    (patch_browser/patch_normalization.py::volume_fader_to_amp_linear) but not
    the same function: that one is bound to Surge amp/volume, patch gain and a
    normalisation cap. Only the law is shared, deliberately duplicated rather
    than coupling the looper control path to the patch browser.
    """
    if raw <= SILENCE_CC:
        return 0.0
    t = max(0.0, min(1.0, raw / CC_MAX))
    log_min = math.log(10.0 ** (floor_db / 20.0))
    log_max = math.log(10.0 ** (ceil_db / 20.0))
    return math.exp(log_min + t * (log_max - log_min))


def auto_law(active_loops: int) -> float:
    """The loop_gain/N backstop from DECISIONS.md:468.

    Keeps stacked loops from summing into the limiter. Identity while
    disabled — the point of it being here regardless is that there is only ever
    one place where the automatic law and the user's fader are combined.
    """
    if not AUTO_LAW_ENABLED or active_loops <= 0:
        return 1.0
    return LOOP_GAIN / active_loops


@dataclass
class LoopMix:
    """Held state: what the user has asked each loop to sit at.

    Gains are stored as CC values (0–127), not as tapered floats, so pickup
    comparisons happen in the same units the hardware speaks and the taper
    stays a pure display of them.
    """

    num_loops: int = 16
    mode: FaderMode = FaderMode.LEVEL
    user_gain: dict[int, int] = field(default_factory=dict)
    active_loops: int = 0
    _picked_up: set[FaderId] = field(default_factory=set)
    # Where each fader must cross before it may write. Held per fader rather
    # than read off one of the column's loops, because the two loops in a
    # column can legitimately disagree — the engine seeds them independently,
    # and a single fader has no way to express the difference. Picking one
    # loop's value arbitrarily would mean a seed on the other loop silently
    # failed to re-arm anything.
    _pickup_ref: dict[int, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for loop in range(self.num_loops):
            self.user_gain.setdefault(loop, CC_MAX)
        for col in range(8):
            self._pickup_ref.setdefault(col, CC_MAX)

    # -- state seeding ----------------------------------------------------

    def seed_from_engine(self, loop: int, wet: float) -> None:
        """Adopt the engine's reported level as truth for this loop.

        Called from the OSC state listener, which streams `wet` continuously —
        so the common case is the engine echoing back a value we just set. That
        must be a no-op. Re-arming pickup on every echo would leave every fader
        permanently inert, since a fader can never cross a target that moves to
        meet it 10 times a second.

        A value we did *not* ask for is different: something else changed the
        level, our stored gain is stale, and the physical fader is now lying
        about it. Adopt it and make the fader earn control back.
        """
        if loop not in self.user_gain:
            return
        cc = _wet_to_cc(wet, PARAMETERS[self.mode])
        if abs(cc - self.user_gain[loop]) <= PICKUP_TOLERANCE_CC:
            return
        self.user_gain[loop] = cc
        for col in range(8):
            if loop in loops_for_column(col):
                self._picked_up.discard(col)
                self._pickup_ref[col] = cc

    def note_active_loops(self, count: int) -> list[tuple[str, list]]:
        """Loop count changed → recompute every loop, do not nudge any."""
        if count == self.active_loops:
            return []
        self.active_loops = count
        return self._all_loop_messages()

    # -- emission ---------------------------------------------------------

    def messages_for(self, fader: FaderId, raw: int) -> list[tuple[str, list]]:
        """(path, args) to send for this fader movement. [] if suppressed."""
        raw = max(0, min(CC_MAX, int(raw)))
        if fader == MASTER:
            # Nothing is stored: the master is a bus control, so it cannot take
            # part in wet_for() composition, and there is no per-loop truth to
            # seed it from. It is a straight pass-through by design.
            param = PARAMETERS[FaderMode.LEVEL]
            value = fader_taper(raw, floor_db=param.floor_db, ceil_db=param.ceil_db)
            return [("/set", [SL_MASTER_CONTROL, value])]

        if not isinstance(fader, int) or not 0 <= fader <= 7:
            return []

        loops = [n for n in loops_for_column(fader) if n < self.num_loops]
        if not loops:
            return []

        if not self._accept(fader, raw, loops):
            return []

        for loop in loops:
            self.user_gain[loop] = raw
        self._pickup_ref[fader] = raw
        return [self._message_for_loop(loop) for loop in loops]

    def _accept(self, fader: int, raw: int, loops: list[int]) -> bool:
        """Pickup: a fader may write once it has crossed where it should be."""
        if fader in self._picked_up:
            return True
        if abs(raw - self._pickup_ref.get(fader, CC_MAX)) <= PICKUP_TOLERANCE_CC:
            self._picked_up.add(fader)
            return True
        return False

    def _message_for_loop(self, loop: int) -> tuple[str, list]:
        param = PARAMETERS[self.mode]
        return (f"/sl/{loop}/set", [param.control, self.wet_for(loop)])

    def _all_loop_messages(self) -> list[tuple[str, list]]:
        return [self._message_for_loop(loop) for loop in range(self.num_loops)]

    # -- composition ------------------------------------------------------

    def wet_for(self, loop: int) -> float:
        """The single point where the user's gain and the automatic law meet."""
        param = PARAMETERS[self.mode]
        user = fader_taper(
            self.user_gain.get(loop, CC_MAX),
            floor_db=param.floor_db,
            ceil_db=param.ceil_db,
        )
        return max(0.0, min(1.0, user * auto_law(self.active_loops)))


def _wet_to_cc(wet: float, param: Parameter) -> int:
    """Inverse of fader_taper, for adopting an engine-reported level."""
    if wet <= 0.0:
        return 0
    log_min = math.log(10.0 ** (param.floor_db / 20.0))
    log_max = math.log(10.0 ** (param.ceil_db / 20.0))
    t = (math.log(max(wet, 1e-9)) - log_min) / (log_max - log_min)
    return max(0, min(CC_MAX, round(t * CC_MAX)))


class CoalescingSender:
    """Rate-limits a dragging fader without ever losing where it stopped.

    A fast drag produces a CC every few milliseconds; forwarding all of them
    floods the engine. Dropping the *last* one is far worse than the flood
    it prevents: the fader ends up physically somewhere the engine never heard
    about, so the surface is lying about the level until the fader is touched
    again. So intermediate values are dropped freely and the endpoint is always
    flushed.
    """

    def __init__(self, send, *, interval_s: float = 0.02) -> None:
        self._send = send
        self._interval_s = interval_s
        self._pending: dict[str, list] = {}
        # -inf, not 0.0: the caller's clock is monotonic and starts near zero,
        # so a zero here makes the very first movement of a fader wait out a
        # full interval. The first touch is exactly the one that must not be
        # swallowed.
        self._last_sent = float("-inf")

    def submit(self, messages: list[tuple[str, list]], *, now: float) -> None:
        for path, args in messages:
            self._pending[path] = args
        if (now - self._last_sent) >= self._interval_s:
            self.flush(now=now)

    def flush(self, *, now: float) -> None:
        if not self._pending:
            return
        for path, args in self._pending.items():
            self._send(path, args)
        self._pending.clear()
        self._last_sent = now
