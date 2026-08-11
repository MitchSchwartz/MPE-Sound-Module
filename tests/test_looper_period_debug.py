"""Tests for looper period debug helper."""

from __future__ import annotations

import unittest

from patch_browser.clip_matrix import ClipMatrix, ClipState
from patch_browser.looper_period_debug import (
    LooperPeriodDebug,
    count_playing_layers,
    looper_debug_enabled,
)


class LooperPeriodDebugTests(unittest.TestCase):
    def test_count_playing_layers(self) -> None:
        matrix = ClipMatrix.create_v1(sample_rate=48000, bpm=120.0, bars=1)
        self.assertEqual(count_playing_layers(matrix), 0)
        clip = matrix.slot(0, 0)
        assert clip is not None
        clip.state = ClipState.PLAYING
        self.assertEqual(count_playing_layers(matrix), 1)

    def test_record_overrun_increments(self) -> None:
        dbg = LooperPeriodDebug(period_budget_s=512 / 48000)
        dbg.record(0.020, 3)
        self.assertEqual(dbg.window_overruns, 1)
        self.assertEqual(dbg.total_overruns, 1)
        dbg.record(0.005, 1)
        self.assertEqual(dbg.window_overruns, 1)
        dbg.flush_window("test")
        self.assertEqual(dbg.window_overruns, 0)

    def test_looper_debug_enabled_parses_env(self) -> None:
        import os

        old = os.environ.get("MPE_LOOPER_DEBUG")
        try:
            os.environ["MPE_LOOPER_DEBUG"] = "1"
            self.assertTrue(looper_debug_enabled())
            os.environ["MPE_LOOPER_DEBUG"] = "0"
            self.assertFalse(looper_debug_enabled())
        finally:
            if old is None:
                os.environ.pop("MPE_LOOPER_DEBUG", None)
            else:
                os.environ["MPE_LOOPER_DEBUG"] = old


if __name__ == "__main__":
    unittest.main()
