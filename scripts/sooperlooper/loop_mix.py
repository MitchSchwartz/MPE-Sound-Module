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
— loud, and exactly when you least want it. So the first CC *anchors* relative
pickup (no level change); movement after that applies delta from the anchor
against the stored column level. Output wet is smoothed toward the target so
fast drags and misaligned surfaces do not step or jump.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from enum import Enum

from apc_faders import CC_MAX, MASTER, FaderId
from apc_grid import loops_for_column

# Bottom of fader travel must mean silence. A log taper cannot reach zero, so
# the bottom of the range is snapped to it explicitly. Without this the fader
# bottoms out at a quiet-but-audible level, which reads as a bug.
SILENCE_CC = int(os.environ.get("MPE_APC_FADER_SILENCE_CC", "1"))

FADER_FLOOR_DB = float(os.environ.get("MPE_APC_FADER_FLOOR_DB", "-40.0"))
FADER_CEIL_DB = float(os.environ.get("MPE_APC_FADER_CEIL_DB", "0.0"))

# Pickup tolerance in CC steps. Faders are noisy near the detent; requiring an
# exact crossing can leave a fader permanently inert.
PICKUP_TOLERANCE_CC = int(os.environ.get("MPE_APC_FADER_PICKUP_CC", "2"))

# Engine `wet` echoes include master and auto-law — compare composed level,
# not per-column fader CC, or master moves corrupt user_gain.
WET_ECHO_TOLERANCE = float(os.environ.get("MPE_APC_FADER_WET_ECHO", "1e-4"))

