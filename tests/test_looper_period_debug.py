"""Tests for looper period debug helper."""

from __future__ import annotations

import contextlib
import io
import os
import unittest

from patch_browser.clip_matrix import ClipMatrix, ClipState
from patch_browser.looper_period_debug import (
    LooperPeriodDebug,
    MsHistogram,
    count_playing_layers,
    looper_debug_enabled,
)

_BUDGET_S = 512 / 48000


@contextlib.contextmanager
def _env(value: str | None):
    old = os.environ.get("MPE_LOOPER_DEBUG")
    try:
        if value is None:
            os.environ.pop("MPE_LOOPER_DEBUG", None)
        else:
            os.environ["MPE_LOOPER_DEBUG"] = value
        yield
    finally:
        if old is None:
            os.environ.pop("MPE_LOOPER_DEBUG", None)
        else:
            os.environ["MPE_LOOPER_DEBUG"] = old


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
        with _env("1"):
            self.assertTrue(looper_debug_enabled())
        with _env("0"):
            self.assertFalse(looper_debug_enabled())


class MsHistogramTests(unittest.TestCase):
    def test_percentiles_on_known_sequence(self) -> None:
        hist = MsHistogram(bucket_ms=1.0, buckets=128)
        for i in range(100):
            hist.add(i + 0.5)
        self.assertEqual(hist.count, 100)
        self.assertAlmostEqual(hist.max_ms, 99.5)
        self.assertAlmostEqual(hist.percentile(0.50), 50.0)
        self.assertAlmostEqual(hist.percentile(0.95), 95.0)

    def test_percentile_never_exceeds_observed_max(self) -> None:
        hist = MsHistogram(bucket_ms=1.0, buckets=8)
        hist.add(0.1)
        self.assertAlmostEqual(hist.percentile(0.50), 0.1)
        self.assertAlmostEqual(hist.percentile(0.95), 0.1)

    def test_overflow_sample_reports_exact_max(self) -> None:
        hist = MsHistogram(bucket_ms=1.0, buckets=8)
        hist.add(0.5)
        hist.add(500.0)
        self.assertAlmostEqual(hist.max_ms, 500.0)
        self.assertAlmostEqual(hist.percentile(1.0), 500.0)

    def test_empty_histogram_is_zero(self) -> None:
        hist = MsHistogram(bucket_ms=1.0)
        self.assertEqual(hist.count, 0)
        self.assertAlmostEqual(hist.percentile(0.95), 0.0)

    def test_reset_clears_counts(self) -> None:
        hist = MsHistogram(bucket_ms=1.0)
        hist.add(4.0)
        hist.reset()
        self.assertEqual(hist.count, 0)
        self.assertAlmostEqual(hist.max_ms, 0.0)
        self.assertEqual(sum(hist.counts), 0)


class LooperPeriodDebugTimingTests(unittest.TestCase):
    def test_first_arrival_only_sets_baseline(self) -> None:
        dbg = LooperPeriodDebug(period_budget_s=_BUDGET_S)
        dbg.record_arrival(10.0)
        self.assertEqual(dbg.intervals.count, 0)
        dbg.record_arrival(10.0 + _BUDGET_S)
        self.assertEqual(dbg.intervals.count, 1)

    def test_burst_counter_counts_sub_quarter_budget_intervals(self) -> None:
        dbg = LooperPeriodDebug(period_budget_s=_BUDGET_S)
        now = 0.0
        dbg.record_arrival(now)
        for delta in (_BUDGET_S, _BUDGET_S * 0.1, _BUDGET_S * 0.2, _BUDGET_S * 2.0):
            now += delta
            dbg.record_arrival(now)
        self.assertEqual(dbg.intervals.count, 4)
        self.assertEqual(dbg.window_bursts, 2)
        self.assertAlmostEqual(dbg.intervals.max_ms, _BUDGET_S * 2.0 * 1000.0, places=6)

    def test_publish_samples_recorded(self) -> None:
        dbg = LooperPeriodDebug(period_budget_s=_BUDGET_S)
        dbg.record_publish(0.0002)
        dbg.record_publish(0.004)
        self.assertEqual(dbg.publishes.count, 2)
        self.assertAlmostEqual(dbg.publishes.max_ms, 4.0)

    def test_flush_prints_timing_line_and_resets_window(self) -> None:
        dbg = LooperPeriodDebug(period_budget_s=_BUDGET_S)
        dbg.record_arrival(0.0)
        dbg.record_arrival(_BUDGET_S)
        dbg.record_arrival(_BUDGET_S * 1.1)
        dbg.record_publish(0.001)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            dbg.flush_window("5s")
        line = buf.getvalue()
        self.assertIn("[debug] 5s timing", line)
        self.assertIn("interval_n=2", line)
        self.assertIn("burst_lt25pct=1", line)
        self.assertIn("publish_n=1", line)
        self.assertEqual(dbg.intervals.count, 0)
        self.assertEqual(dbg.publishes.count, 0)
        self.assertEqual(dbg.window_bursts, 0)

    def test_flush_prints_nothing_without_samples(self) -> None:
        dbg = LooperPeriodDebug(period_budget_s=_BUDGET_S)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            dbg.flush_window("5s")
        self.assertEqual(buf.getvalue(), "")

    def test_disabled_flag_creates_no_instrument(self) -> None:
        for value in (None, "0", ""):
            with _env(value):
                self.assertIsNone(LooperPeriodDebug.create_if_enabled(period_budget_s=_BUDGET_S))
        with _env("1"):
            self.assertIsInstance(
                LooperPeriodDebug.create_if_enabled(period_budget_s=_BUDGET_S),
                LooperPeriodDebug,
            )


class LooperClipTransitionLogTests(unittest.TestCase):
    def _matrix(self) -> ClipMatrix:
        return ClipMatrix.create_v1(sample_rate=48000, bpm=120.0, bars=4)

    def test_logs_recording_and_playing_transitions_once(self) -> None:
        matrix = self._matrix()
        dbg = LooperPeriodDebug(period_budget_s=_BUDGET_S)
        clip = matrix.slot(0, 2)
        assert clip is not None

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            dbg.log_clip_transitions(matrix)
        self.assertEqual(buf.getvalue(), "")

        matrix.clock.advance(120000)  # 1 bar + 1 beat @ 120 BPM / 48 kHz
        clip.state = ClipState.RECORDING
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            dbg.log_clip_transitions(matrix)
        line = buf.getvalue().strip()
        self.assertIn("[debug] clip r0c2 -> recording", line)
        self.assertIn("total_frames=120000", line)
        self.assertIn("bar=2/4", line)
        self.assertIn("beat=2", line)
        self.assertIn("playback_frame=0", line)
        self.assertIn("loop_frames=0", line)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            dbg.log_clip_transitions(matrix)
        self.assertEqual(buf.getvalue(), "")

        clip.state = ClipState.PLAYING
        clip.loop_frames = matrix.loop_frames
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            dbg.log_clip_transitions(matrix)
        self.assertIn("[debug] clip r0c2 -> playing", buf.getvalue())

    def test_non_reported_states_are_silent(self) -> None:
        matrix = self._matrix()
        dbg = LooperPeriodDebug(period_budget_s=_BUDGET_S)
        clip = matrix.slot(0, 0)
        assert clip is not None
        dbg.log_clip_transitions(matrix)
        clip.state = ClipState.STOPPED
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            dbg.log_clip_transitions(matrix)
        self.assertEqual(buf.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
