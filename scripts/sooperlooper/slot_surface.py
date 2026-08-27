"""The matrix as one object the bench can delegate to.

Slot **occupancy** and switch/stop pending live in ``SlotRuntime``. Record,
close, stop, ring-out, quantize, and active-slot LED timing live in the
existing ``LoopFootswitch`` for each track — one gesture brain per column.
"""

from __future__ import annotations

import time
from typing import Callable

from apc_grid import GRID_ROWS, GridView
from apc_transport import scene_launch_index_to_row
from led_table import LED_OFF, LED_RED, SCENE_LED_OFF, SCENE_LED_ON
from sl_loop_states import (
    ACTIVE_PLAY,
    ACTIVE_RECORD,
    SL_STATE_MUTE,
    SL_STATE_OFF,
    SL_STATE_OVERDUBBING,
    SL_STATE_PAUSED,
    SL_STATE_PLAYING,
    SL_STATE_RECORDING,
)
from slot_leds import matrix_messages
from slot_matrix import (
    ACT_NOOP,
    PENDING_LAUNCH,
    PENDING_STOP,
    PENDING_SWITCH,
    PHASE_ARMING,
    PHASE_CLOSING,
    PHASE_IDLE,
    PHASE_RECORDING,
    plan_scene_press,
    scene_row_led_on,
)
from slot_runtime import SlotRuntime

if False:  # TYPE_CHECKING — avoid import cycle at runtime
    from apc_footswitch import LoopFootswitch

MIN_TAKE_LEN_S = 0.01
SILENT = (SL_STATE_MUTE, SL_STATE_PAUSED, SL_STATE_OFF)


