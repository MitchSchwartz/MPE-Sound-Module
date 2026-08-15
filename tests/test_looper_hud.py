"""Looper HUD bar sweep — resuscitated from f069648, re-pointed at SL state."""

import unittest

from patch_browser.looper_hud import (
    bar_progress,
    bar_seconds,
    beat_label,
    interpolated_pos,
    is_running,
    should_show,
)


class BarMathTests(unittest.TestCase):
    def test_bar_seconds_from_bpm(self) -> None:
        self.assertAlmostEqual(bar_seconds(120.0), 2.0)
        self.assertAlmostEqual(bar_seconds(40.0), 6.0)
        self.assertIsNone(bar_seconds(0.0))
        self.assertIsNone(bar_seconds(None))

    def test_progress_wraps_once_per_bar(self) -> None:
        sl = {"bpm": 120.0, "loop_pos": 0.0, "updated_at": 1000.0}
        self.assertAlmostEqual(bar_progress(sl, now=1000.0), 0.0)
        self.assertAlmostEqual(bar_progress(sl, now=1001.0), 0.5)
        self.assertAlmostEqual(bar_progress(sl, now=1002.0), 0.0)  # wrapped
        self.assertAlmostEqual(bar_progress(sl, now=1002.5), 0.25)

    def test_position_is_interpolated_between_file_writes(self) -> None:
        """The HUD file updates ~2x/sec; without this the sweep visibly steps."""
        sl = {"bpm": 120.0, "loop_pos": 0.25, "updated_at": 1000.0}
        self.assertAlmostEqual(interpolated_pos(sl, now=1000.0), 0.25)
        self.assertAlmostEqual(interpolated_pos(sl, now=1000.4), 0.65)

    def test_progress_none_without_a_grid(self) -> None:
        self.assertIsNone(bar_progress({}, now=1.0))
        self.assertIsNone(bar_progress({"bpm": 120.0}, now=1.0))


class LabelAndVisibilityTests(unittest.TestCase):
    def test_beat_label(self) -> None:
        self.assertEqual(beat_label({"beat": 3}), "3/4")
        self.assertEqual(beat_label({}), "")

    def test_shown_once_a_grid_exists_even_before_playback(self) -> None:
        self.assertTrue(should_show({"bpm": 120.0}))
        self.assertFalse(should_show({}))
        self.assertFalse(should_show({"bpm": 120.0}, user_enabled=False))

    def test_running_follows_engine_state(self) -> None:
        self.assertTrue(is_running({"active": True}))
        self.assertTrue(is_running({"playing": True}))
        self.assertFalse(is_running({"active": False, "playing": False}))


if __name__ == "__main__":
    unittest.main()
