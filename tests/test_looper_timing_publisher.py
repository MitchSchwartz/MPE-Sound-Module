"""Tests for looper timing publisher."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from patch_browser.clip_matrix import ClipMatrix
from patch_browser.looper_timing_publisher import LooperTimingPublisher


class LooperTimingPublisherTests(unittest.TestCase):
    def test_skips_unchanged_eighth(self) -> None:
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

    def test_publishes_on_eighth_advance(self) -> None:
        matrix = ClipMatrix.create_v1(sample_rate=48000, bpm=120.0, bars=1, loop_gain=1.0)
        matrix.on_grid(0, 0)
        pub = LooperTimingPublisher()
        fpb = matrix.clock.frames_per_beat
        eighth_frames = fpb // 2
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "timing.json"
            with patch("patch_browser.looper_timing_state.TIMING_STATE_FILE", path):
                with patch("patch_browser.looper_timing_publisher.write_timing_state") as write_mock:
                    pub.publish_from_matrix(matrix)
                    matrix.clock.advance(eighth_frames)
                    pub.publish_from_matrix(matrix)
                    self.assertEqual(write_mock.call_count, 2)
                    self.assertEqual(write_mock.call_args_list[-1].kwargs["tick_in_bar"], 1)

    def test_publishes_on_loop_wrap(self) -> None:
        matrix = ClipMatrix.create_v1(sample_rate=48000, bpm=120.0, bars=4, loop_gain=1.0)
        matrix.on_grid(0, 0)
        pub = LooperTimingPublisher()
        frames_per_loop = matrix.clock.frames_per_bar * 4
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "timing.json"
            with patch("patch_browser.looper_timing_state.TIMING_STATE_FILE", path):
                with patch("patch_browser.looper_timing_publisher.write_timing_state") as write_mock:
                    pub.publish_from_matrix(matrix)
                    first_eighth = write_mock.call_args.kwargs["eighth_index"]
                    matrix.clock.advance(frames_per_loop)
                    pub.publish_from_matrix(matrix)
                    self.assertEqual(write_mock.call_count, 2)
                    wrap = write_mock.call_args.kwargs
                    self.assertEqual(wrap["beat_in_bar"], 1)
                    self.assertEqual(wrap["bar_in_loop"], 1)
                    self.assertEqual(wrap["tick_in_bar"], 0)
                    self.assertGreater(wrap["eighth_index"], first_eighth)


if __name__ == "__main__":
    unittest.main()