class SlotSurface:
    def __init__(
        self,
        *,
        runtime: SlotRuntime,
        footswitches_by_loop: dict[int, "LoopFootswitch"],
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
        self._fs = footswitches_by_loop
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
        self._loop_lens: dict[int, float] = {}
        self._pad_down_note: int | None = None
        self._pad_down_at: float | None = None
        self._hold_fired = False

    # -- input ------------------------------------------------------------

    def handles(self, note: int) -> bool:
        return self._view.cell_for_note(note) is not None

    def note_down(self, note: int) -> bool:
        if not self.handles(note):
            return False
        self._pad_down_note = note
        self._pad_down_at = self._now()
        self._hold_fired = False
        self.press(note)
        return True

    def note_up(self, note: int) -> bool:
        if note != self._pad_down_note:
            return False
        cell = self._view.cell_for_note(note)
        if cell is not None:
            track, _slot = cell
            fs = self._fs.get(track)
            if fs is not None:
                fs.on_pad_up()
        self._pad_down_note = None
        self._pad_down_at = None
        self.repaint()
        return True

    def press(self, note: int, *, hold: bool = False) -> bool:
        cell = self._view.cell_for_note(note)
        if cell is None:
            return False
        track, slot = cell
        fs = self._fs.get(track)
        if fs is None:
            return False
        sl_state = fs.sl_state
        plan = self._rt.press(
            track,
            slot,
            sl_state=sl_state,
            hold=hold,
            record_phase=self._record_phase(track, slot, fs),
        )
        if plan.action == ACT_NOOP and plan.note:
            self._log(f"track {track + 1} slot {slot + 1}: {plan.note}")
        if self._rt.needs_gesture(plan):
            fs.set_note(note)
            fs.on_pad_down()
        self._sync_footswitch_notes()
        self.repaint()
        self.repaint_scenes()
        return True

    def scene_press(self, row: int) -> None:
        if not 0 <= row < GRID_ROWS:
            return
        plans = plan_scene_press(
            self._rt.tracks(), row, sl_states=self._sl_states
        )
        for plan in plans:
            executed = self._rt.dispatch(
                plan, sl_state=self._sl_states.get(plan.track, SL_STATE_OFF)
            )
            if self._rt.needs_gesture(executed):
                fs = self._fs.get(plan.track)
                note = self._view.note_for_cell(plan.track, plan.slot)
                if fs is not None and note is not None:
                    fs.set_note(note)
                    fs.on_pad_down()
        if plans:
            self._log(f"scene row {row + 1}: {len(plans)} track(s)")
        self._sync_footswitch_notes()
        self.repaint()
        self.repaint_scenes()

    def poll_hold(self) -> None:
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
        if self._pad_down_note is None or self._hold_fired or self._pad_down_at is None:
            return
        elapsed = self._now() - self._pad_down_at
        if elapsed < self._hold_blink_start_s:
            return
        note = self._pad_down_note
        vel = LED_RED if int(elapsed * 4) % 2 == 0 else LED_OFF
        self._midi_out.send_message([0x90, note, vel])

    def poll_led_repaint(self) -> None:
        """Advance footswitch blink phase and repaint if needed."""
        self.repaint()

    # -- engine feedback --------------------------------------------------

    def on_state(self, track: int, sl_state: int) -> None:
        self._sl_states[track] = int(sl_state)
        self._maybe_mark_recorded(track, int(sl_state))
        self._maybe_resolve(track, int(sl_state))
        self._sync_footswitch_notes()
        self.repaint()
        self.repaint_scenes()

    def on_loop_len(self, track: int, loop_len: float) -> None:
        if loop_len > 0:
            self._loop_lens[track] = float(loop_len)
        sl_state = self._sl_states.get(track, SL_STATE_OFF)
        if sl_state == SL_STATE_PLAYING:
            self._maybe_mark_recorded(track, sl_state)
        self.repaint()
        self.repaint_scenes()

    def _maybe_mark_recorded(self, track: int, sl_state: int) -> None:
        if sl_state != SL_STATE_PLAYING:
            return
        row = self._rt.track(track)
        active = row.active_slot
        loop_len = self._loop_lens.get(track, 0.0)
        if (
            active is not None
            and not row.occupied(active)
            and loop_len >= MIN_TAKE_LEN_S
        ):
            self._rt.mark_recorded(
                track, active, len_s=loop_len, sl_state=sl_state
            )
            self._log(
                f"track {track + 1} slot {active + 1}: take landed "
                f"({loop_len:.2f}s)"
            )

    def _record_phase(self, track: int, slot: int, fs) -> str:
        if self._rt.track(track).active_slot != slot:
            return PHASE_IDLE
        if fs.sl_state == SL_STATE_OVERDUBBING:
            return PHASE_CLOSING
        if fs.sl_state in ACTIVE_RECORD:
            return (
                PHASE_RECORDING
                if fs.sl_state == SL_STATE_RECORDING
                else PHASE_ARMING
            )
        return PHASE_IDLE

    def _sync_footswitch_notes(self) -> None:
        for track_index in self._view.visible_loops():
            fs = self._fs.get(track_index)
            if fs is None:
                continue
            active = self._rt.track(track_index).active_slot
            if active is None:
                fs.set_note(None)
            else:
                fs.set_note(self._view.note_for_cell(track_index, active))

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
        self._rt.reset()
        self._sl_states.clear()
        self._loop_lens.clear()
        self._pad_down_note = None
        self._pad_down_at = None
        self._hold_fired = False
        self.blank()
        self.repaint_scenes(force=True)

    # -- output -----------------------------------------------------------

    def set_view(self, view: GridView) -> None:
        self._view = view
        self._painted = None
        self._sync_footswitch_notes()
        self.repaint()
        self.repaint_scenes(force=True)

    def _footswitch_leds(self) -> dict[int, int]:
        out: dict[int, int] = {}
        for track_index in self._view.visible_loops():
            fs = self._fs.get(track_index)
            if fs is not None:
                out[track_index] = fs.current_led()
        return out

    def repaint(self) -> None:
        messages, painted = matrix_messages(
            self._view,
            self._rt.tracks(),
            self._sl_states,
            previous=self._painted,
            footswitch_leds=self._footswitch_leds(),
        )
        for note, colour in messages:
            self._midi_out.send_message([0x90, note, colour])
        self._painted = painted

    def repaint_scenes(self, *, force: bool = False) -> None:
        if not self._scene_launch_notes:
            return
        desired: dict[int, int] = {}
        for index, note in enumerate(self._scene_launch_notes):
            row = scene_launch_index_to_row(index)
            lit = scene_row_led_on(
                self._rt.tracks(), row, sl_states=self._sl_states
            )
            desired[note] = SCENE_LED_ON if lit else SCENE_LED_OFF
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
        for row in range(GRID_ROWS):
            for col in range(8):
                self._midi_out.send_message([0x90, row * 8 + col, LED_OFF])
        self._painted = None