# Output smoothing — one-pole follow toward target wet (0 = off).
FADER_SMOOTH_MS = float(os.environ.get("MPE_APC_FADER_SMOOTH_MS", "45"))
FADER_SMOOTH_SNAP = float(os.environ.get("MPE_APC_FADER_SMOOTH_SNAP", "0.004"))

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
    # The master is a factor in the composition below, not a message of its
    # own. See messages_for() for why it is not an engine-global control.
    master_gain: int = CC_MAX
    active_loops: int = 0
    _picked_up: set[FaderId] = field(default_factory=set)
    # Relative pickup: first CC anchors; later CCs apply delta from here.
    _pickup_anchor: dict[int, int] = field(default_factory=dict)
    # Stored column level the relative delta is applied against.
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

        Echo detection compares against ``wet_for(loop)`` — the full composite
        of column fader, master, and auto-law — not ``user_gain`` CC. Inverting
        composed wet into CC and comparing to the column fader corrupts
        ``user_gain`` whenever the master moves.

        A value we did *not* ask for is different: something else changed the
        level, our stored gain is stale, and the physical fader is now lying
        about it. Back out master and law, adopt the implied column fader
        position, and make the fader earn control back.
        """
        if loop not in self.user_gain:
            return
        if abs(wet - self.wet_for(loop)) <= WET_ECHO_TOLERANCE:
            return
        cc = self._user_cc_from_composed_wet(loop, wet)
        if abs(cc - self.user_gain[loop]) <= PICKUP_TOLERANCE_CC:
            return
        self.user_gain[loop] = cc
        for col in range(8):
            if loop in loops_for_column(col):
                self._picked_up.discard(col)
                self._pickup_anchor.pop(col, None)
                self._pickup_ref[col] = cc

    def _user_cc_from_composed_wet(self, loop: int, wet: float) -> int:
        """Column-fader CC implied by a composed engine ``wet`` level."""
        param = PARAMETERS[self.mode]
        master = fader_taper(
            self.master_gain, floor_db=param.floor_db, ceil_db=param.ceil_db
        )
        law = auto_law(self.active_loops)
        divisor = max(master * law, 1e-9)
        user_amp = max(0.0, min(1.0, wet / divisor))
        return _wet_to_cc(user_amp, param)

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
            # Arithmetic in the control layer, not a bus control (DECISIONS.md
            # :468). An engine-global `/set wet` would be the obvious mapping,
            # but nothing in this system has ever written a *level* at engine
            # scope — every global we send is a setting (tempo, sync_source,
            # fade_samples) — so that control is unproven, and an OSC message
            # to a control SooperLooper does not have is dropped in silence.
            # Per-loop `wet` is proven live (eval 2026-08-14). Since loops sum
            # into common_out through plain jack_connect with no gain or
            # limiter stage, scaling all 16 is exactly equal to scaling the
            # bus — the same result over a control we know exists.
            #
            # Master stays exempt from pickup: there is no engine truth to
            # seed it from, so gating it would leave it permanently inert.
            # The cost is chosen, not overlooked — its first move jumps all 16
            # loops at once. That jump is downward from stored unity, which is
            # the safe direction, and the alternative is a dead fader.
            self.master_gain = raw
            return self._all_loop_messages()

        if not isinstance(fader, int) or not 0 <= fader <= 7:
            return []

        loops = [n for n in loops_for_column(fader) if n < self.num_loops]
        if not loops:
            return []

        if not self._accept(fader, raw):
            return []

        effective = self._effective_cc(fader, raw)
        for loop in loops:
            self.user_gain[loop] = effective
        self._pickup_ref[fader] = effective
        return [self._message_for_loop(loop) for loop in loops]

    def _accept(self, fader: int, raw: int) -> bool:
        """Relative pickup: anchor on first touch, no jump; then delta applies."""
        if fader in self._pickup_anchor:
            return True
        self._pickup_anchor[fader] = raw
        self._picked_up.add(fader)
        return False

    def _effective_cc(self, fader: int, raw: int) -> int:
        """Map physical travel to column level via anchor + stored ref."""
        anchor = self._pickup_anchor[fader]
        ref = self._pickup_ref.get(fader, CC_MAX)
        return max(0, min(CC_MAX, ref + (raw - anchor)))

    def _message_for_loop(self, loop: int) -> tuple[str, list]:
        param = PARAMETERS[self.mode]
        return (f"/sl/{loop}/set", [param.control, self.wet_for(loop)])

    def _all_loop_messages(self) -> list[tuple[str, list]]:
        return [self._message_for_loop(loop) for loop in range(self.num_loops)]

    # -- composition ------------------------------------------------------

    def wet_for(self, loop: int) -> float:
        """The single point where every contribution to a loop's level meets.

        Three factors, one multiply: what the user asked this loop to sit at,
        the master, and the automatic backstop law. Everything is recomputed
        in full from state we own, so nothing here compounds and no two
        writers of `wet` can fight.
        """
        param = PARAMETERS[self.mode]
        user = fader_taper(
            self.user_gain.get(loop, CC_MAX),
            floor_db=param.floor_db,
            ceil_db=param.ceil_db,
        )
        master = fader_taper(
            self.master_gain, floor_db=param.floor_db, ceil_db=param.ceil_db
        )
        return max(0.0, min(1.0, user * master * auto_law(self.active_loops)))


def _wet_to_cc(wet: float, param: Parameter) -> int:
    """Inverse of fader_taper, for adopting an engine-reported level."""
    if wet <= 0.0:
        return 0
    log_min = math.log(10.0 ** (param.floor_db / 20.0))
    log_max = math.log(10.0 ** (param.ceil_db / 20.0))
    t = (math.log(max(wet, 1e-9)) - log_min) / (log_max - log_min)
    return max(0, min(CC_MAX, round(t * CC_MAX)))


class CoalescingSender:
    """Rate-limits OSC sends; optional wet smoothing for fader drags.

    Targets update immediately on submit; ``tick`` ramps current wet toward
    target with a one-pole filter so fast moves and misaligned pickup do not
    step or jump. ``flush`` snaps to target (end of drag / idle).
    """

    def __init__(
        self,
        send,
        *,
        interval_s: float = 0.02,
        smooth_tau_s: float | None = None,
        smooth_snap: float | None = None,
    ) -> None:
        self._send = send
        self._interval_s = interval_s
        tau_ms = FADER_SMOOTH_MS
        self._smooth_tau_s = (
            smooth_tau_s if smooth_tau_s is not None else (tau_ms / 1000.0 if tau_ms > 0 else 0.0)
        )
        self._smooth_snap = smooth_snap if smooth_snap is not None else FADER_SMOOTH_SNAP
        self._target_wet: dict[str, float] = {}
        self._current_wet: dict[str, float] = {}
        self._pending_meta: dict[str, list] = {}
        self._last_sent = float("-inf")
        self._last_tick = float("-inf")

    def seed_current(self, path: str, wet: float) -> None:
        """Start the ramp from engine truth — avoids a first-send crackle."""
        if path not in self._current_wet:
            self._current_wet[path] = max(0.0, min(1.0, float(wet)))

    def submit(self, messages: list[tuple[str, list]], *, now: float) -> None:
        for path, args in messages:
            self._pending_meta[path] = args
            if len(args) >= 2 and args[0] == "wet":
                target = float(args[1])
                self._target_wet[path] = target
                if path not in self._current_wet:
                    self._current_wet[path] = target
        if self._smooth_tau_s <= 0:
            if (now - self._last_sent) >= self._interval_s:
                self.flush(now=now)
        else:
            self.tick(now=now)

    def tick(self, *, now: float) -> None:
        if not self._target_wet or self._smooth_tau_s <= 0:
            return
        dt = now - self._last_tick if self._last_tick > float("-inf") else 0.0
        self._last_tick = now
        if dt <= 0:
            return
        alpha = 1.0 - math.exp(-dt / self._smooth_tau_s)
        moved = False
        for path, target in self._target_wet.items():
            cur = self._current_wet.get(path, target)
            if abs(target - cur) <= self._smooth_snap:
                nxt = target
            else:
                nxt = cur + alpha * (target - cur)
            if abs(nxt - cur) > 1e-6:
                self._current_wet[path] = nxt
                moved = True
        if moved and (now - self._last_sent) >= self._interval_s:
            self._emit_current(now=now)

    def flush(self, *, now: float) -> None:
        if not self._target_wet and not self._pending_meta:
            return
        for path, target in self._target_wet.items():
            self._current_wet[path] = target
        self._emit_current(now=now)

    def _emit_current(self, *, now: float) -> None:
        if not self._current_wet:
            return
        for path, wet in self._current_wet.items():
            meta = self._pending_meta.get(path, ["wet"])
            control = meta[0] if meta else "wet"
            self._send(path, [control, wet])
        self._last_sent = now
