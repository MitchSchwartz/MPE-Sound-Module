"""Tests for LooperBarClock."""

from __future__ import annotations

import unittest

from patch_browser.looper_bar_clock import LooperBarClock


class LooperBarClockTests(unittest.TestCase):
    def test_beat_and_bar_at_120_bpm(self) -> None:
        clock = LooperBarClock(sample_rate=48000, bpm=120.0, beats_per_bar=4, bars_per_loop=4)
        fpb = clock.frames_per_beat  # 24000 @ 120/48k
        self.assertEqual(clock.beat_in_bar, 1)
        self.assertEqual(clock.bar_in_loop, 1)
        clock.advance(fpb)
        self.assertEqual(clock.beat_in_bar, 2)
        clock.advance(fpb)
        self.assertEqual(clock.beat_in_bar, 3)
        crossed = clock.advance(fpb * 2)
        self.assertTrue(crossed)
        self.assertEqual(clock.beat_in_bar, 1)
        self.assertEqual(clock.bar_in_loop, 2)

    def test_advance_reports_bar_boundary(self) -> None:
        clock = LooperBarClock(sample_rate=48000, bpm=120.0, beats_per_bar=4, bars_per_loop=4)
        crossed = clock.advance(clock.frames_per_bar)
        self.assertTrue(crossed)


if __name__ == "__main__":
    unittest.main()
