"""Scene Launch button ↔ grid row, anchored to the physical hardware.

The right-hand column of the APC MINI mk1 is EIGHT buttons, 0x52 at the top
down to 0x59 at the bottom, and the bottom one is Stop All Clips. So there are
eight grid rows and only seven scene launchers, and the one row without a
button is row 0 — the bottom one — because that is where Stop All sits.

This was wrong in both directions before, and the reason is worth keeping: the
mapping was derived from a docstring's description of the layout rather than
from the note table three lines above it. Every assertion here names the
physical button, so a future reader checks it against the panel and not
against prose.
"""

from __future__ import annotations

from tests import conftest  # noqa: F401

import unittest

from scripts.sooperlooper.apc_transport import (
    NOTE_STOP_ALL_CLIPS_MK1,
    SCENE_LAUNCH_NOTES_MK1,
    scene_launch_index_to_row,
    scene_row_for_note,
    scene_row_to_launch_index,
)


class PhysicalAlignmentTests(unittest.TestCase):
    def test_the_note_table_is_what_this_file_assumes(self) -> None:
        """If these ever change, every row below is meaningless."""
        self.assertEqual(SCENE_LAUNCH_NOTES_MK1, tuple(range(0x52, 0x59)))
        self.assertEqual(NOTE_STOP_ALL_CLIPS_MK1, 0x59)
        self.assertEqual(len(SCENE_LAUNCH_NOTES_MK1), 7, "seven, not eight")

    def test_the_top_button_is_beside_the_top_row(self) -> None:
        self.assertEqual(scene_launch_index_to_row(0), 7)

    def test_the_lowest_scene_button_is_beside_row_one(self) -> None:
        """0x58 is second from the bottom — Stop All is below it — so it sits
        beside row 1, not row 0."""
        self.assertEqual(scene_launch_index_to_row(6), 1)

    def test_every_button_is_one_row_lower_than_the_one_above(self) -> None:
        rows = [scene_launch_index_to_row(i) for i in range(7)]
        self.assertEqual(rows, [7, 6, 5, 4, 3, 2, 1])

    def test_row_zero_has_no_scene_button(self) -> None:
        """Eight rows, seven free buttons. Stop All Clips occupies the eighth
        position, so the bottom row simply has no scene launcher."""
        self.assertIsNone(scene_row_to_launch_index(0))

    def test_round_trip_over_the_rows_that_have_buttons(self) -> None:
        for row in range(1, 8):
            index = scene_row_to_launch_index(row)
            self.assertIsNotNone(index, f"row {row} should have a button")
            self.assertEqual(scene_launch_index_to_row(index), row)

    def test_note_lookup_agrees_with_the_panel(self) -> None:
        notes = SCENE_LAUNCH_NOTES_MK1
        self.assertEqual(scene_row_for_note(notes, notes[0]), 7)   # top button
        self.assertEqual(scene_row_for_note(notes, notes[6]), 1)   # lowest scene

    def test_stop_all_is_not_a_scene_row(self) -> None:
        self.assertIsNone(
            scene_row_for_note(SCENE_LAUNCH_NOTES_MK1, NOTE_STOP_ALL_CLIPS_MK1)
        )

    def test_no_scene_button_maps_to_row_zero(self) -> None:
        """The bug reported from the appliance: the seven buttons were mapped
        to rows 6..0, so each sat one row below its own pads and row 0 was
        driven by the button beside row 1."""
        rows = {scene_launch_index_to_row(i) for i in range(7)}
        self.assertNotIn(0, rows)


if __name__ == "__main__":
    unittest.main()
