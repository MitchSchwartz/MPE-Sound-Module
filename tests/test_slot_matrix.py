"""Slot matrix — the cell vocabulary and scene rows, per multi-clip spec rev 3."""

from __future__ import annotations

from tests import conftest  # noqa: F401 — bare sooperlooper imports

import unittest

from scripts.sooperlooper.slot_matrix import (
    ACT_CANCEL,
    ACT_CLEAR,
    ACT_LAUNCH,
    ACT_NOOP,
    ACT_RECORD,
    ACT_STOP,
    ACT_SWITCH,
    NUM_SLOTS,
    NUM_TRACKS,
    PENDING_LAUNCH,
    PENDING_STOP,
    PENDING_SWITCH,
    Pending,
    Slot,
    Track,
    apply_pending,
    occupied_cells,
    plan_cell_press,
    plan_scene_press,
    resolve_at_boundary,
    row_is_fully_playing,
)
from scripts.sooperlooper.sl_loop_states import (
    SL_STATE_MUTE,
    SL_STATE_OFF,
    SL_STATE_OVERDUBBING,
    SL_STATE_PLAYING,
)


def clip(name="a.wav", *, dirty=False) -> Slot:
    return Slot(file=name, len_s=2.0, dirty=dirty)


def track(**kw) -> Track:
    slots = list(kw.pop("slots", [None] * NUM_SLOTS))
    return Track(slots=tuple(slots), **kw)


def press(tr, slot, *, sl_state=SL_STATE_OFF, index=0, hold=False):
    return plan_cell_press(
        track_index=index, track=tr, slot=slot, sl_state=sl_state, hold=hold
    )


class GeometryTests(unittest.TestCase):
    def test_fifteen_contiguous_tracks_the_engine_ceiling(self) -> None:
        """15, not 16. SooperLooper 1.7.9 stops at index 14 — index 15 answers
        reads with defaults and discards writes, so a 16th track looks present
        and behaves unlike every other one. Measured 2026-08-27; see
        sl_limits.py."""
        """rev 3: the seam-weld scratch loop is gone, so nothing is reserved."""
        self.assertEqual(NUM_TRACKS, 15)
        self.assertEqual(NUM_SLOTS, 8)

    def test_a_track_always_has_eight_slots(self) -> None:
        self.assertEqual(len(Track().slots), NUM_SLOTS)

    def test_out_of_range_slot_is_a_noop_not_a_crash(self) -> None:
        for bad in (-1, NUM_SLOTS, 99):
            self.assertEqual(press(track(), bad).action, ACT_NOOP)


class EmptyCellTests(unittest.TestCase):
    def test_empty_slot_arms_record(self) -> None:
        p = press(track(), 0)
        self.assertEqual(p.action, ACT_RECORD)
        self.assertFalse(p.save_first, "nothing loaded — nothing to flush")

    def test_record_into_empty_slot_flushes_a_dirty_active_slot(self) -> None:
        """One buffer per track: arming reuses it, so unflushed audio dies."""
        slots = [clip(dirty=True)] + [None] * 7
        p = press(track(slots=slots, active_slot=0), 3)
        self.assertEqual(p.action, ACT_RECORD)
        self.assertTrue(p.save_first)
        self.assertEqual(p.from_slot, 0)

    def test_record_does_not_flush_a_clean_active_slot(self) -> None:
        slots = [clip(dirty=False)] + [None] * 7
        p = press(track(slots=slots, active_slot=0), 3)
        self.assertFalse(p.save_first, "already on disk")


