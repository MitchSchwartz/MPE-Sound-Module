"""Tests for looper timing publisher."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from patch_browser.clip_matrix import ClipMatrix
from patch_browser.looper_timing_publisher import LooperTimingPublisher


class LooperTimingPublisherTests(unittest.TestCase):
    def test_skips_unchanged_beat_index(self) -> None:
        matrix = ClipMatrix.create_v1(sample_rate=48000, bpm=120.0, bars=1, loop_gain=1.0)
        matrix.on_grid(0, 0)
        pub = LooperTimingPublisher()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "timing.json"
            with patch("patch_browser.looper_timing_state.TIMING_STATE_FILE", path):
                with patch("patch_browser.looper_timing_publisher.write_timing_state") as write_mock:
                    pub.publish_from_matrix(matrix)
                    pub.publish_from_matrix(matrix)
                    self.assertEqual(write_mock.call_count, 1)

    def test_publishes_on_beat_advance(self) -> None:
        matrix = ClipMatrix.create_v1(sample_rate=48000, bpm=120.0, bars=1, loop_gain=1.0)
        matrix.on_grid(0, 0)
        pub = LooperTimingPublisher()
        fpb = matrix.clock.frames_per_beat
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "timing.json"
            with patch("patch_browser.looper_timing_state.TIMING_STATE_FILE", path):
                with patch("patch_browser.looper_timing_publisher.write_timing_state") as write_mock:
                    pub.publish_from_matrix(matrix)
                    matrix.clock.advance(fpb)
                    pub.publish_from_matrix(matrix)
                    self.assertEqual(write_mock.call_count, 2)
                    self.assertEqual(write_mock.call_args_list[-1].kwargs["beat_in_bar"], 2)
                    self.assertEqual(write_mock.call_args_list[-1].kwargs["beat_index"], 1)

    def test_publishes_on_loop_wrap(self) -> None:
        """Global beat_index must advance when (bar,beat) returns to (1,1)."""
        matrix = ClipMatrix.create_v1(sample_rate=48000, bpm=120.0, bars=4, loop_gain=1.0)
        matrix.on_grid(0, 0)
        pub = LooperTimingPublisher()
        frames_per_loop = matrix.clock.frames_per_bar * 4
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "timing.json"
            with patch("patch_browser.looper_timing_state.TIMING_STATE_FILE", path):
                with patch("patch_browser.looper_timing_publisher.write_timing_state") as write_mock:
                    pub.publish_from_matrix(matrix)
                    first_index = write_mock.call_args.kwargs["beat_index"]
                    matrix.clock.advance(frames_per_loop)
                    pub.publish_from_matrix(matrix)
                    self.assertEqual(write_mock.call_count, 2)
                    wrap = write_mock.call_args.kwargs
                    self.assertEqual(wrap["beat_in_bar"], 1)
                    self.assertEqual(wrap["bar_in_loop"], 1)
                    self.assertGreater(wrap["beat_index"], first_index)


if __name__ == "__main__":
    unittest.main()
