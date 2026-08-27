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
from slot_leds import matrix_messages, static_cell_led  # noqa: E402
from slot_matrix import Pending, Slot, Track  # noqa: E402


def track_with(active=None, occupied=(), pending=None) -> Track:
    slots = [Slot(f"s{i}.wav") if i in occupied else None for i in range(8)]
    return Track(slots=tuple(slots), active_slot=active, pending=pending)


class StaticCellColourTests(unittest.TestCase):
    """`static_cell_led` colours only cells the footswitch does not own."""

    def test_empty_is_dark(self) -> None:
        self.assertEqual(static_cell_led(track_with(), 3),
                         LED_OFF)

    def test_occupied_but_not_active_is_yellow(self) -> None:
        t = track_with(active=0, occupied=(0, 4))
        self.assertEqual(static_cell_led(t, 4), LED_YELLOW)


class ActiveLaneOwnershipTests(unittest.TestCase):
    """The active cell's colour comes from the footswitch and nowhere else."""

    def setUp(self) -> None:
        self.view = GridView(offset=0)
        self.tracks = {0: track_with(active=0, occupied=(0,))}

    def _colour_of_active(self, states, leds) -> int:
        msgs, _ = matrix_messages(self.view, self.tracks, states,
                                  previous=None, footswitch_leds=leds)
        note = self.view.note_for_cell(0, 0)
        return dict(msgs)[note]

    def test_the_footswitch_colour_wins(self) -> None:
        self.assertEqual(
            self._colour_of_active({0: SL_STATE_PLAYING}, {0: LED_RED_BLINK}),
            LED_RED_BLINK,
            "a timed record-to-play blink is a sequence, not a function of "
            "the current state — only the footswitch can produce it",
        )

    def test_the_engine_state_does_not_override_it(self) -> None:
        for state in (SL_STATE_OFF, SL_STATE_PLAYING, SL_STATE_RECORDING,
                      SL_STATE_MUTE, SL_STATE_WAIT_START):
            self.assertEqual(self._colour_of_active({0: state},
                                                    {0: LED_GREEN_BLINK}),
                             LED_GREEN_BLINK, state)

    def test_an_unpainted_active_cell_is_dark_not_guessed(self) -> None:
        """No entry means the footswitch has not painted yet. Dark is honest;
        a state-derived fallback here would be the second opinion again."""
        self.assertEqual(self._colour_of_active({0: SL_STATE_PLAYING}, {}), LED_OFF)


class PendingTests(unittest.TestCase):
    def test_a_switch_blinks_both_ends(self) -> None:
        t = track_with(active=0, occupied=(0, 3),
                       pending=Pending("switch", to_slot=3, from_slot=0))
        self.assertEqual(static_cell_led(t, 3),
                         LED_GREEN_BLINK)
        self.assertEqual(static_cell_led(t, 0),
                         LED_YELLOW_BLINK)

    def test_a_pending_stop_blinks_the_outgoing_slot(self) -> None:
        t = track_with(active=2, occupied=(2,), pending=Pending("stop", from_slot=2))
        self.assertEqual(static_cell_led(t, 2),
                         LED_YELLOW_BLINK)

    def test_a_pending_outranks_the_footswitch_on_the_active_cell(self) -> None:
        """A pending switch's outgoing slot IS the active slot, and the
        footswitch has never heard of the switch. Only the matrix can blink
        it, so this is the one case that reaches past the footswitch."""
        view = GridView(offset=0)
        tracks = {0: track_with(active=0, occupied=(0, 5),
                                pending=Pending("switch", to_slot=5, from_slot=0))}
        msgs, _ = matrix_messages(view, tracks, {0: SL_STATE_PLAYING},
                                  previous=None, footswitch_leds={0: LED_GREEN})
        self.assertEqual(dict(msgs)[view.note_for_cell(0, 0)], LED_YELLOW_BLINK)

    def test_uninvolved_slots_ignore_the_pending(self) -> None:
        t = track_with(active=0, occupied=(0, 3, 6),
                       pending=Pending("switch", to_slot=3, from_slot=0))
        self.assertEqual(static_cell_led(t, 6), LED_YELLOW)


