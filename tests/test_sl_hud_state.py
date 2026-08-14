"""SL HUD beat/bar helpers."""

import unittest

from patch_browser.sl_hud_state import read_sl_hud_state
from scripts.sooperlooper.sl_hud_monitor import beat_and_bar


class BeatAndBarTests(unittest.TestCase):
    def test_quarter_notes_in_cycle(self) -> None:
        beat, bar = beat_and_bar(0.0, 2.0)
        self.assertEqual(beat, 1)
        self.assertEqual(bar, 1)
        beat, bar = beat_and_bar(0.5, 2.0)
        self.assertEqual(beat, 2)
        beat, bar = beat_and_bar(1.5, 2.0)
        self.assertEqual(beat, 4)
        beat, bar = beat_and_bar(2.5, 2.0)
        self.assertEqual(bar, 2)


class ReadSlHudStateTests(unittest.TestCase):
    def test_missing_file_returns_empty(self) -> None:
        with unittest.mock.patch(
            "patch_browser.sl_hud_state.SL_HUD_STATE_FILE",
            unittest.mock.MagicMock(read_text=unittest.mock.Mock(side_effect=OSError())),
        ):
            self.assertEqual(read_sl_hud_state(), {})


if __name__ == "__main__":
    unittest.main()