class OccupiedCellTests(unittest.TestCase):
    def test_tapping_the_playing_active_slot_queues_a_stop(self) -> None:
        slots = [clip()] + [None] * 7
        p = press(track(slots=slots, active_slot=0), 0, sl_state=SL_STATE_PLAYING)
        self.assertEqual(p.action, ACT_STOP)
        self.assertEqual(p.from_slot, 0)

    def test_tapping_the_muted_active_slot_relaunches_it(self) -> None:
        slots = [clip()] + [None] * 7
        p = press(track(slots=slots, active_slot=0), 0, sl_state=SL_STATE_MUTE)
        self.assertEqual(p.action, ACT_LAUNCH)

    def test_overdubbing_counts_as_playing(self) -> None:
        """A take closing into its ring-out overdub is sounding, so the pad
        means stop — not relaunch (rev 3 / OPEN-4)."""
        slots = [clip()] + [None] * 7
        p = press(track(slots=slots, active_slot=0), 0, sl_state=SL_STATE_OVERDUBBING)
        self.assertEqual(p.action, ACT_STOP)

    def test_launch_when_the_track_has_nothing_active(self) -> None:
        slots = [None, clip()] + [None] * 6
        p = press(track(slots=slots, active_slot=None), 1)
        self.assertEqual(p.action, ACT_LAUNCH)
        self.assertIsNone(p.from_slot)

    def test_other_occupied_slot_is_a_switch_not_a_layer(self) -> None:
        slots = [clip("a.wav"), clip("b.wav")] + [None] * 6
        p = press(track(slots=slots, active_slot=0), 1, sl_state=SL_STATE_PLAYING)
        self.assertEqual(p.action, ACT_SWITCH)
        self.assertEqual(p.from_slot, 0)
        self.assertEqual(p.slot, 1)

    def test_switch_flushes_a_dirty_outgoing_slot(self) -> None:
        slots = [clip("a.wav", dirty=True), clip("b.wav")] + [None] * 6
        p = press(track(slots=slots, active_slot=0), 1, sl_state=SL_STATE_PLAYING)
        self.assertTrue(p.save_first, "the outgoing buffer is about to be reused")

    def test_hold_clears_an_occupied_slot(self) -> None:
        slots = [clip()] + [None] * 7
        self.assertEqual(press(track(slots=slots), 0, hold=True).action, ACT_CLEAR)

    def test_hold_on_empty_does_nothing(self) -> None:
        self.assertEqual(press(track(), 0, hold=True).action, ACT_NOOP)


class CancelTests(unittest.TestCase):
    """Cancel is a re-tap of the slot that OWNS the pending action."""

    def test_retap_outgoing_cancels_a_pending_stop(self) -> None:
        slots = [clip()] + [None] * 7
        tr = track(slots=slots, active_slot=0,
                   pending=Pending(PENDING_STOP, from_slot=0))
        p = press(tr, 0, sl_state=SL_STATE_PLAYING)
        self.assertEqual(p.action, ACT_CANCEL)
        self.assertTrue(p.clear_pending)

    def test_retap_outgoing_cancels_a_pending_switch(self) -> None:
        slots = [clip("a.wav"), clip("b.wav")] + [None] * 6
        tr = track(slots=slots, active_slot=0,
                   pending=Pending(PENDING_SWITCH, from_slot=0, to_slot=1))
        p = press(tr, 0, sl_state=SL_STATE_PLAYING)
        self.assertEqual(p.action, ACT_CANCEL)

    def test_pending_launch_is_cancelled_by_the_incoming_slot(self) -> None:
        """rev 2 correction: with nothing playing there is no outgoing slot,
        so 're-tap the outgoing slot' is undefined."""
        slots = [None, clip()] + [None] * 6
        tr = track(slots=slots, active_slot=None,
                   pending=Pending(PENDING_LAUNCH, to_slot=1))
        p = press(tr, 1)
        self.assertEqual(p.action, ACT_CANCEL)

    def test_pressing_a_different_slot_does_not_cancel(self) -> None:
        slots = [clip("a.wav"), clip("b.wav"), clip("c.wav")] + [None] * 5
        tr = track(slots=slots, active_slot=0,
                   pending=Pending(PENDING_SWITCH, from_slot=0, to_slot=1))
        p = press(tr, 2, sl_state=SL_STATE_PLAYING)
        self.assertEqual(p.action, ACT_SWITCH, "replaces the pending switch")
        self.assertFalse(p.clear_pending)


class BookkeepingTests(unittest.TestCase):
    def test_dispatching_a_switch_records_one_pending(self) -> None:
        slots = [clip("a.wav"), clip("b.wav")] + [None] * 6
        tr = track(slots=slots, active_slot=0)
        p = press(tr, 1, sl_state=SL_STATE_PLAYING)
        tr = apply_pending(tr, p)
        self.assertEqual(tr.pending, Pending(PENDING_SWITCH, from_slot=0, to_slot=1))

    def test_boundary_makes_the_incoming_slot_active(self) -> None:
        slots = [clip("a.wav"), clip("b.wav")] + [None] * 6
        tr = track(slots=slots, active_slot=0,
                   pending=Pending(PENDING_SWITCH, from_slot=0, to_slot=1))
        tr = resolve_at_boundary(tr)
        self.assertEqual(tr.active_slot, 1)
        self.assertIsNone(tr.pending)

    def test_boundary_on_a_stop_keeps_the_slot_active(self) -> None:
        """Stopped is not unloaded — the buffer still holds that slot."""
        slots = [clip()] + [None] * 7
        tr = track(slots=slots, active_slot=0,
                   pending=Pending(PENDING_STOP, from_slot=0))
        tr = resolve_at_boundary(tr)
        self.assertEqual(tr.active_slot, 0)
        self.assertIsNone(tr.pending)

    def test_cancel_clears_pending_without_moving_the_active_slot(self) -> None:
        slots = [clip("a.wav"), clip("b.wav")] + [None] * 6
        tr = track(slots=slots, active_slot=0,
                   pending=Pending(PENDING_SWITCH, from_slot=0, to_slot=1))
        tr = apply_pending(tr, press(tr, 0, sl_state=SL_STATE_PLAYING))
        self.assertIsNone(tr.pending)
        self.assertEqual(tr.active_slot, 0)

    def test_plan_alone_does_not_move_the_matrix(self) -> None:
        slots = [clip("a.wav"), clip("b.wav")] + [None] * 6
        tr = track(slots=slots, active_slot=0)
        press(tr, 1, sl_state=SL_STATE_PLAYING)
        self.assertIsNone(tr.pending, "planning must be free of side effects")