class ColumnInvariantTests(unittest.TestCase):
    def test_at_most_one_green_per_column(self) -> None:
        """One buffer per track means one audible clip. Two greens in a column
        would show something the engine cannot do. Checked through
        `matrix_messages` now, because that is where the footswitch's colour
        and the stored-clip colours finally meet."""
        view = GridView(offset=0)
        pendings = (None,
                    Pending("switch", to_slot=6, from_slot=1),
                    Pending("launch", to_slot=4),
                    Pending("stop", from_slot=1))
        for active in (None, 0, 1, 7):
            for pending in pendings:
                for fs in (LED_OFF, LED_GREEN, LED_RED, LED_YELLOW,
                           LED_GREEN_BLINK, LED_RED_BLINK):
                    tracks = {0: track_with(active=active, occupied=(0, 1, 4, 6, 7),
                                            pending=pending)}
                    msgs, _ = matrix_messages(view, tracks, {0: SL_STATE_PLAYING},
                                              previous=None, footswitch_leds={0: fs})
                    column = [c for n, c in msgs if view.cell_for_note(n)[0] == 0]
                    self.assertLessEqual(
                        column.count(LED_GREEN), 1,
                        f"active={active} pending={pending} fs={fs}"
                    )


class MatrixMessageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.view = GridView(offset=0)
        self.tracks = {i: track_with(active=0, occupied=(0,)) for i in range(15)}
        self.states = {i: SL_STATE_PLAYING for i in range(15)}
        # The active row is the footswitch's; without this every column's
        # bottom pad is dark and the diff tests have nothing to move.
        self.fs = {i: LED_GREEN for i in range(15)}

    def test_first_paint_covers_every_visible_pad(self) -> None:
        msgs, _ = matrix_messages(self.view, self.tracks, self.states, previous=None,
                                  footswitch_leds=self.fs)
        self.assertEqual(len(msgs), 64)

    def test_an_unchanged_surface_sends_nothing(self) -> None:
        """A full repaint is ~192 bytes on a 31.25 kbaud link — about 60 ms of
        wire time, on the same cable the pad presses arrive on."""
        _, state = matrix_messages(self.view, self.tracks, self.states, previous=None,
                                  footswitch_leds=self.fs)
        msgs, _ = matrix_messages(self.view, self.tracks, self.states, previous=state,
                                  footswitch_leds=self.fs)
        self.assertEqual(msgs, [])

    def test_only_the_changed_cell_is_sent(self) -> None:
        _, state = matrix_messages(self.view, self.tracks, self.states, previous=None,
                                  footswitch_leds=self.fs)
        self.fs[3] = LED_YELLOW      # track 3's footswitch went muted
        msgs, _ = matrix_messages(self.view, self.tracks, self.states, previous=state,
                                  footswitch_leds=self.fs)
        self.assertEqual(len(msgs), 1)
        note, colour = msgs[0]
        self.assertEqual(self.view.cell_for_note(note), (3, 0))
        self.assertEqual(colour, LED_YELLOW)

    def test_a_bank_change_forces_a_full_repaint(self) -> None:
        """The notes now address different tracks, so diffing against the old
        bank leaves pads showing the previous one."""
        _, state = matrix_messages(self.view, self.tracks, self.states, previous=None,
                                  footswitch_leds=self.fs)
        moved = GridView(offset=7)
        msgs, _ = matrix_messages(moved, self.tracks, self.states, previous=None,
                                  footswitch_leds=self.fs)
        self.assertEqual(len(msgs), 64)
        self.assertNotEqual(state, {})

    def test_a_track_with_no_state_is_dark_not_missing(self) -> None:
        msgs, _ = matrix_messages(self.view, {}, {}, previous=None)
        self.assertEqual(len(msgs), 64)
        self.assertTrue(all(c == LED_OFF for _n, c in msgs))
