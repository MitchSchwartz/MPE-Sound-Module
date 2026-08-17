"""Tests for looper/graph health metrics and HUD badge (salvaged from phase0 f39d0a6)."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from patch_browser.looper_health import (
    JackGraphHealth,
    LooperHealth,
    collect_jack_graph_health,
    jackd_journal_xruns_since,
    read_jack_cpu_load_pct,
)
from patch_browser.looper_hud import health_badge

BUDGET_S = 512 / 48000  # 10.67 ms


class LooperHealthTests(unittest.TestCase):
    def _health(self) -> LooperHealth:
        return LooperHealth(period_budget_s=BUDGET_S, window_s=1.0)

    def test_reports_nothing_before_first_window_closes(self) -> None:
        health = self._health()
        health.record_period(0.001, 0.0)
        snap = health.snapshot()
        self.assertIsNone(snap["max_pct"])
        self.assertEqual(snap["over_budget"], 0)

    def test_worst_case_is_reported_as_percent_of_budget(self) -> None:
        health = self._health()
        health.record_period(BUDGET_S / 2, 0.0)
        health.record_period(BUDGET_S, 0.5)
        health.record_period(0.0001, 1.5)
        snap = health.snapshot()
        self.assertAlmostEqual(snap["max_pct"], 100.0, delta=4.0)
        self.assertEqual(snap["over_budget"], 0)

    def test_counts_periods_over_budget(self) -> None:
        health = self._health()
        for i in range(3):
            health.record_period(BUDGET_S * 1.4, i * 0.1)
        health.record_period(0.0001, 2.0)
        snap = health.snapshot()
        self.assertEqual(snap["over_budget"], 3)
        self.assertGreater(snap["max_pct"], 100.0)

    def test_snapshot_carries_xruns(self) -> None:
        self.assertEqual(self._health().snapshot(xruns=7)["xruns"], 7)


class JackGraphHealthTests(unittest.TestCase):
    def test_tracks_session_xruns_and_cpu_peak(self) -> None:
        tracker = JackGraphHealth(window_s=1.0, started_at=1000.0)
        tracker.sample(cpu_load_pct=40.0, xruns_total=2, now_s=0.0)
        tracker.sample(cpu_load_pct=85.0, xruns_total=2, now_s=0.5)
        tracker.sample(cpu_load_pct=10.0, xruns_total=2, now_s=1.1)
        snap = tracker.snapshot()
        self.assertEqual(snap["xruns"], 2)
        self.assertAlmostEqual(snap["max_pct"], 85.0, delta=0.1)

    @patch("patch_browser.looper_health.read_jack_cpu_load_pct", return_value=22.5)
    @patch("patch_browser.looper_health.jackd_journal_xruns_since", return_value=3)
    def test_collect_jack_graph_health(self, _journal, _cpu) -> None:
        tracker = JackGraphHealth(window_s=0.0, started_at=1000.0)
        snap = collect_jack_graph_health(tracker)
        self.assertEqual(snap["xruns"], 3)


class JackProbeTests(unittest.TestCase):
    @patch("patch_browser.looper_health.subprocess.run")
    def test_read_jack_cpu_load_pct(self, run_mock) -> None:
        run_mock.return_value.stdout = "jack DSP load 35.666183\njack DSP load 40.1\n"
        run_mock.return_value.returncode = 0
        self.assertAlmostEqual(read_jack_cpu_load_pct(), 40.1)

    @patch("patch_browser.looper_health.subprocess.run")
    def test_jackd_journal_xruns_since(self, run_mock) -> None:
        run_mock.return_value.stdout = "jackd: xrun of at least 128 msecs\nok\n"
        run_mock.return_value.returncode = 0
        self.assertEqual(jackd_journal_xruns_since(1_700_000_000.0), 1)


class HealthBadgeTests(unittest.TestCase):
    def test_silent_when_healthy(self) -> None:
        self.assertIsNone(health_badge({"health": {"max_pct": 12.0, "xruns": 0}}))

    def test_xruns_win_over_utilization(self) -> None:
        badge = health_badge({"health": {"max_pct": 12.0, "xruns": 3}})
        self.assertEqual(badge, ("!3", "danger"))

    def test_warns_before_budget(self) -> None:
        self.assertEqual(health_badge({"health": {"max_pct": 80.0}}), ("80%", "warn"))


if __name__ == "__main__":
    unittest.main()
