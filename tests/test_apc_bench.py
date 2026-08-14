"""16-pad APC footswitch bench wiring."""

import unittest
from unittest.mock import MagicMock

from scripts.sooperlooper.apc_footswitch import build_footswitches
from scripts.sooperlooper.apc_grid import loop_index_for_note, pad_note


class ApcBenchFootswitchTests(unittest.TestCase):
    def test_build_sixteen_pads(self) -> None:
        osc = MagicMock()
        midi_out = MagicMock()
        by_note, footswitches = build_footswitches(
            osc=osc,
            midi_out=midi_out,
            num_loops=16,
            hold_ms=1000.0,
            debounce_ms=200.0,
        )
        self.assertEqual(len(by_note), 16)
        self.assertEqual(len(footswitches), 16)
        for note, fs in by_note.items():
            loop_i = loop_index_for_note(note)
            self.assertIsNotNone(loop_i)
            self.assertEqual(fs.loop, loop_i)
            self.assertEqual(fs._note, note)

    def test_row0_and_row3_notes(self) -> None:
        osc = MagicMock()
        midi_out = MagicMock()
        by_note, _ = build_footswitches(
            osc=osc,
            midi_out=midi_out,
            num_loops=16,
            hold_ms=1000.0,
            debounce_ms=200.0,
        )
        for col in range(8):
            self.assertIn(pad_note(0, col), by_note)
            self.assertIn(pad_note(3, col), by_note)


if __name__ == "__main__":
    unittest.main()
