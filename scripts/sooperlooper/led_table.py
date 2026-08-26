"""Pad colour as a pure function of engine state and unconfirmed intent.

The rule that makes the surface trustworthy:

    A solid colour is only ever painted from `sl_state`. Anything we have asked
    for but not seen confirmed blinks.

So a pad going solid green is a promise that the engine says the loop is
playing. It used to be a promise that we had *sent* a command, which is how a
pad sat solid green over a loop containing no audio at all.

Blink = "queued, lands on the next bar" — the clip-launcher idiom, and the same
shape Ableton uses. A quantized action does not take effect until the next
cycle boundary, up to a full bar later. Without a distinct armed state the pad
looks identical before and after the tap, so the player reads it as "my press
did nothing", presses again, or holds — and holding clears the take they just
recorded. That cost an evening on 2026-08-14.
"""

from __future__ import annotations

from loop_model import (
    STATE_IDLE,
    STATE_PLAYING,
    STATE_RECORDING,
    STATE_STOPPED,
    derive_state,
)
from sl_loop_states import (
    SL_STATE_OVERDUBBING,
    SL_STATE_PLAYING,
    SL_STATE_WAIT_START,
    SL_STATE_WAIT_STOP,
)

LED_OFF = 0
LED_GREEN = 1
LED_GREEN_BLINK = 2
LED_RED = 3
LED_RED_BLINK = 4
LED_YELLOW = 5
LED_YELLOW_BLINK = 6

# APC side buttons are single-colour — not the grid RGB velocity table.
# Scene Launch (Stop All, etc.): green only. Track Select: red only.
SCENE_LED_OFF = 0
SCENE_LED_ON = 1
SCENE_LED_BLINK = 2
TRACK_LED_OFF = 0
TRACK_LED_ON = 1
TRACK_LED_BLINK = 2


def accelerating_hold_blink_on(
    elapsed: float,
    *,
    hold_s: float,
    blink_after_s: float = 0.0,
    blink_start_half_s: float = 0.35,
    blink_min_half_s: float = 0.04,
) -> bool | None:
    """Return whether the hold-warning blink phase is lit.

    ``None`` means the caller should show its normal (solid) colour — the hold
    has not yet reached ``blink_after_s``. After that, the blink accelerates
    until ``hold_s``.
    """
    if elapsed < blink_after_s:
        return None
    blink_elapsed = elapsed - blink_after_s
    blink_window = max(hold_s - blink_after_s, 0.001)
    progress = min(1.0, blink_elapsed / blink_window)
    half_period = max(
        blink_min_half_s,
        blink_start_half_s * (1.0 - progress) ** 1.5,
    )
    return int(blink_elapsed / half_period) % 2 == 0


_SOLID = {
    STATE_IDLE: LED_OFF,
    STATE_RECORDING: LED_RED,
    STATE_PLAYING: LED_GREEN,
    STATE_STOPPED: LED_YELLOW,
}

_BLINK = {
    STATE_IDLE: LED_OFF,
    STATE_RECORDING: LED_RED_BLINK,
    STATE_PLAYING: LED_GREEN_BLINK,
    STATE_STOPPED: LED_YELLOW_BLINK,
}

# Recording -> playing gets a four-phase sequence: OFF, RED, OFF, GREEN.
#
# Ableton has no "queued to stop recording" state — it shows triggered_to_play
# (green blink) and drops the fact that recording is still running. On a looper
# that matters: you are performing into that bar and need to know to keep
# playing. Alternating red/green says "recording -> playing" outright.
#
# The gaps demarcate the two colours; RED, GREEN, RED, GREEN with no break reads
# as one intense flicker rather than two distinct states. Reserved for this case
# only — it is visually noisy, and it earns that noise by confirming recording
# is STILL RUNNING, which the player needs. Everywhere else a plain blink is
# enough, because nothing of consequence is happening meanwhile.
RECORD_TO_PLAY = (LED_OFF, LED_RED, LED_OFF, LED_GREEN)


def led_for(
    sl_state: int,
    *,
    pending: str | None = None,
    tail_capture: bool = False,
) -> tuple[int, ...]:
    """Pad colour, as a blink sequence. Length 1 means hold it steady.

    Returning one shape for both cases keeps the caller from having to know
    which states animate — it just cycles whatever it is given.

    There is deliberately no separate "launch queued" input. A queued launch is
    simply an unconfirmed expectation of Playing, and giving it its own flag is
    what let a poll clear the flag while the launch was still pending, leaving
    the pad blinking green forever after it had already landed.
    """
    if tail_capture:
        # Stop-then-weld: length fixed; scratch tail + merge still running.
        # Same idiom as WAIT_STOP — recording is done but the take is not finished;
        # player must keep performing through the release tail.
        return RECORD_TO_PLAY
    if sl_state == SL_STATE_WAIT_STOP:
        return RECORD_TO_PLAY
    if sl_state == SL_STATE_WAIT_START:
        return (LED_RED_BLINK,)  # queued to record
    if pending is not None and derive_state(sl_state) != pending:
        # Asked for, not yet confirmed. This covers the two states the old bench
        # could not show at all: a quantized mute waiting for the bar, and a
        # launch that will start on the next one. Plain blink of the
        # destination colour — "it is going to play" is the whole message.
        return (_BLINK[pending],)
    return (_SOLID[derive_state(sl_state)],)
