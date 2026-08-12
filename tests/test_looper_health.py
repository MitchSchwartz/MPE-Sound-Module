"""Tests for always-on looper deadline/xrun health and its HUD badge."""

from __future__ import annotations

import unittest

from patch_browser.looper_health import LooperHealth
from patch_browser.looper_hud import looper_health_badge

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
        # Half a period, then a full period, inside one window.
        health.record_period(BUDGET_S / 2, 0.0)
        health.record_period(BUDGET_S, 0.5)
        health.record_period(0.0001, 1.5)  # closes the window
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

    def test_window_resets_so_old_spikes_age_out(self) -> None:
        health = self._health()
        health.record_period(BUDGET_S * 2, 0.0)
        health.record_period(0.0001, 1.5)  # window 1 closes, spike recorded
        self.assertGreater(health.snapshot()["max_pct"], 150.0)

        health.record_period(0.0001, 1.6)
        health.record_period(0.0001, 3.0)  # window 2 closes, all quiet
        self.assertLess(health.snapshot()["max_pct"], 10.0)
        self.assertEqual(health.snapshot()["over_budget"], 0)

    def test_snapshot_carries_xruns(self) -> None:
        self.assertEqual(self._health().snapshot(xruns=7)["xruns"], 7)

    def test_zero_budget_does_not_divide_by_zero(self) -> None:
        health = LooperHealth(period_budget_s=0.0, window_s=1.0)
        health.record_period(0.001, 0.0)
        health.record_period(0.001, 2.0)
        self.assertIsNotNone(health.snapshot()["max_pct"])


class LooperHealthBadgeTests(unittest.TestCase):
    def test_silent_when_healthy(self) -> None:
        self.assertIsNone(looper_health_badge({"health": {"max_pct": 12.0, "xruns": 0}}))

    def test_silent_when_no_health_published(self) -> None:
        self.assertIsNone(looper_health_badge({}))
        self.assertIsNone(looper_health_badge({"health": None}))
        self.assertIsNone(looper_health_badge({"health": {"max_pct": None, "xruns": 0}}))

    def test_xruns_win_over_utilization(self) -> None:
        badge = looper_health_badge({"health": {"max_pct": 12.0, "xruns": 3}})
        self.assertEqual(badge, ("!3", "danger"))

    def test_xrun_count_is_capped(self) -> None:
        badge = looper_health_badge({"health": {"max_pct": 12.0, "xruns": 1234}})
        self.assertEqual(badge, ("!99+", "danger"))

    def test_warns_before_the_deadline_is_missed(self) -> None:
        self.assertEqual(looper_health_badge({"health": {"max_pct": 80.0}}), ("80%", "warn"))

    def test_danger_at_or_over_budget(self) -> None:
        self.assertEqual(looper_health_badge({"health": {"max_pct": 136.0}}), ("136%", "danger"))


if __name__ == "__main__":
    unittest.main()
