"""Standalone calibration Surge + dsnoop capture helpers."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from patch_browser import calibration_standalone as cs


class ResolveStandaloneInterfaceTests(unittest.TestCase):
    @mock.patch.object(cs, "_list_surge_devices", return_value="Output Audio Device: [0.3] : ALSA.Sound Blaster Direct hardware\n")
    @mock.patch.object(cs, "_interface_is_sound_blaster", return_value=True)
    @mock.patch.object(cs, "run_detect_audio_device")
    def test_resolve_interface_from_detect_script(
        self, detect_mock: mock.Mock, _sb: mock.Mock, _list: mock.Mock
    ) -> None:
        detect_mock.return_value = {
            "DEVICE_ID": "0.4",
            "DEVICE_NAME": "Front output",
            "TIER": "1",
        }
        iface = cs.resolve_surge_standalone_interface(
            Path("/fake/cli"), detect_script=Path("/fake/detect.sh")
        )
        self.assertEqual(iface, "0.3")

    @mock.patch.object(cs, "run_detect_audio_device")
    def test_rejects_headphone_tier(self, detect_mock: mock.Mock) -> None:
        detect_mock.return_value = {
            "DEVICE_ID": "0.1",
            "DEVICE_NAME": "bcm2835 Headphones",
            "TIER": "3",
        }
        with self.assertRaises(RuntimeError):
            cs.resolve_surge_standalone_interface(
                Path("/fake/cli"), detect_script=Path("/fake/detect.sh")
            )


if __name__ == "__main__":
    unittest.main()
