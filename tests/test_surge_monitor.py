"""Tests for SurgeMonitor graph-failure restart gating."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from patch_browser.surge_monitor import SurgeMonitor


class SurgeMonitorRestartGateTests(unittest.TestCase):
    @mock.patch("patch_browser.surge_monitor.read_engine_state")
    @mock.patch("patch_browser.surge_monitor.subprocess.run")
    def test_blocks_restart_when_graph_failed(self, run_mock: mock.Mock, state_mock: mock.Mock) -> None:
        state_mock.return_value = {"state": "failed", "reason": "no-server"}
        monitor = SurgeMonitor()
        ok, message = monitor.restart_surge()
        self.assertFalse(ok)
        self.assertIn("graph", message.lower())
        run_mock.assert_not_called()

    @mock.patch("patch_browser.surge_monitor.read_engine_state")
    def test_status_disables_restart_on_graph_failure(self, state_mock: mock.Mock) -> None:
        state_mock.return_value = {"state": "failed", "reason": "no-jack-device"}
        monitor = SurgeMonitor()
        monitor.is_healthy = False
        monitor.last_error = "Surge not running"
        with mock.patch.object(monitor, "check_health", return_value=(False, "Surge not running")):
            summary = monitor.get_status_summary()
        self.assertFalse(summary["can_restart"])


if __name__ == "__main__":
    unittest.main()
