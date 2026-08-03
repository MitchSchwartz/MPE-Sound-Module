"""Tests for Surge limiter activity monitor."""

from __future__ import annotations

import unittest
from unittest import mock

from patch_browser.surge_limiter_monitor import SurgeLimiterMonitor
from patch_browser.surge_output_limiter import at_limiter_ceiling


class AtLimiterCeilingTests(unittest.TestCase):
    def test_at_ceiling_when_peak_matches_setting(self) -> None:
        with mock.patch("patch_browser.surge_output_limiter.limiter_threshold_db", return_value=-1.0):
            self.assertTrue(at_limiter_ceiling(-1.0))
            self.assertTrue(at_limiter_ceiling(-0.6))
            self.assertTrue(at_limiter_ceiling(-1.5))

    def test_not_at_ceiling_when_quiet(self) -> None:
        with mock.patch("patch_browser.surge_output_limiter.limiter_threshold_db", return_value=-1.0):
            self.assertFalse(at_limiter_ceiling(-30.0))

    def test_not_at_ceiling_when_well_below_setting(self) -> None:
        with mock.patch("patch_browser.surge_output_limiter.limiter_threshold_db", return_value=-1.0):
            self.assertFalse(at_limiter_ceiling(-6.0))


class SurgeLimiterMonitorTests(unittest.TestCase):
    def test_reducing_when_peak_at_ceiling(self) -> None:
        surge = mock.Mock()
        surge.check_health.return_value = (True, "")
        monitor = SurgeLimiterMonitor(surge, mock.Mock(), mock.Mock())
        monitor.peak_monitor = mock.Mock()
        monitor.peak_monitor.snapshot.return_value = {
            "online": True,
            "peak_dbtp": -1.1,
        }
        with mock.patch("patch_browser.surge_limiter_monitor.limiter_active", return_value=True):
            with mock.patch("patch_browser.surge_limiter_monitor.at_limiter_ceiling", return_value=True):
                monitor._poll_once()
        self.assertTrue(monitor.snapshot()["reducing"])

    def test_idle_when_peak_off_ceiling(self) -> None:
        surge = mock.Mock()
        surge.check_health.return_value = (True, "")
        monitor = SurgeLimiterMonitor(surge, mock.Mock(), mock.Mock())
        monitor.peak_monitor = mock.Mock()
        monitor.peak_monitor.snapshot.return_value = {
            "online": True,
            "peak_dbtp": -12.0,
        }
        with mock.patch("patch_browser.surge_limiter_monitor.limiter_active", return_value=True):
            with mock.patch("patch_browser.surge_limiter_monitor.at_limiter_ceiling", return_value=False):
                monitor._poll_once()
        self.assertFalse(monitor.snapshot()["reducing"])


if __name__ == "__main__":
    unittest.main()
