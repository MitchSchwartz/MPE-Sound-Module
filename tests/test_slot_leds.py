"""Cell colours. The invariant worth protecting is one green per column."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "sooperlooper"))

from apc_grid import GridView  # noqa: E402
from led_table import (  # noqa: E402
    LED_GREEN,
    LED_GREEN_BLINK,
    LED_OFF,
    LED_RED,
    LED_RED_BLINK,
    LED_YELLOW,
    LED_YELLOW_BLINK,
)
from sl_loop_states import (  # noqa: E402
    SL_STATE_MUTE,
    SL_STATE_OFF,
    SL_STATE_OVERDUBBING,
    SL_STATE_PLAYING,
    SL_STATE_RECORDING,
    SL_STATE_WAIT_START,
)
from slot_leds import cell_led, column_leds, matrix_messages  # noqa: E402
from slot_matrix import Pending, Slot, Track  # noqa: E402


def track_with(active=None, occupied=(), pending=None) -> Track:
    slots = [Slot(f"s{i}.wav") if i in occupied else None for i in range(8)]
    return Track(slots=tuple(slots), active_slot=active, pending=pending)


class CellColourTests(unittest.TestCase):
    def test_empty_is_dark(self) -> None:
        self.assertEqual(cell_led(track_with(), 3, sl_state=SL_STATE_OFF), LED_OFF)

    def test_occupied_but_not_active_is_yellow(self) -> None:
        t = track_with(active=0, occupied=(0, 4))
        self.assertEqual(cell_led(t, 4, sl_state=SL_STATE_PLAYING), LED_YELLOW)

    def test_active_and_playing_is_green(self) -> None:
        t = track_with(active=0, occupied=(0,))
        self.assertEqual(cell_led(t, 0, sl_state=SL_STATE_PLAYING), LED_GREEN)

    def test_overdub_still_reads_as_playing(self) -> None:
        """The ring-out overdub runs for a full pass after a take. It is not a
        different thing to the player and must not change colour mid-pass."""
        t = track_with(active=0, occupied=(0,))
        self.assertEqual(cell_led(t, 0, sl_state=SL_STATE_OVERDUBBING), LED_GREEN)

    def test_recording_is_red(self) -> None:
        t = track_with(active=2, occupied=(2,))
        self.assertEqual(cell_led(t, 2, sl_state=SL_STATE_RECORDING), LED_RED)

    def test_an_empty_slot_queued_to_record_blinks_red(self) -> None:
        t = track_with(active=5)
        self.assertEqual(cell_led(t, 5, sl_state=SL_STATE_WAIT_START), LED_RED_BLINK)

    def test_loaded_but_muted_is_yellow_not_green(self) -> None:
        """Green means audible. A muted clip that reads green is the surface
        telling the player something is sounding when nothing is."""
        t = track_with(active=1, occupied=(1,))
        self.assertEqual(cell_led(t, 1, sl_state=SL_STATE_MUTE), LED_YELLOW)


class PendingTests(unittest.TestCase):
    def test_a_switch_blinks_both_ends(self) -> None:
        t = track_with(active=0, occupied=(0, 3),
                       pending=Pending("switch", to_slot=3, from_slot=0))
        self.assertEqual(cell_led(t, 3, sl_state=SL_STATE_PLAYING), LED_GREEN_BLINK)
        self.assertEqual(cell_led(t, 0, sl_state=SL_STATE_PLAYING), LED_YELLOW_BLINK)

    def test_a_pending_stop_blinks_the_outgoing_slot(self) -> None:
        t = track_with(active=2, occupied=(2,), pending=Pending("stop", from_slot=2))
        self.assertEqual(cell_led(t, 2, sl_state=SL_STATE_PLAYING), LED_YELLOW_BLINK)

    def test_pending_outranks_current_state(self) -> None:
        """The blink is the only confirmation a press was received. Without
        this precedence a queued press reads as a dead pad for a whole bar."""
        t = track_with(active=0, occupied=(0, 5),
                       pending=Pending("switch", to_slot=5, from_slot=0))
        self.assertEqual(cell_led(t, 0, sl_state=SL_STATE_PLAYING), LED_YELLOW_BLINK)

    def test_uninvolved_slots_ignore_the_pending(self) -> None:
        t = track_with(active=0, occupied=(0, 3, 6),
                       pending=Pending("switch", to_slot=3, from_slot=0))
        self.assertEqual(cell_led(t, 6, sl_state=SL_STATE_PLAYING), LED_YELLOW)


class ColumnInvariantTests(unittest.TestCase):
    def test_at_most_one_green_per_column(self) -> None:
        """One buffer per track means one audible clip. Two greens in a column
        would show something the engine cannot do."""
        states = (SL_STATE_OFF, SL_STATE_PLAYING, SL_STATE_RECORDING,
                  SL_STATE_OVERDUBBING, SL_STATE_MUTE, SL_STATE_WAIT_START)
        pendings = (None,
                    Pending("switch", to_slot=6, from_slot=1),
                    Pending("launch", to_slot=4),
                    Pending("stop", from_slot=1))
        for active in (None, 0, 1, 7):
            for pending in pendings:
                for state in states:
                    t = track_with(active=active, occupied=(0, 1, 4, 6, 7),
                                   pending=pending)
                    greens = column_leds(t, sl_state=state).count(LED_GREEN)
                    self.assertLessEqual(
                        greens, 1,
                        f"active={active} pending={pending} state={state}"
                    )


class MatrixMessageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.view = GridView(offset=0)
        self.tracks = {i: track_with(active=0, occupied=(0,)) for i in range(15)}
        self.states = {i: SL_STATE_PLAYING for i in range(15)}

    def test_first_paint_covers_every_visible_pad(self) -> None:
        msgs, _ = matrix_messages(self.view, self.tracks, self.states, previous=None)
        self.assertEqual(len(msgs), 64)

    def test_an_unchanged_surface_sends_nothing(self) -> None:
        """A full repaint is ~192 bytes on a 31.25 kbaud link — about 60 ms of
        wire time, on the same cable the pad presses arrive on."""
        _, state = matrix_messages(self.view, self.tracks, self.states, previous=None)
        msgs, _ = matrix_messages(self.view, self.tracks, self.states, previous=state)
        self.assertEqual(msgs, [])

    def test_only_the_changed_cell_is_sent(self) -> None:
        _, state = matrix_messages(self.view, self.tracks, self.states, previous=None)
        self.states[3] = SL_STATE_MUTE
        msgs, _ = matrix_messages(self.view, self.tracks, self.states, previous=state)
        self.assertEqual(len(msgs), 1)
        note, colour = msgs[0]
        self.assertEqual(self.view.cell_for_note(note), (3, 0))
        self.assertEqual(colour, LED_YELLOW)

    def test_a_bank_change_forces_a_full_repaint(self) -> None:
        """The notes now address different tracks, so diffing against the old
        bank leaves pads showing the previous one."""
        _, state = matrix_messages(self.view, self.tracks, self.states, previous=None)
        moved = GridView(offset=7)
        msgs, _ = matrix_messages(moved, self.tracks, self.states, previous=None)
        self.assertEqual(len(msgs), 64)
        self.assertNotEqual(state, {})

    def test_a_track_with_no_state_is_dark_not_missing(self) -> None:
        msgs, _ = matrix_messages(self.view, {}, {}, previous=None)
        self.assertEqual(len(msgs), 64)
        self.assertTrue(all(c == LED_OFF for _n, c in msgs))
