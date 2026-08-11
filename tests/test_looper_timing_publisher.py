"""Tests for throttled looper timing publisher."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from patch_browser.clip_matrix import ClipMatrix
from patch_browser.looper_timing_publisher import LooperTimingPublisher
from patch_browser.looper_timing_state import TIMING_STATE_FILE


class LooperTimingPublisherTests(unittest.TestCase):
    def test_skips_unchanged_tick(self) -> None:
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

    def test_publishes_on_tick_advance(self) -> None:
        matrix = ClipMatrix.create_v1(sample_rate=48000, bpm=120.0, bars=1, loop_gain=1.0)
        matrix.on_grid(0, 0)
        pub = LooperTimingPublisher()
        fpb = matrix.clock.frames_per_beat
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "timing.json"
            with patch("patch_browser.looper_timing_state.TIMING_STATE_FILE", path):
                with patch("patch_browser.looper_timing_publisher.write_timing_state") as write_mock:
                    pub.publish_from_matrix(matrix)
                    matrix.clock.advance(fpb // 2)
                    pub.publish_from_matrix(matrix)
                    self.assertEqual(write_mock.call_count, 2)

    def test_publishes_full_bar_before_wrap(self) -> None:
        matrix = ClipMatrix.create_v1(sample_rate=48000, bpm=120.0, bars=4, loop_gain=1.0)
        matrix.on_grid(0, 0)
        pub = LooperTimingPublisher()
        frames_per_bar = matrix.clock.frames_per_bar
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "timing.json"
            with patch("patch_browser.looper_timing_state.TIMING_STATE_FILE", path):
                with patch("patch_browser.looper_timing_publisher.write_timing_state") as write_mock:
                    matrix.clock.advance(frames_per_bar - 512)
                    pub.publish_from_matrix(matrix)
                    matrix.clock.advance(512)
                    pub.publish_from_matrix(matrix)
                    self.assertGreaterEqual(write_mock.call_count, 2)
                    last = write_mock.call_args_list[-1]
                    self.assertEqual(
                        last.kwargs.get("total_frames") or last[1].get("total_frames"),
                        frames_per_bar,
                    )


if __name__ == "__main__":
    unittest.main()
