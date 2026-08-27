"""APC grid ↔ loop index mapping — horizontal track lane with a banked viewport.

Supersedes the row-0/row-3 layout (16 loops stacked two deep). Those
assertions are gone on purpose, not by accident: a column now holds exactly
one track, which is what makes per-column state expressible later.
"""

import unittest

from scripts.sooperlooper.apc_grid import (
    NUM_LOOPS,
    CONTROLLER_ROWS,
    MAX_VIEW_OFFSET,
    RESERVED_GRID_NOTES,
    GridView,
    all_clip_pads,
    is_clip_note,
    is_reserved_grid_note,
    pad_note,
)


class ApcGridTests(unittest.TestCase):
    def test_bottom_row_is_the_first_eight_tracks(self) -> None:
        view = GridView()
        for col in range(8):
            self.assertEqual(view.loop_for_pad(0, col), col)
            self.assertEqual(pad_note(0, col), col)

    def test_controller_rows_unmapped(self) -> None:
        view = GridView()
        for row in CONTROLLER_ROWS:
            self.assertIsNone(view.loop_for_pad(row, 0))

    def test_reserved_grid_notes_are_rows_one_through_seven(self) -> None:
        self.assertEqual(len(RESERVED_GRID_NOTES), 7 * 8)
        for note in RESERVED_GRID_NOTES:
            self.assertTrue(is_reserved_grid_note(note))
            self.assertFalse(is_clip_note(note))
        self.assertFalse(is_reserved_grid_note(pad_note(0, 0)))

    def test_row_three_is_no_longer_a_clip_row(self) -> None:
        self.assertIsNone(GridView().loop_for_note(pad_note(3, 0)))
        self.assertFalse(is_clip_note(pad_note(3, 0)))

    def test_banking_shifts_which_tracks_are_shown(self) -> None:
        view = GridView(offset=MAX_VIEW_OFFSET)
        self.assertEqual(view.visible_loops(), tuple(range(7, 15)))
        self.assertEqual(view.loop_for_pad(0, 0), MAX_VIEW_OFFSET)
        self.assertEqual(view.note_for_loop(MAX_VIEW_OFFSET), pad_note(0, 0))
        self.assertIsNone(view.note_for_loop(0))

    def test_scroll_clamps_and_never_wraps(self) -> None:
        self.assertEqual(GridView().scrolled(-8).offset, 0)
        self.assertEqual(GridView(offset=MAX_VIEW_OFFSET).scrolled(8).offset, MAX_VIEW_OFFSET)
        self.assertEqual(GridView(offset=MAX_VIEW_OFFSET).scrolled(1).offset,
                         MAX_VIEW_OFFSET, 'clamps at the last bank')
        self.assertFalse(GridView().can_scroll(-1))
        self.assertTrue(GridView().can_scroll(1))

    def test_note_roundtrip_in_every_bank(self) -> None:
        for offset in range(MAX_VIEW_OFFSET + 1):
            view = GridView(offset=offset)
            for row, col, loop_i in view.visible_pads():
                self.assertEqual(view.loop_for_note(pad_note(row, col)), loop_i)
                self.assertEqual(view.note_for_loop(loop_i), pad_note(row, col))

    def test_eight_visible_pads_covering_every_track_across_banks(self) -> None:
        self.assertEqual(len(GridView().visible_pads()), 8)
        self.assertEqual(len(all_clip_pads()), 8)
        seen = set(GridView().visible_loops()) | set(GridView(offset=MAX_VIEW_OFFSET).visible_loops())
        self.assertEqual(sorted(seen), list(range(NUM_LOOPS)))

    def test_column_holds_exactly_one_track(self) -> None:
        for offset in (0, 3, MAX_VIEW_OFFSET):
            view = GridView(offset=offset)
            for col in range(8):
                self.assertEqual(view.loops_for_column(col), (offset + col,))

    def test_column_agrees_with_the_pad_layout(self) -> None:
        # The point of deriving from visible_pads(): faders cannot drift away
        # from the pads by keeping their own copy of the layout.
        view = GridView(offset=5)
        for col in range(8):
            from_pads = tuple(
                loop for _row, c, loop in view.visible_pads() if c == col
            )
            self.assertEqual(view.loops_for_column(col), from_pads)

    def test_column_out_of_range_raises(self) -> None:
        for col in (-1, 8):
            with self.assertRaises(ValueError):
                GridView().loops_for_column(col)

    def test_short_track_count_leaves_trailing_columns_empty(self) -> None:
        view = GridView(num_loops=5)
        self.assertEqual(view.visible_loops(), (0, 1, 2, 3, 4))
        self.assertEqual(view.loops_for_column(6), ())
        self.assertEqual(view.max_offset, 0)


if __name__ == "__main__":
    unittest.main()
