"""Tests for looper timing publisher."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from patch_browser.clip_matrix import ClipMatrix
from patch_browser.looper_timing_publisher import PUBLISH_INTERVAL_S, LooperTimingPublisher
from patch_browser.looper_timing_state import write_timing_state


class FakeClock:
    """Injectable monotonic source — advance explicitly instead of sleeping."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _publisher(clock: FakeClock | None = None) -> LooperTimingPublisher:
    return LooperTimingPublisher(time_source=clock or FakeClock())


class LooperTimingPublisherTests(unittest.TestCase):
    def test_skips_unchanged_total_frames(self) -> None:
        matrix = ClipMatrix.create_v1(sample_rate=48000, bpm=120.0, bars=1, loop_gain=1.0)
        matrix.on_grid(0, 0)
        pub = _publisher()
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
        clock = FakeClock()
        pub = _publisher(clock)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "timing.json"
            with patch("patch_browser.looper_timing_state.TIMING_STATE_FILE", path):
                with patch("patch_browser.looper_timing_publisher.write_timing_state") as write_mock:
                    pub.publish_from_matrix(matrix)
                    matrix.clock.advance(512)
                    clock.advance(PUBLISH_INTERVAL_S * 2)
                    pub.publish_from_matrix(matrix)
                    self.assertEqual(write_mock.call_count, 2)
                    last = write_mock.call_args_list[-1].kwargs
                    self.assertEqual(last["total_frames"], 512)
                    self.assertEqual(last["frames_per_beat"], matrix.clock.frames_per_beat)
                    self.assertEqual(last["sample_rate"], matrix.clock.sample_rate)

    def test_publishes_on_eighth_advance(self) -> None:
        matrix = ClipMatrix.create_v1(sample_rate=48000, bpm=120.0, bars=1, loop_gain=1.0)
        matrix.on_grid(0, 0)
        clock = FakeClock()
        pub = _publisher(clock)
        fpb = matrix.clock.frames_per_beat
        eighth_frames = fpb // 2
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "timing.json"
            with patch("patch_browser.looper_timing_state.TIMING_STATE_FILE", path):
                with patch("patch_browser.looper_timing_publisher.write_timing_state") as write_mock:
                    pub.publish_from_matrix(matrix)
                    matrix.clock.advance(eighth_frames)
                    clock.advance(PUBLISH_INTERVAL_S * 2)
                    pub.publish_from_matrix(matrix)
                    self.assertEqual(write_mock.call_args_list[-1].kwargs["tick_in_bar"], 1)

    def test_inactive_matrix_does_not_publish(self) -> None:
        matrix = ClipMatrix.create_v1(sample_rate=48000, bpm=120.0, bars=1, loop_gain=1.0)
        pub = _publisher()
        with patch("patch_browser.looper_timing_publisher.write_timing_state") as write_mock:
            pub.publish_from_matrix(matrix)
            self.assertEqual(write_mock.call_count, 0)

    def test_publishes_on_loop_wrap(self) -> None:
        matrix = ClipMatrix.create_v1(sample_rate=48000, bpm=120.0, bars=4, loop_gain=1.0)
        matrix.on_grid(0, 0)
        clock = FakeClock()
        pub = _publisher(clock)
        frames_per_loop = matrix.clock.frames_per_bar * 4
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "timing.json"
            with patch("patch_browser.looper_timing_state.TIMING_STATE_FILE", path):
                with patch("patch_browser.looper_timing_publisher.write_timing_state") as write_mock:
                    pub.publish_from_matrix(matrix)
                    first_eighth = write_mock.call_args.kwargs["eighth_index"]
                    matrix.clock.advance(frames_per_loop)
                    clock.advance(PUBLISH_INTERVAL_S * 2)
                    pub.publish_from_matrix(matrix)
                    wrap = write_mock.call_args.kwargs
                    self.assertEqual(wrap["beat_in_bar"], 1)
                    self.assertEqual(wrap["bar_in_loop"], 1)
                    self.assertEqual(wrap["tick_in_bar"], 0)
                    self.assertGreater(wrap["eighth_index"], first_eighth)

    def test_clear_resets_publish_dedupe(self) -> None:
        matrix = ClipMatrix.create_v1(sample_rate=48000, bpm=120.0, bars=1, loop_gain=1.0)
        matrix.on_grid(0, 0)
        pub = _publisher()
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
        pub = _publisher()
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


