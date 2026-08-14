"""APC grid ↔ loop index mapping."""

import unittest

from scripts.sooperlooper.apc_grid import (
    all_loop_pads,
    loop_index_for_note,
    loop_index_for_pad,
    pad_note,
)


class ApcGridTests(unittest.TestCase):
    def test_row0_maps_loops_0_7(self) -> None:
        for col in range(8):
            self.assertEqual(loop_index_for_pad(0, col), col)
            self.assertEqual(pad_note(0, col), col)

    def test_row3_maps_loops_8_15(self) -> None:
        for col in range(8):
            self.assertEqual(loop_index_for_pad(3, col), 8 + col)
            self.assertEqual(pad_note(3, col), 24 + col)

    def test_controller_rows_unmapped(self) -> None:
        for row in (1, 2, 4, 5, 6, 7):
            self.assertIsNone(loop_index_for_pad(row, 0))

    def test_note_roundtrip(self) -> None:
        for row, col, loop_i in all_loop_pads():
            note = pad_note(row, col)
            self.assertEqual(loop_index_for_note(note), loop_i)

    def test_sixteen_clip_pads(self) -> None:
        self.assertEqual(len(all_loop_pads()), 16)


if __name__ == "__main__":
    unittest.main()