class SceneRowTests(unittest.TestCase):
    def _grid(self):
        return {
            0: track(slots=[clip("a0.wav")] + [None] * 7, active_slot=0),
            1: track(slots=[clip("a1.wav")] + [None] * 7, active_slot=0),
            2: track(),  # empty column
        }

    def test_empty_columns_do_not_keep_the_row_lit(self) -> None:
        """Counting empties would leave every scene button permanently lit."""
        states = {0: SL_STATE_PLAYING, 1: SL_STATE_PLAYING, 2: SL_STATE_OFF}
        self.assertTrue(row_is_fully_playing(self._grid(), 0, sl_states=states))

    def test_a_row_with_one_stopped_cell_is_not_fully_playing(self) -> None:
        states = {0: SL_STATE_PLAYING, 1: SL_STATE_MUTE, 2: SL_STATE_OFF}
        self.assertFalse(row_is_fully_playing(self._grid(), 0, sl_states=states))

    def test_a_row_with_no_occupied_cells_is_not_fully_playing(self) -> None:
        """Nothing to stop — the button must not read as an active row."""
        self.assertFalse(row_is_fully_playing({0: track()}, 0, sl_states={0: 0}))

    def test_an_empty_row_scene_led_is_off(self) -> None:
        from slot_matrix import scene_row_led_on

        self.assertFalse(scene_row_led_on({0: track()}, 0, sl_states={0: 0}))

    def test_lit_row_launches_only_the_cells_that_are_not_playing(self) -> None:
        states = {0: SL_STATE_PLAYING, 1: SL_STATE_MUTE, 2: SL_STATE_OFF}
        plans = plan_scene_press(self._grid(), 0, sl_states=states)
        self.assertEqual([(p.track, p.action) for p in plans], [(1, ACT_LAUNCH)])

    def test_dark_row_stops_every_playing_cell(self) -> None:
        states = {0: SL_STATE_PLAYING, 1: SL_STATE_PLAYING, 2: SL_STATE_OFF}
        plans = plan_scene_press(self._grid(), 0, sl_states=states)
        self.assertEqual([(p.track, p.action) for p in plans],
                         [(0, ACT_STOP), (1, ACT_STOP)])

    def test_scene_reaches_tracks_banked_off_screen(self) -> None:
        """The viewport must not change what the gesture means."""
        grid = {i: track(slots=[clip(f"a{i}.wav")] + [None] * 7, active_slot=None)
                for i in range(NUM_TRACKS)}
        plans = plan_scene_press(grid, 0, sl_states={i: SL_STATE_OFF for i in range(NUM_TRACKS)})
        self.assertEqual(len(plans), NUM_TRACKS)
        self.assertEqual(sorted(p.track for p in plans), list(range(NUM_TRACKS)))

    def test_scene_skips_tracks_with_that_slot_empty(self) -> None:
        grid = {0: track(slots=[clip()] + [None] * 7), 1: track()}
        plans = plan_scene_press(grid, 0, sl_states={0: SL_STATE_OFF, 1: SL_STATE_OFF})
        self.assertEqual([p.track for p in plans], [0])


class InventoryTests(unittest.TestCase):
    def test_occupied_cells_lists_every_clip(self) -> None:
        grid = {
            0: track(slots=[clip(), None, clip()] + [None] * 5),
            1: track(),
            2: track(slots=[None] * 7 + [clip()]),
        }
        self.assertEqual(occupied_cells(grid), [(0, 0), (0, 2), (2, 7)])
