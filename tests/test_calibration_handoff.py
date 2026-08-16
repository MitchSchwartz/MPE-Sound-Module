"""Unit tests for browser-launched calibration teardown handoff (no systemd on Pi)."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

from patch_browser import calibration_teardown as ct
from patch_browser.calibration_constants import (
    CALIBRATION_LOADER_SCRIPT,
    CALIBRATE_WITH_LOADER_SCRIPT,
    MPE_CALIB_FROM_BROWSER,
    REPO_ROOT,
)
from patch_browser.touch_ui_enums import CalibrateMode

import sys

if not isinstance(sys.modules.get("pygame"), mock.MagicMock):
    # This module's mocks (e.g. `screen = mock.Mock()` in
    # CalibrationLoaderLaunchTests) rely on MagicMock's magic-method
    # fallback (__rsub__ etc.) to survive real pygame draw calls. A
    # plain "not in sys.modules" guard is order-dependent: any test
    # module that runs first and installs a leaner, non-magic pygame
    # stub (valid for its own purposes) leaves this one broken.
    sys.modules["pygame"] = mock.MagicMock()

from patch_browser.touch_browser_normalization import TouchBrowserNormalizationMixin


def _systemctl_calls(run_mock: mock.Mock, verb: str) -> list[str]:
    units: list[str] = []
    for call in run_mock.call_args_list:
        args = list(call.args[0])
        if len(args) >= 4 and args[:3] == ["sudo", "systemctl", verb]:
            unit = args[3]
            if unit.endswith(".service"):
                unit = unit[: -len(".service")]
            units.append(unit)
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
    @mock.patch("patch_browser.calibration_teardown.subprocess.run")
    def test_restore_does_not_systemd_restart_browser_when_from_browser(
        self,
        run_mock: mock.Mock,
        _sleep: mock.Mock,
    ) -> None:
        os.environ[MPE_CALIB_FROM_BROWSER] = "1"
        ct.restore_mpe_audio_services(restart_browser=True)
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


class CalibrationLoaderLaunchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mixin = TouchBrowserNormalizationMixin.__new__(TouchBrowserNormalizationMixin)
        self.mixin._pending_calibrate_mode = CalibrateMode.MISSING_ONLY
        self.mixin._evdev_bridge = None

    def test_calibrate_with_loader_script_resolves_to_repo_root(self) -> None:
        self.assertEqual(CALIBRATE_WITH_LOADER_SCRIPT, REPO_ROOT / "scripts" / "calibrate-with-loader.sh")
        self.assertTrue(CALIBRATE_WITH_LOADER_SCRIPT.is_file())

    @mock.patch("patch_browser.touch_browser_normalization.os.execv")
    def test_launch_calibration_loader_execs_loader_directly(
        self,
        execv_mock: mock.Mock,
    ) -> None:
        self.mixin.screen = mock.Mock()
        self.mixin.theme = mock.Mock()
        self.mixin._launch_calibration_loader()
        execv_mock.assert_called_once()
        argv = execv_mock.call_args.args[1]
        self.assertEqual(argv[0], sys.executable)
        self.assertEqual(Path(argv[2]), CALIBRATION_LOADER_SCRIPT)
        self.assertNotIn("--force", argv)

    @mock.patch("patch_browser.touch_browser_normalization.os.execv")
    def test_launch_calibration_loader_force_appends_flag(
        self,
        execv_mock: mock.Mock,
    ) -> None:
        self.mixin.screen = mock.Mock()
        self.mixin.theme = mock.Mock()
        self.mixin._pending_calibrate_mode = CalibrateMode.FORCE_FULL
        self.mixin._launch_calibration_loader()
        argv = execv_mock.call_args.args[1]
        self.assertEqual(Path(argv[2]), CALIBRATION_LOADER_SCRIPT)
        self.assertEqual(argv[-1], "--force")

    @mock.patch("patch_browser.touch_browser_normalization.sys.exit")
    @mock.patch("patch_browser.touch_browser_normalization.os.execv")
    def test_launch_calibration_loader_execv_failure_exits_cleanly(
        self,
        execv_mock: mock.Mock,
        exit_mock: mock.Mock,
    ) -> None:
        self.mixin.screen = mock.Mock()
        self.mixin.theme = mock.Mock()
        execv_mock.side_effect = OSError("exec failed")
        self.mixin._launch_calibration_loader()
        exit_mock.assert_called_once_with(1)


if __name__ == "__main__":
    unittest.main()
