"""Tests for ALSA loopback helpers used during patch calibration."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from patch_browser import calibration_loopback as lb


class CalibrationLoopbackTests(unittest.TestCase):
    def test_loopback_card_number_from_proc_cards(self) -> None:
        cards = """
 0 [Headphones     ]: bcm2835_headpho - bcm2835 Headphones
 2 [Loopback       ]: Loopback - Loopback PCM
 1 [S3             ]: USB-Audio - Sound Blaster Play! 3
"""
        self.assertEqual(lb.loopback_card_number(cards), 2)

    def test_resolve_loopback_capture_device_uses_card_index(self) -> None:
        cards = " 2 [Loopback       ]: Loopback - Loopback PCM\n"
        self.assertEqual(
            lb.resolve_loopback_capture_device(cards_text=cards),
            "plughw:2,1,0",
        )

    def test_parse_surge_loopback_interface_prefers_direct_hardware(self) -> None:
        blob = """
Output Audio Device 0 [0.19] : Loopback, Loopback PCM; Direct sample mixing (1)
Output Audio Device 1 [0.20] : Loopback, Loopback PCM; Direct hardware device without any conversions (1)
"""
        self.assertEqual(lb.parse_surge_loopback_interface(blob), "0.20")

    def test_parse_surge_loopback_interface_raises_when_missing(self) -> None:
        with self.assertRaises(RuntimeError):
            lb.parse_surge_loopback_interface("Output Audio Device 0 [0.4] : Sound Blaster\n")

    @mock.patch("patch_browser.calibration_loopback.time.sleep")
    @mock.patch("patch_browser.calibration_loopback.read_asound_cards")
    @mock.patch("patch_browser.calibration_loopback.subprocess.run")
    def test_ensure_snd_aloop_waits_for_card(
        self,
        run_mock: mock.Mock,
        read_mock: mock.Mock,
        _sleep: mock.Mock,
    ) -> None:
        read_mock.side_effect = ["", " 2 [Loopback       ]: Loopback\n"]
        lb.ensure_snd_aloop(timeout_s=1.0)
        run_mock.assert_called_once()

    @mock.patch("patch_browser.calibration_loopback.time.sleep")
    @mock.patch("patch_browser.calibration_loopback.read_asound_cards", return_value="")
    @mock.patch("patch_browser.calibration_loopback.subprocess.run")
    def test_ensure_snd_aloop_raises_when_card_never_appears(
        self,
        _run_mock: mock.Mock,
        _read_mock: mock.Mock,
        _sleep: mock.Mock,
    ) -> None:
        with self.assertRaises(RuntimeError):
            lb.ensure_snd_aloop(timeout_s=0.3)

    @mock.patch("patch_browser.calibration_loopback.subprocess.run")
    def test_resolve_surge_loopback_interface_from_cli(self, run_mock: mock.Mock) -> None:
        run_mock.return_value = mock.Mock(
            stdout="Output Audio Device 1 [0.20] : Loopback; Direct hardware device (1)\n",
            stderr="",
        )
        iface = lb.resolve_surge_loopback_interface(Path("/fake/surge-xt-cli"))
        self.assertEqual(iface, "0.20")

    @mock.patch("patch_browser.calibration_loopback.subprocess.run")
    def test_resolve_surge_loopback_interface_falls_back_to_card_index(
        self, run_mock: mock.Mock
    ) -> None:
        run_mock.return_value = mock.Mock(stdout="Output Audio Device 0 [0.4] : Sound Blaster\n", stderr="")
        with mock.patch(
            "patch_browser.calibration_loopback.read_asound_cards",
            return_value=" 5 [Loopback       ]: Loopback - Loopback PCM\n",
        ):
            iface = lb.resolve_surge_loopback_interface(Path("/fake/surge-xt-cli"))
        self.assertEqual(iface, "5.0")


if __name__ == "__main__":
    unittest.main()
