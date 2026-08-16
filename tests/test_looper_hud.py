"""Looper HUD bar sweep — resuscitated from f069648, re-pointed at SL state."""

import unittest

from patch_browser.looper_hud import (
    bar_progress,
    current_beat_index,
    phrase_seconds,
    segment_count,
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


class PhraseTests(unittest.TestCase):
    """The display cycle is the longest clip, not one bar."""

    def test_phrase_spans_the_longest_clip(self) -> None:
        sl = {"bpm": 120.0, "phrase_len": 8.0, "bars_in_phrase": 4}
        self.assertAlmostEqual(phrase_seconds(sl), 8.0)
        self.assertEqual(segment_count(sl), 16)

    def test_phrase_falls_back_to_one_bar_before_any_clip(self) -> None:
        self.assertAlmostEqual(phrase_seconds({"bpm": 120.0}), 2.0)
        self.assertEqual(segment_count({"bpm": 120.0}), 4)

    def test_sweep_fills_the_whole_phrase_not_just_the_first_bar(self) -> None:
        sl = {"bpm": 120.0, "phrase_len": 8.0, "bars_in_phrase": 4,
              "loop_pos": 0.0, "updated_at": 1000.0}
        self.assertAlmostEqual(bar_progress(sl, now=1002.0), 0.25)
        self.assertAlmostEqual(bar_progress(sl, now=1006.0), 0.75)
        self.assertAlmostEqual(bar_progress(sl, now=1007.99), 0.99875)

    def test_live_segment_advances_discretely(self) -> None:
        sl = {"bpm": 120.0, "phrase_len": 8.0, "bars_in_phrase": 4,
              "loop_pos": 0.0, "updated_at": 1000.0}
        self.assertEqual(current_beat_index(sl, now=1000.0), 0)
        self.assertEqual(current_beat_index(sl, now=1000.6), 1)
        self.assertEqual(current_beat_index(sl, now=1007.9), 15)


class LabelAndVisibilityTests(unittest.TestCase):
    def test_label_counts_bars_within_the_phrase(self) -> None:
        self.assertEqual(beat_label({"bar": 3, "bars_in_phrase": 4}), "3/4")
        self.assertEqual(beat_label({"bar": 1, "bars_in_phrase": 1}), "1/1")
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
