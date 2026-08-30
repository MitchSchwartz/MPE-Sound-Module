"""Colour for every cell in the matrix — pure, so it can be tested exhaustively.

Ableton's Session View reading, on the three colours an APC mini has:

    off      the slot is empty — nothing recorded here
    yellow   the slot holds a clip that is not sounding
    green    the slot is the active one and its track is playing
    red      the slot is recording
    blink    what this cell is *about to* become at the next boundary

The rule the whole surface rests on: **at most one non-off cell per column may
be green.** One buffer per track means one audible clip per track, so two green
cells in a column would be showing something the engine cannot do — and a
player reads a column at a glance, not cell by cell.

`sl_state` is the engine's state for a track's loop, and it is meaningful only
for that track's **active** slot: the other slots have no buffer, so nothing
about the engine describes them. Passing a state per track rather than per cell
is what keeps that from being expressible.
"""

from __future__ import annotations

from apc_grid import GRID_COLS, GRID_ROWS, pad_note
from led_table import (
    LED_GREEN,
    LED_GREEN_BLINK,
    LED_OFF,
    LED_YELLOW,
    LED_YELLOW_BLINK,
)
from slot_matrix import PENDING_LAUNCH, PENDING_SWITCH, Track


def static_cell_led(track: Track, slot: int) -> int:
    """The colour for a cell the gesture does NOT own.

    Deliberately narrower than it used to be. This function once coloured the
    active slot too, from `sl_state` alone — and that could never be right,
    because the single-clip surface's colours are not a function of the current
    state: record-to-play is a timed red/green blink, a SEQUENCE, whose input
    (how long ago the take closed) is not in this signature. No amount of
    case-patching here could reproduce it, which is exactly how the missing
    ring-out blinker got shipped. The active cell's colour now comes from
    `TrackGesture` via `gesture_leds`, and there is no second opinion.

    What is left is what the gesture genuinely cannot know: the matrix's own
    pending blinks, and the yellow for a slot that holds audio but is not the
    one bound to the track's buffer. Neither depends on the engine state, so
    there is deliberately no `sl_state` parameter: `sl_state` describes the one
    loop bound to the buffer, and every cell this function still colours is by
    definition not that loop. An unused parameter here would be an open
    invitation to derive a colour from it again.
    """
    pending = track.pending

    # Pending first: what is about to happen outranks what is happening, because
    # the blink is the only feedback that a press was received at all. A press
    # that queues silently for two seconds reads as a dead pad.
    if pending is not None:
        if pending.kind == PENDING_SWITCH:
            if slot == pending.to_slot:
                return LED_GREEN_BLINK      # arriving
            if slot == pending.from_slot:
                return LED_YELLOW_BLINK     # leaving
        elif pending.kind == PENDING_LAUNCH and slot == pending.to_slot:
            return LED_GREEN_BLINK

    if not track.occupied(slot):
        return LED_OFF
    return LED_YELLOW                   # holds audio, not the one sounding


def matrix_colours(
    view,
    tracks: dict[int, Track],
    *,
    gesture_leds: dict[int, int] | None = None,
) -> dict[int, int]:
    """note -> colour for every visible pad. What the matrix *should* show.

    Total over the viewport, and nothing more. It used to also diff against the
    caller's `previous` map and return only the changed pads — the surface's
    private record of what it had painted. That record was one of four in the
    process, none of them at the wire, and it is what made the reconnect
    erasure permanent: a different writer darkened 56 pads, this diff saw no
    change, and fifty poll cycles sent nothing. The diff now happens once, in
    `led_compositor`, against what the device was actually told.

    It also used to take `sl_states` and never read it. `static_cell_led`'s
    docstring explains at length why it deliberately dropped that parameter —
    "an unused parameter here would be an open invitation to derive a colour
    from it again" — and this function kept one anyway.
    """
    # Column-major, because a column IS a track: the track lookup, its active
    # slot, its pending and its gesture colour are all per-column facts and
    # were being re-fetched once per cell — 64 lookups an iteration, at the
    # bench's ~485 Hz poll rate, to answer eight questions. `visible_cells`
    # already hands back the column, so the note comes from `pad_note(row, col)`
    # rather than from re-deriving the column out of the track index.
    leds = gesture_leds or {}
    desired: dict[int, int] = {}
    for col in range(GRID_COLS):
        track_index = view.track_for_pad(0, col)
        if track_index is None:
            continue
        track = tracks.get(track_index)
        if track is None:
            for row in range(GRID_ROWS):
                desired[pad_note(row, col)] = LED_OFF
            continue
        active = track.active_slot
        pending = track.pending
        # The active lane is the gesture's, unconditionally — including
        # when it has no entry yet, which reads as OFF and is correct: an
        # unpainted pad is dark. Falling back to a state-derived colour here
        # would quietly restore the second opinion this refactor removed.
        #
        # A pending is the one thing that outranks it: a pending switch's
        # outgoing slot IS the active slot, and the gesture has never heard
        # of the switch, so only the matrix can blink it.
        gesture_colour = leds.get(track_index, LED_OFF)
        for row in range(GRID_ROWS):
            if row == active and pending is None:
                desired[pad_note(row, col)] = gesture_colour
            else:
                desired[pad_note(row, col)] = static_cell_led(track, row)
    return desired
