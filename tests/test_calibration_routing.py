"""Loopback vs standalone calibration routing."""

from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
CAL_MODULE_PATH = REPO_ROOT / "scripts" / "calibrate-patch-normalization.py"


def load_cal_module():
    spec = importlib.util.spec_from_file_location("calibrate_patch_normalization", CAL_MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["calibrate_patch_normalization"] = module
    spec.loader.exec_module(module)
    return module


class ShouldUseLoopbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cal = load_cal_module()

    @mock.patch.dict(os.environ, {"MPE_AUDIO_PROFILE": "standalone"}, clear=False)
    @mock.patch.object(Path, "is_file", return_value=True)
    def test_standalone_profile_disables_loopback_on_pi(self, _is_file: mock.Mock) -> None:
        self.assertFalse(self.cal.should_use_loopback(None))

    @mock.patch.dict(os.environ, {"MPE_AUDIO_PROFILE": "usb-host"}, clear=False)
    @mock.patch.object(Path, "is_file", return_value=True)
    def test_usb_host_profile_enables_loopback_on_pi(self, _is_file: mock.Mock) -> None:
        self.assertTrue(self.cal.should_use_loopback(None))

    def test_explicit_flag_wins(self) -> None:
        self.assertTrue(self.cal.should_use_loopback(True))
        self.assertFalse(self.cal.should_use_loopback(False))



class StandaloneRestartTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cal = load_cal_module()
        import patch_browser.calibration_standalone as standalone

        self.standalone = standalone

    @mock.patch.dict(os.environ, {"MPE_AUDIO_PROFILE": "standalone"}, clear=False)
    def test_should_restart_surge_on_standalone_live_run(self) -> None:
        self.assertTrue(
            self.standalone.should_restart_surge_for_standalone(
                use_loopback=False, dry_run=False, mock_lufs=None
            )
        )

    def test_should_not_restart_when_loopback(self) -> None:
        self.assertFalse(
            self.standalone.should_restart_surge_for_standalone(
                use_loopback=True, dry_run=False, mock_lufs=None
            )
        )

    def test_resolve_standalone_capture_prefers_dsnoop(self) -> None:
        cards = " 1 [S3             ]: USB-Audio - Sound Blaster Play! 3\n"
        arecord = "dsnoop:CARD=S3,DEV=0\n    Sound Blaster Play! 3, USB Audio\n"
        dev = self.standalone.resolve_standalone_capture_device(
            cards_text=cards, arecord_list=arecord
        )
        self.assertEqual(dev, "dsnoop:CARD=S3,DEV=0")

if __name__ == "__main__":
    unittest.main()
