"""JACK timebase master helpers."""

import unittest

from scripts.sooperlooper.sl_hud_monitor import beat_and_bar, beat_and_bar_from_transport


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

    def test_transport_bbt(self) -> None:
        beat, bar = beat_and_bar_from_transport({"beat": 2, "bar": 5})
        self.assertEqual(beat, 2)
        self.assertEqual(bar, 5)
        self.assertEqual(beat_and_bar_from_transport({}), (None, None))


class TimebaseMasterTests(unittest.TestCase):
    def test_set_bpm_clamps(self) -> None:
        from scripts.sooperlooper.jack_timebase import TimebaseMaster

        tm = TimebaseMaster(bpm=120.0)
        tm.set_bpm(9999.0)
        self.assertEqual(tm.bpm(), 999.0)
        tm.set_bpm(0.1)
        self.assertEqual(tm.bpm(), 1.0)


if __name__ == "__main__":
    unittest.main()
