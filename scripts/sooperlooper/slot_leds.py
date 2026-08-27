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

from led_table import (
    LED_GREEN,
    LED_GREEN_BLINK,
    LED_OFF,
    LED_RED,
    LED_RED_BLINK,
    LED_YELLOW,
    LED_YELLOW_BLINK,
)
from sl_loop_states import (
    ACTIVE_PLAY,
    ACTIVE_RECORD,
    SL_STATE_RECORDING,
    SL_STATE_WAIT_START,
)
from slot_matrix import PENDING_LAUNCH, PENDING_STOP, PENDING_SWITCH, Track


def cell_led(track: Track, slot: int, *, sl_state: int) -> int:
    """The colour this cell should show right now."""
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
        elif pending.kind == PENDING_STOP and slot == pending.from_slot:
            return LED_YELLOW_BLINK

    if not track.occupied(slot):
        if slot == track.active_slot:
            if sl_state in ACTIVE_RECORD or sl_state == SL_STATE_RECORDING:
                return LED_RED if sl_state == SL_STATE_RECORDING else LED_RED_BLINK
            if sl_state == SL_STATE_WAIT_START:
                return LED_RED_BLINK
        return LED_OFF

    if slot != track.active_slot:
        return LED_YELLOW               # holds audio, not the one sounding

    if sl_state == SL_STATE_RECORDING:
        return LED_RED
    if sl_state == SL_STATE_WAIT_START:
        return LED_RED_BLINK
    if sl_state in ACTIVE_PLAY:
        return LED_GREEN
    return LED_YELLOW                   # loaded but stopped or muted


def column_leds(track: Track, *, sl_state: int, num_slots: int = 8) -> list[int]:
    """Colours for one column, bottom row first."""
    return [cell_led(track, slot, sl_state=sl_state) for slot in range(num_slots)]


def matrix_messages(
    view,
    tracks: dict[int, Track],
    sl_states: dict[int, int],
    *,
    previous: dict[int, int] | None = None,
) -> tuple[list[tuple[int, int]], dict[int, int]]:
    """(note, colour) for the visible matrix, plus the state to pass in next time.

    Only changed pads are returned. The APC is on a 31.25 kbaud MIDI link and a
    full 64-pad repaint is ~192 bytes, about 60 ms of wire time — enough, at a
    poll rate, to delay the pad presses arriving on the same cable. Diffing
    keeps a steady surface silent.

    Pass `previous=None` after a bank change to force a full repaint: the notes
    have been reassigned to different tracks, so a diff against the old bank
    would leave pads showing the previous one.
    """
    desired: dict[int, int] = {}
    for row, col, track_index in view.visible_cells():
        note = view.note_for_cell(track_index, row)
        if note is None:
            continue
        track = tracks.get(track_index)
        if track is None:
            desired[note] = LED_OFF
            continue
        desired[note] = cell_led(
            track, row, sl_state=sl_states.get(track_index, 0)
        )
    if previous is None:
        return sorted(desired.items()), desired
    changed = [(n, c) for n, c in sorted(desired.items()) if previous.get(n) != c]
    return changed, desired
