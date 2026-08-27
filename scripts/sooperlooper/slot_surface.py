"""The matrix as one object the bench can delegate to.

Joins the three pure layers — `apc_grid` (which pad is which cell),
`slot_matrix` / `slot_runtime` (what a press means and how to do it), and
`slot_leds` (what the surface should show) — and owns the one piece of state
none of them can: **when a queued action has actually happened.**

Resolving a pending is not a timer. SooperLooper defers the unmute to its own
quantize boundary, and the only honest signal that the boundary arrived is the
engine reporting the state the pending was aiming at. Guessing from a bar clock
would drift against the engine, and the drift would show as a pad that goes
solid a beat before the audio does — which reads as the surface lying.

One owner for the whole 8x8 surface, deliberately. Splitting it — row 0 to the
existing footswitch, rows 1-7 here — would give one track's single buffer two
controllers with separate ideas of what is loaded, and they would disagree
silently.
"""

from __future__ import annotations

from typing import Callable

from apc_grid import GRID_ROWS, GridView
from led_table import LED_OFF
from sl_loop_states import ACTIVE_PLAY, SL_STATE_MUTE, SL_STATE_OFF, SL_STATE_PAUSED
from slot_leds import matrix_messages
from slot_matrix import ACT_NOOP, PENDING_LAUNCH, PENDING_STOP, PENDING_SWITCH
from slot_runtime import SlotRuntime

SILENT = (SL_STATE_MUTE, SL_STATE_PAUSED, SL_STATE_OFF)


class SlotSurface:
    def __init__(
        self,
        *,
        runtime: SlotRuntime,
        view: GridView,
        midi_out,
        num_tracks: int,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._rt = runtime
        self._view = view
        self._midi_out = midi_out
        self._num_tracks = num_tracks
        self._log = log or (lambda _m: None)
        self._painted: dict[int, int] | None = None
        self._sl_states: dict[int, int] = {}

    # -- input ------------------------------------------------------------

    def handles(self, note: int) -> bool:
        return self._view.cell_for_note(note) is not None

    def press(self, note: int, *, hold: bool = False) -> bool:
        """Dispatch a pad press. True when this surface consumed it."""
        cell = self._view.cell_for_note(note)
        if cell is None:
            return False
        track, slot = cell
        plan = self._rt.press(
            track, slot, sl_state=self._sl_states.get(track, SL_STATE_OFF), hold=hold
        )
        if plan.action == ACT_NOOP and plan.note:
            self._log(f"track {track + 1} slot {slot + 1}: {plan.note}")
        self.repaint()
        return True

    # -- engine feedback --------------------------------------------------

    def on_state(self, track: int, sl_state: int) -> None:
        """An engine state update. Resolves a pending when it has come true."""
        self._sl_states[track] = int(sl_state)
        self._maybe_resolve(track, int(sl_state))
        self.repaint()

    def _maybe_resolve(self, track_index: int, sl_state: int) -> None:
        track = self._rt.track(track_index)
        pending = track.pending
        if pending is None:
            return
        # Aiming at silence, or aiming at sound. The engine reaching the target
        # state is the boundary, because SL only changes state when it acts.
        if pending.kind == PENDING_STOP:
            arrived = sl_state in SILENT
        elif pending.kind in (PENDING_LAUNCH, PENDING_SWITCH):
            arrived = sl_state in ACTIVE_PLAY
        else:
            arrived = False
        if arrived:
            self._rt.boundary(track_index)

    # -- output -----------------------------------------------------------

    def set_view(self, view: GridView) -> None:
        """Bank change: the notes now address different tracks, so the next
        paint must be full rather than a diff against the old bank."""
        self._view = view
        self._painted = None
        self.repaint()

    def repaint(self) -> None:
        messages, painted = matrix_messages(
            self._view, self._rt.tracks(), self._sl_states, previous=self._painted
        )
        for note, colour in messages:
            self._midi_out.send_message([0x90, note, colour])
        self._painted = painted

    def blank(self) -> None:
        """Darken the whole surface — on shutdown, or before handing the pads
        back to the single-clip layer."""
        for row in range(GRID_ROWS):
            for col in range(8):
                self._midi_out.send_message([0x90, row * 8 + col, LED_OFF])
        self._painted = None
