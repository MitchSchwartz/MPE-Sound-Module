"""Tests for looper timing publisher."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from patch_browser.clip_matrix import ClipMatrix
from patch_browser.looper_timing_publisher import LooperTimingPublisher
from patch_browser.looper_timing_state import write_timing_state


class LooperTimingPublisherTests(unittest.TestCase):
    def test_skips_unchanged_total_frames(self) -> None:
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

    def test_publishes_total_frames_each_advance(self) -> None:
        matrix = ClipMatrix.create_v1(sample_rate=48000, bpm=120.0, bars=1, loop_gain=1.0)
        matrix.on_grid(0, 0)
        pub = LooperTimingPublisher()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "timing.json"
            with patch("patch_browser.looper_timing_state.TIMING_STATE_FILE", path):
                with patch("patch_browser.looper_timing_publisher.write_timing_state") as write_mock:
                    pub.publish_from_matrix(matrix)
                    matrix.clock.advance(512)
                    pub.publish_from_matrix(matrix)
                    self.assertEqual(write_mock.call_count, 2)
                    last = write_mock.call_args_list[-1].kwargs
                    self.assertEqual(last["total_frames"], 512)
                    self.assertEqual(last["frames_per_beat"], matrix.clock.frames_per_beat)

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
                    self.assertEqual(write_mock.call_args_list[-1].kwargs["tick_in_bar"], 1)

    def test_inactive_matrix_does_not_publish(self) -> None:
        matrix = ClipMatrix.create_v1(sample_rate=48000, bpm=120.0, bars=1, loop_gain=1.0)
        pub = LooperTimingPublisher()
        with patch("patch_browser.looper_timing_publisher.write_timing_state") as write_mock:
            pub.publish_from_matrix(matrix)
            self.assertEqual(write_mock.call_count, 0)

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
                    wrap = write_mock.call_args.kwargs
                    self.assertEqual(wrap["beat_in_bar"], 1)
                    self.assertEqual(wrap["bar_in_loop"], 1)
                    self.assertEqual(wrap["tick_in_bar"], 0)
                    self.assertGreater(wrap["eighth_index"], first_eighth)

    def test_clear_resets_publish_dedupe(self) -> None:
        matrix = ClipMatrix.create_v1(sample_rate=48000, bpm=120.0, bars=1, loop_gain=1.0)
        matrix.on_grid(0, 0)
        pub = LooperTimingPublisher()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "timing.json"
            with patch("patch_browser.looper_timing_state.TIMING_STATE_FILE", path):
                with patch("patch_browser.looper_timing_publisher.write_timing_state") as write_mock:
                    pub.publish_from_matrix(matrix)
                    self.assertEqual(write_mock.call_count, 1)
                    pub.clear()
                    pub.publish_from_matrix(matrix)
                    self.assertEqual(write_mock.call_count, 2)

    def test_clear_wipes_timing_state(self) -> None:
        pub = LooperTimingPublisher()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "timing.json"
            with patch("patch_browser.looper_timing_state.TIMING_STATE_FILE", path):
                write_timing_state(
                    active=True,
                    bar_in_loop=2,
                    bars_per_loop=4,
                    path=path,
                )
                pub.clear()
                from patch_browser.looper_timing_state import read_timing_state

                snap = read_timing_state(path=path, stale_after_s=999.0)
                self.assertFalse(snap["active"])
                self.assertIsNone(snap["bar_in_loop"])


if __name__ == "__main__":
    unittest.main()
