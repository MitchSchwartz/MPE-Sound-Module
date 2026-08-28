"""Scene column ↔ grid row, against the canonical panel map.

This mapping was wrong in production three times in one day — once shipped,
twice by me "fixing" it from prose instead of from the hardware. Every
assertion here therefore names the PHYSICAL button, and the first test pins
the note table itself, so a reader checks the panel and not a paragraph.

The trap, stated once: buttons are listed top-to-bottom in ascending note
order, grid rows are numbered bottom-to-top. The two orderings run opposite
ways, so the pairing is a reflection, never an identity.
"""

from __future__ import annotations

from tests import conftest  # noqa: F401

import unittest

from scripts.sooperlooper import apc_panel

from scripts.sooperlooper.apc_panel import (
    NOTE_STOP_ALL_CLIPS_MK1,
    SCENE_COLUMN_MK1,
    SCENE_COLUMN_MK2,
    is_stop_all,
    row_for_scene_index,
    row_for_scene_note,
    scene_index_for_row,
    scene_note_for_row,
)


class TheNoteTableItself(unittest.TestCase):
    """If these change, every other assertion in the file is meaningless."""

    def test_the_column_is_eight_buttons_top_to_bottom(self) -> None:
        self.assertEqual(SCENE_COLUMN_MK1, tuple(range(0x52, 0x5A)))
        self.assertEqual(len(SCENE_COLUMN_MK1), 8)

    def test_stop_all_is_the_bottom_button(self) -> None:
        """Measured on the appliance 2026-08-27: the button that sends 0x59 is
        the bottom one of the right-hand column."""
        self.assertEqual(SCENE_COLUMN_MK1[-1], NOTE_STOP_ALL_CLIPS_MK1)
        self.assertEqual(NOTE_STOP_ALL_CLIPS_MK1, 0x59)

    def test_mk2_has_the_same_shape(self) -> None:
        self.assertEqual(len(SCENE_COLUMN_MK2), 8)


class EveryRowHasAButton(unittest.TestCase):
    def test_eight_buttons_for_eight_rows(self) -> None:
        """"There is the obvious exact right amount of buttons." A mapping
        that leaves a row without one is wrong on its face."""
        rows = [row_for_scene_index(i) for i in range(8)]
        self.assertEqual(sorted(rows), list(range(8)))

    def test_the_pairing_is_a_reflection(self) -> None:
        self.assertEqual([row_for_scene_index(i) for i in range(8)],
                         [7, 6, 5, 4, 3, 2, 1, 0])

    def test_the_top_button_is_beside_the_top_row(self) -> None:
        self.assertEqual(row_for_scene_note(SCENE_COLUMN_MK1, 0x52), 7)

    def test_the_bottom_button_is_beside_the_bottom_row(self) -> None:
        """0x59 pressed alone is row 0's launcher. Its "Stop All Clips" label
        is a SHIFT layer — the bench has always required Shift+0x59."""
        self.assertEqual(row_for_scene_note(SCENE_COLUMN_MK1, 0x59), 0)

    def test_round_trip_over_every_row(self) -> None:
        for row in range(8):
            self.assertEqual(row_for_scene_index(scene_index_for_row(row)), row)

    def test_row_zero_is_reachable(self) -> None:
        """It was not, for one build: the seven upper buttons were mapped to
        rows 6..0, so row 0 was driven by the button beside row 1 and the
        bottom button did nothing but stop everything."""
        self.assertEqual(scene_note_for_row(SCENE_COLUMN_MK1, 0), 0x59)

    def test_no_two_buttons_share_a_row(self) -> None:
        rows = [row_for_scene_index(i) for i in range(8)]
        self.assertEqual(len(set(rows)), 8)


class NotAScene(unittest.TestCase):
    def test_a_grid_pad_is_not_a_scene_note(self) -> None:
        self.assertIsNone(row_for_scene_note(SCENE_COLUMN_MK1, 0x00))

    def test_shift_is_not_a_scene_note(self) -> None:
        self.assertIsNone(row_for_scene_note(SCENE_COLUMN_MK1, 0x62))

    def test_out_of_range_is_refused_not_wrapped(self) -> None:
        """Silently wrapping produced a scene on a row that was not pressed."""
        with self.assertRaises(ValueError):
            row_for_scene_index(8)
        with self.assertRaises(ValueError):
            scene_index_for_row(-1)


class StopAllIdentification(unittest.TestCase):
    def test_the_bottom_button_is_the_stop_all_button(self) -> None:
        self.assertTrue(is_stop_all("mk1", 0x59))

    def test_the_others_are_not(self) -> None:
        for note in SCENE_COLUMN_MK1[:-1]:
            self.assertFalse(is_stop_all("mk1", note), hex(note))


if __name__ == "__main__":
    unittest.main()


class TheBottomButtonWearsTwoHats(unittest.TestCase):
    """0x59 is a scene launcher alone and Stop All Clips with Shift held.

    This used to be "resolved" by leaving the button out of the scene column
    entirely, which silenced row 0. Now the two meanings are separated by the
    Shift state, so both have to be asserted.
    """

    def test_alone_it_launches_row_zero(self) -> None:
        self.assertEqual(
            apc_panel.scene_press_row(
                apc_panel.NOTE_STOP_ALL_CLIPS_MK1,
                scene_notes=apc_panel.SCENE_COLUMN_MK1,
                apc_label="mk1",
                shift_held=False,
            ),
            0,
        )

    def test_with_shift_it_is_not_a_scene_press(self) -> None:
        self.assertIsNone(
            apc_panel.scene_press_row(
                apc_panel.NOTE_STOP_ALL_CLIPS_MK1,
                scene_notes=apc_panel.SCENE_COLUMN_MK1,
                apc_label="mk1",
                shift_held=True,
            )
        )

    def test_shift_does_not_disable_the_other_seven(self) -> None:
        for note in apc_panel.SCENE_COLUMN_MK1:
            if note == apc_panel.NOTE_STOP_ALL_CLIPS_MK1:
                continue
            with self.subTest(note=hex(note)):
                self.assertEqual(
                    apc_panel.scene_press_row(
                        note,
                        scene_notes=apc_panel.SCENE_COLUMN_MK1,
                        apc_label="mk1",
                        shift_held=True,
                    ),
                    apc_panel.row_for_scene_note(apc_panel.SCENE_COLUMN_MK1, note),
                )

    def test_a_grid_pad_is_never_a_scene_press(self) -> None:
        self.assertIsNone(
            apc_panel.scene_press_row(
                0x00,
                scene_notes=apc_panel.SCENE_COLUMN_MK1,
                apc_label="mk1",
                shift_held=False,
            )
        )
