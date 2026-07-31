"""Unit tests for browser-launched calibration teardown handoff (no systemd on Pi)."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from patch_browser import calibration_teardown as ct
from patch_browser.calibration_constants import MPE_CALIB_FROM_BROWSER


def _systemctl_calls(run_mock: mock.Mock, verb: str) -> list[str]:
    units: list[str] = []
    for call in run_mock.call_args_list:
        args = list(call.args[0])
        if len(args) >= 4 and args[:3] == ["sudo", "systemctl", verb]:
            units.append(args[3])
    return units


class CalibrationHandoffTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env = mock.patch.dict(os.environ, {}, clear=False)
        self._env.start()
        os.environ.pop(MPE_CALIB_FROM_BROWSER, None)

    def tearDown(self) -> None:
        self._env.stop()

    @mock.patch("patch_browser.calibration_teardown.time.sleep")
    @mock.patch("patch_browser.calibration_teardown.subprocess.run")
    def test_stop_skips_touch_browser_when_from_browser(self, run_mock: mock.Mock, _sleep: mock.Mock) -> None:
        os.environ[MPE_CALIB_FROM_BROWSER] = "1"
        ct.stop_mpe_audio_services()
        stopped = _systemctl_calls(run_mock, "stop")
        self.assertNotIn("touch-patch-browser", stopped)
        self.assertIn("surge-xt-cli", stopped)

    @mock.patch("patch_browser.calibration_teardown.time.sleep")
    @mock.patch("patch_browser.calibration_teardown.subprocess.run")
    def test_stop_includes_touch_browser_when_not_from_browser(self, run_mock: mock.Mock, _sleep: mock.Mock) -> None:
        ct.stop_mpe_audio_services()
        stopped = _systemctl_calls(run_mock, "stop")
        self.assertIn("touch-patch-browser", stopped)
        self.assertIn("surge-xt-cli", stopped)

    @mock.patch("patch_browser.calibration_teardown.time.sleep")
    @mock.patch("patch_browser.calibration_teardown.subprocess.Popen")
    @mock.patch("patch_browser.calibration_teardown.subprocess.run")
    def test_restore_schedules_restart_when_from_browser(
        self,
        run_mock: mock.Mock,
        popen_mock: mock.Mock,
        _sleep: mock.Mock,
    ) -> None:
        os.environ[MPE_CALIB_FROM_BROWSER] = "1"
        ct.restore_mpe_audio_services(restart_browser=True)
        popen_mock.assert_called_once()
        cmd = popen_mock.call_args.args[0]
        self.assertEqual(cmd[0], "sudo")
        self.assertIn("systemctl restart touch-patch-browser", cmd[-1])
        started = _systemctl_calls(run_mock, "start")
        self.assertNotIn("touch-patch-browser", started)
        self.assertIn("surge-xt-cli", started)

    @mock.patch("patch_browser.calibration_teardown.time.sleep")
    @mock.patch("patch_browser.calibration_teardown.subprocess.run")
    def test_restore_sync_starts_browser_when_not_from_browser(self, run_mock: mock.Mock, _sleep: mock.Mock) -> None:
        ct.restore_mpe_audio_services(restart_browser=True)
        started = _systemctl_calls(run_mock, "start")
        self.assertIn("touch-patch-browser", started)
        self.assertIn("surge-xt-cli", started)


if __name__ == "__main__":
    unittest.main()
