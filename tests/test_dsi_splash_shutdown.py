"""Unit tests for shutdown splash helpers (no display required)."""

from __future__ import annotations

import unittest
from unittest import mock

from patch_browser.dsi_splash import (
    BOOT_SPINNER_PERIOD,
    SHUTDOWN_FAILED_HINT_SECONDS,
    boot_animation_phase,
    request_system_power_action,
    shutdown_animation_phase,
    shutdown_subtitle,
)


class ShutdownSplashHelperTests(unittest.TestCase):
    def test_animation_phase_cycles(self) -> None:
        self.assertAlmostEqual(shutdown_animation_phase(0.0), 0.0)
        self.assertAlmostEqual(shutdown_animation_phase(0.6, period=1.2), 0.5)
        self.assertAlmostEqual(shutdown_animation_phase(1.2, period=1.2), 0.0)

    def test_boot_animation_phase_cycles(self) -> None:
        self.assertAlmostEqual(boot_animation_phase(0.0), 0.0)
        self.assertAlmostEqual(boot_animation_phase(0.6, period=BOOT_SPINNER_PERIOD), 0.5)
        self.assertAlmostEqual(boot_animation_phase(BOOT_SPINNER_PERIOD, period=BOOT_SPINNER_PERIOD), 0.0)

    def test_subtitle_before_slow_threshold(self) -> None:
        self.assertEqual(shutdown_subtitle(0.0), "Shutting down…")
        self.assertEqual(
            shutdown_subtitle(SHUTDOWN_FAILED_HINT_SECONDS - 0.1),
            "Shutting down…",
        )

    def test_subtitle_after_slow_threshold(self) -> None:
        self.assertEqual(
            shutdown_subtitle(SHUTDOWN_FAILED_HINT_SECONDS),
            "Still shutting down…",
        )
        self.assertEqual(shutdown_subtitle(60.0), "Still shutting down…")

    def test_subtitle_failed(self) -> None:
        self.assertEqual(
            shutdown_subtitle(0.0, failed=True),
            "Shutdown failed — check sudo/logs",
        )


class RequestSystemPowerActionTests(unittest.TestCase):
    @mock.patch("patch_browser.dsi_splash.subprocess.run")
    def test_shutdown_uses_systemctl_poweroff(self, run_mock: mock.Mock) -> None:
        run_mock.return_value = mock.Mock(returncode=0, stdout="", stderr="")
        self.assertTrue(request_system_power_action("shutdown"))
        run_mock.assert_called_once()
        self.assertEqual(run_mock.call_args.args[0], ["sudo", "systemctl", "poweroff"])

    @mock.patch("patch_browser.dsi_splash.subprocess.run")
    def test_restart_uses_systemctl_reboot(self, run_mock: mock.Mock) -> None:
        run_mock.return_value = mock.Mock(returncode=0, stdout="", stderr="")
        self.assertTrue(request_system_power_action("restart"))
        self.assertEqual(run_mock.call_args.args[0], ["sudo", "systemctl", "reboot"])

    @mock.patch("patch_browser.dsi_splash.subprocess.run")
    def test_nonzero_return_is_failure(self, run_mock: mock.Mock) -> None:
        run_mock.return_value = mock.Mock(returncode=1, stdout="", stderr="denied")
        self.assertFalse(request_system_power_action("shutdown"))


if __name__ == "__main__":
    unittest.main()
