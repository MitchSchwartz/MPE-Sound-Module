"""Unit tests for shutdown splash helpers (no display required)."""

from __future__ import annotations

import unittest
from unittest import mock

from patch_browser.dsi_splash import (
    BOOT_SPINNER_PERIOD,
    SHUTDOWN_FAILED_HINT_SECONDS,
    SHUTDOWN_SPLASH_UNIT,
    boot_animation_phase,
    request_system_power_action,
    shutdown_animation_phase,
    shutdown_subtitle,
    start_shutdown_splash_service,
    trigger_user_shutdown,
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
    @mock.patch("patch_browser.dsi_splash._run_systemctl")
    def test_shutdown_uses_systemctl_poweroff(self, run_mock: mock.Mock) -> None:
        run_mock.return_value = True
        self.assertTrue(request_system_power_action("shutdown"))
        run_mock.assert_called_once_with(["poweroff"], log_label="systemctl poweroff")

    @mock.patch("patch_browser.dsi_splash._run_systemctl")
    def test_restart_uses_systemctl_reboot(self, run_mock: mock.Mock) -> None:
        run_mock.return_value = True
        self.assertTrue(request_system_power_action("restart"))
        run_mock.assert_called_once_with(["reboot"], log_label="systemctl reboot")

    @mock.patch("patch_browser.dsi_splash._run_systemctl")
    def test_nonzero_return_is_failure(self, run_mock: mock.Mock) -> None:
        run_mock.return_value = False
        self.assertFalse(request_system_power_action("shutdown"))


class StartShutdownSplashServiceTests(unittest.TestCase):
    @mock.patch("patch_browser.dsi_splash._run_systemctl")
    def test_starts_mpe_shutdown_splash_unit(self, run_mock: mock.Mock) -> None:
        run_mock.return_value = True
        self.assertTrue(start_shutdown_splash_service())
        run_mock.assert_called_once_with(
            ["start", SHUTDOWN_SPLASH_UNIT],
            log_label=f"systemctl start {SHUTDOWN_SPLASH_UNIT}",
        )


class TriggerUserShutdownTests(unittest.TestCase):
    @mock.patch("patch_browser.dsi_splash.request_system_power_action")
    @mock.patch("patch_browser.dsi_splash.start_shutdown_splash_service")
    @mock.patch("patch_browser.dsi_splash.stop_boot_splash_service")
    @mock.patch("patch_browser.dsi_splash.stop_getty_tty1")
    def test_splash_before_poweroff(
        self,
        stop_getty_mock: mock.Mock,
        stop_boot_mock: mock.Mock,
        start_splash_mock: mock.Mock,
        power_mock: mock.Mock,
    ) -> None:
        order: list[str] = []

        def _stop_getty() -> None:
            order.append("stop_getty")

        def _stop_boot(*_args: object, **_kwargs: object) -> None:
            order.append("stop_boot")

        def _start_splash() -> bool:
            order.append("start_splash")
            return True

        def _power(action: str) -> bool:
            order.append(f"power:{action}")
            return True

        stop_getty_mock.side_effect = _stop_getty
        stop_boot_mock.side_effect = _stop_boot
        start_splash_mock.side_effect = _start_splash
        power_mock.side_effect = _power

        self.assertTrue(trigger_user_shutdown("shutdown"))
        self.assertEqual(
            order,
            ["stop_getty", "stop_boot", "start_splash", "power:shutdown"],
        )

    @mock.patch("patch_browser.dsi_splash.request_system_power_action", return_value=False)
    @mock.patch("patch_browser.dsi_splash.start_shutdown_splash_service", return_value=True)
    @mock.patch("patch_browser.dsi_splash.stop_boot_splash_service")
    @mock.patch("patch_browser.dsi_splash.stop_getty_tty1")
    def test_power_failure_still_returns_false(
        self,
        _stop_getty: mock.Mock,
        _stop_boot: mock.Mock,
        _start_splash: mock.Mock,
        _power: mock.Mock,
    ) -> None:
        self.assertFalse(trigger_user_shutdown("restart"))


if __name__ == "__main__":
    unittest.main()