class LooperTimingPublisherThrottleTests(unittest.TestCase):
    """Audio-period publishes are rate-capped; the SD-card churn regression guard."""

    def _matrix(self):
        matrix = ClipMatrix.create_v1(sample_rate=48000, bpm=120.0, bars=1, loop_gain=1.0)
        matrix.on_grid(0, 0)
        return matrix

    def test_burst_of_periods_writes_once(self) -> None:
        matrix = self._matrix()
        clock = FakeClock()
        pub = _publisher(clock)
        with patch("patch_browser.looper_timing_publisher.write_timing_state") as write_mock:
            for _ in range(20):
                matrix.clock.advance(512)
                clock.advance(512 / 48000.0)  # ~10.7 ms per audio period
                pub.publish_from_matrix(matrix)
            # 20 periods ≈ 213 ms of audio → at most 213/40 + 1 publishes
            self.assertLessEqual(write_mock.call_count, 6)
            self.assertGreaterEqual(write_mock.call_count, 4)

    def test_rapid_calls_inside_interval_write_once(self) -> None:
        matrix = self._matrix()
        clock = FakeClock()
        pub = _publisher(clock)
        with patch("patch_browser.looper_timing_publisher.write_timing_state") as write_mock:
            pub.publish_from_matrix(matrix)
            for _ in range(10):
                matrix.clock.advance(512)
                clock.advance(0.001)
                pub.publish_from_matrix(matrix)
            self.assertEqual(write_mock.call_count, 1)

    def test_first_publish_after_activation_is_immediate(self) -> None:
        matrix = ClipMatrix.create_v1(sample_rate=48000, bpm=120.0, bars=1, loop_gain=1.0)
        clock = FakeClock()
        pub = _publisher(clock)
        with patch("patch_browser.looper_timing_publisher.write_timing_state") as write_mock:
            matrix.on_grid(0, 0)  # EMPTY -> RECORDING
            pub.publish_from_matrix(matrix)
            self.assertEqual(write_mock.call_count, 1)

            matrix.on_grid(0, 0)  # RECORDING, no content -> EMPTY (inactive)
            pub.publish_from_matrix(matrix)
            self.assertEqual(write_mock.call_count, 1)

            matrix.on_grid(0, 0)  # active again
            matrix.clock.advance(512)
            pub.publish_from_matrix(matrix)  # no wall-clock advance: still writes
            self.assertEqual(write_mock.call_count, 2)

    def test_clear_writes_through_unthrottled(self) -> None:
        matrix = self._matrix()
        clock = FakeClock()
        pub = _publisher(clock)
        with patch("patch_browser.looper_timing_publisher.clear_timing_state") as clear_mock:
            with patch("patch_browser.looper_timing_publisher.write_timing_state"):
                pub.publish_from_matrix(matrix)
                pub.clear()
                pub.clear()
                self.assertEqual(clear_mock.call_count, 2)

    def test_publish_resumes_immediately_after_clear(self) -> None:
        matrix = self._matrix()
        clock = FakeClock()
        pub = _publisher(clock)
        with patch("patch_browser.looper_timing_publisher.clear_timing_state"):
            with patch("patch_browser.looper_timing_publisher.write_timing_state") as write_mock:
                pub.publish_from_matrix(matrix)
                pub.clear()
                matrix.clock.advance(512)
                pub.publish_from_matrix(matrix)
                self.assertEqual(write_mock.call_count, 2)


if __name__ == "__main__":
    unittest.main()
