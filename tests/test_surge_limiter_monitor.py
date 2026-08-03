"""Tests for Surge limiter activity monitor."""

from __future__ import annotations

import unittest
from unittest import mock

from patch_browser.surge_limiter_monitor import SurgeLimiterMonitor


class SurgeLimiterMonitorTests(unittest.TestCase):
    def test_idle_when_limiter_off(self) -> None:
        monitor = SurgeLimiterMonitor(mock.Mock(), mock.Mock(), mock.Mock())
        with mock.patch("patch_browser.surge_limiter_monitor.limiter_active", return_value=False):
            monitor._poll_once()
        self.assertFalse(monitor.snapshot()["reducing"])

    def test_active_when_hot_and_cpu_high(self) -> None:
        surge = mock.Mock()
        surge.check_health.return_value = (True, "")
        cpu = mock.Mock()
        cpu.snapshot.return_value = {"online": True, "raw_percent": 35.0}
        loader = mock.Mock()
        loader._patch_gain_linear = 1.4
        loader.user_volume_trim = 1.0
        monitor = SurgeLimiterMonitor(surge, cpu, loader)
        with mock.patch("patch_browser.surge_limiter_monitor.limiter_active", return_value=True):
            monitor._poll_once()
        self.assertTrue(monitor.snapshot()["reducing"])


if __name__ == "__main__":
    unittest.main()
