"""Tests for looper ALSA device resolution."""

from __future__ import annotations

import unittest
from unittest import mock

from patch_browser.looper_devices import (
    resolve_looper_capture_device,
    resolve_sound_blaster_playback_device,
)

MOCK_CARDS = """
 2 [Loopback ]: Loopback
 0 [S3        ]: USB-Audio - Sound Blaster Play! 3
"""

MOCK_APLAY = """
hw:CARD=S3,DEV=0
plughw:CARD=S3,DEV=0
dsnoop:CARD=S3,DEV=0
"""


class LooperDeviceTests(unittest.TestCase):
    def test_resolve_looper_capture(self) -> None:
        dev = resolve_looper_capture_device(cards_text=MOCK_CARDS)
        self.assertEqual(dev, "plughw:2,1,0")

    def test_resolve_sound_blaster_playback_prefers_plughw(self) -> None:
        dev = resolve_sound_blaster_playback_device(
            cards_text=MOCK_CARDS,
            aplay_list=MOCK_APLAY,
        )
        self.assertEqual(dev, "plughw:CARD=S3,DEV=0")

    @mock.patch("patch_browser.looper_devices._aplay_list", return_value=MOCK_APLAY)
    @mock.patch("patch_browser.looper_devices._read_cards", return_value=MOCK_CARDS)
    @mock.patch("patch_browser.looper_devices.ensure_snd_aloop")
    def test_prepare_looper_audio_path(
        self,
        modprobe_mock: mock.Mock,
        _cards_mock: mock.Mock,
        _aplay_mock: mock.Mock,
    ) -> None:
        from patch_browser.looper_devices import prepare_looper_audio_path

        capture, playback = prepare_looper_audio_path(load_loopback=True)
        modprobe_mock.assert_called_once()
        self.assertIn("Loopback", capture)
        self.assertEqual(playback, "plughw:CARD=S3,DEV=0")


if __name__ == "__main__":
    unittest.main()
