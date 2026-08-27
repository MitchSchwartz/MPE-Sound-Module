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

import time
from typing import Callable

from apc_grid import GRID_ROWS, GridView
from led_table import LED_OFF, LED_RED, SCENE_LED_OFF, SCENE_LED_ON
from sl_loop_states import ACTIVE_PLAY, SL_STATE_MUTE, SL_STATE_OFF, SL_STATE_PAUSED
from slot_leds import matrix_messages
from slot_matrix import ACT_NOOP, PENDING_LAUNCH, PENDING_STOP, PENDING_SWITCH
from slot_matrix import plan_scene_press, row_is_fully_playing
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
        scene_launch_notes: tuple[int, ...] = (),
        hold_s: float = 2.0,
        hold_blink_start_s: float = 0.5,
        log: Callable[[str], None] | None = None,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._rt = runtime
        self._view = view
        self._midi_out = midi_out
        self._num_tracks = num_tracks
        self._scene_launch_notes = scene_launch_notes
        self._hold_s = max(hold_s, 0.001)
        self._hold_blink_start_s = max(hold_blink_start_s, 0.0)
        self._log = log or (lambda _m: None)
        self._now = now
        self._painted: dict[int, int] | None = None
        self._scene_painted: dict[int, int] = {}
        self._sl_states: dict[int, int] = {}
        self._pad_down_note: int | None = None
        self._pad_down_at: float | None = None
        self._hold_fired = False

    # -- input ------------------------------------------------------------

    def handles(self, note: int) -> bool:
        return self._view.cell_for_note(note) is not None

    def note_down(self, note: int) -> bool:
        """Pad down — tap gesture. True when this surface consumed it."""
        if not self.handles(note):
            return False
        self._pad_down_note = note
        self._pad_down_at = self._now()
        self._hold_fired = False
        self.press(note)
        return True

    def note_up(self, note: int) -> bool:
        """Pad up — ends the hold timer without a second tap."""
        if note != self._pad_down_note:
            return False
        self._pad_down_note = None
        self._pad_down_at = None
        self.repaint()
        return True

    def press(self, note: int, *, hold: bool = False) -> bool:
        """Dispatch a cell press (tap or hold-clear). True when consumed."""
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
        self.repaint_scenes()
        return True

    def scene_press(self, row: int) -> None:
        """Toggle slot row `row` across every track (Scene Launch 1–7)."""
        if not 0 <= row < GRID_ROWS:
            return
        plans = plan_scene_press(
            self._rt.tracks(), row, sl_states=self._sl_states
        )
        for plan in plans:
            self._rt.dispatch(plan)
        if plans:
            self._log(f"scene row {row + 1}: {len(plans)} track(s)")
        self.repaint()
        self.repaint_scenes()

    def poll_hold(self) -> None:
        """Long-press clear — same hold_ms as the single-clip footswitch."""
        if self._pad_down_note is None or self._hold_fired or self._pad_down_at is None:
            return
        if (self._now() - self._pad_down_at) < self._hold_s:
            return
        note = self._pad_down_note
        self._hold_fired = True
        self._pad_down_note = None
        self._pad_down_at = None
        self.press(note, hold=True)

    def poll_hold_led(self) -> None:
        """Warn before hold-clear fires — red on the held pad."""
        if self._pad_down_note is None or self._hold_fired or self._pad_down_at is None:
            return
        elapsed = self._now() - self._pad_down_at
        if elapsed < self._hold_blink_start_s:
            return
        note = self._pad_down_note
        vel = LED_RED if int(elapsed * 4) % 2 == 0 else LED_OFF
        self._midi_out.send_message([0x90, note, vel])

    # -- engine feedback --------------------------------------------------

    def on_state(self, track: int, sl_state: int) -> None:
        """An engine state update. Resolves a pending when it has come true."""
        self._sl_states[track] = int(sl_state)
        self._maybe_resolve(track, int(sl_state))
        self.repaint()
        self.repaint_scenes()

    def _maybe_resolve(self, track_index: int, sl_state: int) -> None:
        track = self._rt.track(track_index)
        pending = track.pending
        if pending is None:
            return
        if pending.kind == PENDING_STOP:
            arrived = sl_state in SILENT
        elif pending.kind in (PENDING_LAUNCH, PENDING_SWITCH):
            arrived = sl_state in ACTIVE_PLAY
        else:
            arrived = False
        if arrived:
            self._rt.boundary(track_index)

    def reset(self) -> None:
        """After Shift+Stop All long — match engine cleared, dark surface."""
        self._rt.reset()
        self._sl_states.clear()
        self._pad_down_note = None
        self._pad_down_at = None
        self._hold_fired = False
        self.blank()
        self.repaint_scenes(force=True)

    # -- output -----------------------------------------------------------

    def set_view(self, view: GridView) -> None:
        """Bank change: the notes now address different tracks, so the next
        paint must be full rather than a diff against the old bank."""
        self._view = view
        self._painted = None
        self.repaint()
        self.repaint_scenes(force=True)

    def repaint(self) -> None:
        messages, painted = matrix_messages(
            self._view, self._rt.tracks(), self._sl_states, previous=self._painted
        )
        for note, colour in messages:
            self._midi_out.send_message([0x90, note, colour])
        self._painted = painted

    def repaint_scenes(self, *, force: bool = False) -> None:
        """Scene Launch 1–7 LEDs — lit when the row is not fully playing."""
        if not self._scene_launch_notes:
            return
        desired: dict[int, int] = {}
        for index, note in enumerate(self._scene_launch_notes):
            row = index
            fully = row_is_fully_playing(
                self._rt.tracks(), row, sl_states=self._sl_states
            )
            desired[note] = SCENE_LED_OFF if fully else SCENE_LED_ON
        if force:
            to_send = sorted(desired.items())
        else:
            to_send = [
                (n, v) for n, v in sorted(desired.items())
                if self._scene_painted.get(n) != v
            ]
        for note, vel in to_send:
            self._midi_out.send_message([0x90, note, vel])
        self._scene_painted = desired

    def blank(self) -> None:
        """Darken the whole surface — on shutdown, or before handing the pads
        back to the single-clip layer."""
        for row in range(GRID_ROWS):
            for col in range(8):
                self._midi_out.send_message([0x90, row * 8 + col, LED_OFF])
        self._painted = None
