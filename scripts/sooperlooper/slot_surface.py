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
    ACT_RECORD,
    PENDING_LAUNCH,
    PENDING_STOP,
    PENDING_SWITCH,
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

    def _acts_on_release(self, note: int) -> bool:
        """Does this pad wait for the finger to lift?

        An occupied slot that is NOT the track's active one does. Its press
        means launch-or-switch, and its long press means delete — so acting on
        pad-down loaded and played the clip before the hold could fire, and the
        player watched the take they were deleting start up first. The
        footswitch already lands mute and launch on release for the same
        reason; only record has to be immediate.
        """
        cell = self._view.cell_for_note(note)
        if cell is None or self._is_active_lane(note):
            return False
        track, slot = cell
        return self._rt.track(track).occupied(slot)

    def note_down(self, note: int) -> bool:
        if not self.handles(note):
            return False
        self._pad_down_note = note
        self._pad_down_at = self._now()
        self._hold_fired = False
        if not self._acts_on_release(note):
            self.press(note)
        return True

    def note_up(self, note: int) -> bool:
        if note != self._pad_down_note:
            return False
        if not self._hold_fired and self._acts_on_release(note):
            self.press(note)
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
        )
        if plan.action == ACT_NOOP and plan.note:
            self._log(f"track {track + 1} slot {slot + 1}: {plan.note}")
        if self._rt.needs_gesture(plan):
            fs.set_note(note)
            if plan.action == ACT_RECORD:
                # The runtime just muted and emptied this track's buffer for a
                # take on a different slot. Tell the footswitch, or it derives
                # `playing` from the last engine report and its gesture is a
                # mute instead of a record.
                fs.expect_cleared()
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
                    # A scene press is a complete tap. There is no pad to
                    # release, so the up has to be synthesised here: leaving
                    # the footswitch held would let its hold timer expire and
                    # fire long-press-to-clear on every track in the row, and
                    # the mute/unmute half of the gesture lands on the up.
                    fs.on_pad_down()
                    fs.on_pad_up()
        if plans:
            self._log(f"scene row {row + 1}: {len(plans)} track(s)")
        self._sync_footswitch_notes()
        self.repaint()
        self.repaint_scenes()

    def _is_active_lane(self, note: int) -> bool:
        """Is this pad the track's bound buffer (or a track with none)?

        The lane where the footswitch decides everything. Kept as one predicate
        so hold, press and LED routing cannot disagree about which lane a pad
        is in — three separate answers to that question is how the surface
        ended up with two hold implementations.
        """
        cell = self._view.cell_for_note(note)
        if cell is None:
            return False
        track, slot = cell
        tr = self._rt.track(track)
        # Must agree with plan_cell_press exactly. An OCCUPIED slot on an
        # unbound track is NOT the lane — the matrix launches it from disk —
        # so routing its hold or its LED to the footswitch would have the
        # surface and the planner disagreeing about who owns the same pad.
        return tr.active_slot == slot or (
            tr.active_slot is None and not tr.occupied(slot)
        )

    def poll_hold(self) -> None:
        if self._pad_down_note is None or self._hold_fired or self._pad_down_at is None:
            return
        # Active lane: the footswitch owns hold-to-clear, blink and all. Its
        # own poll_hold is skipped under multigrid (see poll_footswitches), so
        # drive it here rather than reimplementing the timing — a second hold
        # implementation fired at a different moment and painted a different
        # blink, which is what the equivalence test caught.
        if self._is_active_lane(self._pad_down_note):
            cell = self._view.cell_for_note(self._pad_down_note)
            fs = self._fs.get(cell[0]) if cell else None
            if fs is not None:
                fs.poll_hold()
                if fs.hold_fired:
                    # The footswitch cleared the engine. The clip on disk is
                    # ours, and nothing else will remove it.
                    self._rt.forget_active_slot(cell[0])
                    self._hold_fired = True
                    self._pad_down_note = None
                    self._pad_down_at = None
                    self.repaint()
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
        # Active lane: the footswitch's own hold blink reaches the surface
        # through current_led(), so painting here as well would fight it.
        if self._is_active_lane(self._pad_down_note):
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

    def repaint(self, *, force: bool = False) -> None:
        """Paint changed pads. `force` repaints every one.

        Needed after the APC re-enumerates: the device comes back dark, so the
        diff cache describes a surface that no longer exists and a normal
        repaint would send nothing at all.
        """
        messages, painted = matrix_messages(
            self._view,
            self._rt.tracks(),
            self._sl_states,
            previous=None if force else self._painted,
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
