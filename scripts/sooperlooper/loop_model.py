"""What a pad press means — as pure functions over engine state.

SooperLooper is the authority on what a loop is doing. The bench used to keep
a parallel `self.state` and *write* to it the moment a command was sent, which
made the two disagree for as long as the engine took to answer. Every symptom
that followed was a variation on the same theme: a pad lit solid green over a
loop that had recorded nothing, a queued stop that a poll silently cancelled,
a grid that stayed quantized after a reset.

The fix is not to delete the bench's idea of state — that idea is load-bearing.
A double tap has to mean "record exactly one cycle" even though the engine has
not yet acknowledged the first tap, and a second tap during a quantized mute
has to mean "actually, keep playing". Both need to know what we just asked for.

The fix is to stop calling intent truth. There are two variables here:

    sl_state   what the engine reports. Authoritative, always.
    pending    what we asked for and have not yet seen confirmed. Expires.

Dispatch reads the two together. The LED renders `pending` as a *blink* and
only ever paints a solid colour from `sl_state` — so an unconfirmed intent is
always visually distinct from an accomplished fact. That distinction is the
whole design, and it is what a solid-green-over-silence bug is made of.

Everything here is a pure function: no OSC, no MIDI, no clock. The caller
executes the plan.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sl_loop_states import (
    SL_STATE_MUTE,
    SL_STATE_OFF,
    SL_STATE_PAUSED,
    SL_STATE_PLAYING,
    SL_STATE_RECORDING,
    SL_STATE_WAIT_START,
    SL_STATE_WAIT_STOP,
)

STATE_IDLE = "idle"
STATE_RECORDING = "recording"
STATE_PLAYING = "playing"
STATE_STOPPED = "stopped"

_FROM_SL = {
    SL_STATE_OFF: STATE_IDLE,
    SL_STATE_RECORDING: STATE_RECORDING,
    SL_STATE_WAIT_START: STATE_RECORDING,
    SL_STATE_WAIT_STOP: STATE_RECORDING,
    SL_STATE_PLAYING: STATE_PLAYING,
    SL_STATE_PAUSED: STATE_STOPPED,
    SL_STATE_MUTE: STATE_STOPPED,
}


def derive_state(sl_state: int) -> str:
    """The engine's state, in the bench's vocabulary. Total, never guesses.

    Unknown codes fall back to idle rather than raising: an engine that invents
    a state must not take the control surface down with it.
    """
    return _FROM_SL.get(sl_state, STATE_IDLE)


def effective_state(sl_state: int, pending: str | None) -> str:
    """What the pad should behave as, given truth and unconfirmed intent.

    `pending` wins until the engine catches up. It is the caller's job to expire
    it — see `pending_resolved`.
    """
    return pending if pending is not None else derive_state(sl_state)


def pending_resolved(sl_state: int, pending: str | None) -> bool:
    """Has the engine caught up with what we asked for?

    True also when the engine went somewhere we did not ask for at all. That is
    not an error to suppress: the engine is the authority, and a pending intent
    that reality has overtaken is stale by definition.
    """
    if pending is None:
        return True
    return derive_state(sl_state) == pending


@dataclass(frozen=True)
class Plan:
    """What to do about a tap. Commands are SooperLooper `hit` verbs, in order."""

    commands: tuple[str, ...] = ()
    # Derived state to hold until the engine confirms it. None = expect nothing.
    expect: str | None = None
    arm_grid: bool = False
    queue_stop: bool = False
    begin_quantize_wait: bool = False
    note: str = ""


def plan_tap(
    *,
    sl_state: int,
    pending: str | None,
    grid_established: bool,
    is_defining: bool,
    quantized: bool,
) -> Plan:
    """The whole gesture vocabulary, in one place.

    idle      -> record
    recording -> stop (quantized to the bar unless this take defines the grid)
    playing   -> mute  (keeps running, so it stays locked to the grid)
    stopped   -> quantized trigger
    """
    state = effective_state(sl_state, pending)

    if state == STATE_IDLE:
        # No grid yet? This take defines it: record free-form and instant,
        # because there is no bar to count in to. Standard looper workflow.
        if not grid_established:
            return Plan(
                commands=("record",),
                expect=STATE_RECORDING,
                arm_grid=True,
                note="defining the grid (free-form, no count-in)",
            )
        return Plan(commands=("record",), expect=STATE_RECORDING)

    if state == STATE_RECORDING:
        if sl_state == SL_STATE_WAIT_STOP:
            # A stop is already queued in the engine and the quantize wait has
            # since expired. `record` here cancels that stop and keeps
            # recording, which is what a player tapping again means.
            return Plan(commands=("record",))
        if sl_state != SL_STATE_RECORDING:
            # Armed, or asked-for-but-unconfirmed. Either way the engine may be
            # sitting in WAIT_START, where `record` arrives as CANCEL and loses
            # the take outright. Queue the stop and fire it the moment recording
            # actually begins, so a double tap records exactly one cycle:
            # starts on the next boundary, ends on the one after.
            #
            # Keying this off WAIT_START alone was not enough — between the tap
            # that arms and the poll that reports it, `sl_state` is still the
            # *previous* state, so a fast double tap read as "idle" and
            # cancelled itself.
            return Plan(
                queue_stop=True,
                # Still recording as far as the player is concerned. Dropping
                # the expectation here let the pad fall back to idle in the
                # middle of a take.
                expect=STATE_RECORDING,
                note="stop queued — will record exactly one cycle",
            )
        # Only wait for a boundary if this loop is actually quantized. In
        # free-form there is no boundary, so arming the wait swallowed the next
        # tap for the whole timeout and stranded the pad on red while the loop
        # was already playing (2026-08-14).
        wait = quantized and not is_defining
        return Plan(
            commands=("record",),
            expect=None if wait else STATE_PLAYING,
            begin_quantize_wait=wait,
        )

    if state == STATE_PLAYING:
        # Mute rather than pause: a muted loop keeps RUNNING, so it stays locked
        # to the grid and relaunching it is back in phase with everything else.
        # With mute_quantized this lands on the bar.
        return Plan(commands=("mute_on",), expect=STATE_STOPPED)

    if state == STATE_STOPPED:
        # Start = quantized trigger. Nothing else.
        #
        # `trigger` plays the loop FROM ITS START and SL defers it to the sync
        # boundary (plugin.cc MULTI_TRIGGER: immediate only when sync is off, or
        # within one eighth of the last sync pulse — a deliberate grace window
        # for hitting slightly late). It also lifts a mute, which is why stop
        # uses mute and not pause.
        #
        # Because every clip restarts from its own beginning on a boundary,
        # phase takes care of itself. There is nothing to track.
        return Plan(
            commands=("pause_off", "trigger"),  # pause_off only matters after stop-all
            expect=STATE_PLAYING,
            note="launch queued — starts on the bar",
        )

    return Plan(commands=("record",), expect=STATE_RECORDING)
