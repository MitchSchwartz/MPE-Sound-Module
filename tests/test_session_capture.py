"""Tests for Sound Blaster mic → UAC2 gadget device resolution."""

from __future__ import annotations

import unittest

from patch_browser.session_capture import (
    resolve_blaster_mic_capture_device,
    resolve_uac2_playback_device,
)

MOCK_CARDS = """
 0 [S3        ]: USB-Audio - Sound Blaster Play! 3
 4 [UAC2Gadget]: UAC2_Gadget - USB Audio Passthrough
"""

MOCK_AREcord = """
hw:CARD=S3,DEV=0
plughw:CARD=S3,DEV=0
dsnoop:CARD=S3,DEV=0
"""

MOCK_APLAY = """
hw:CARD=UAC2Gadget,DEV=0
plughw:CARD=UAC2Gadget,DEV=0
"""


class SessionCaptureTests(unittest.TestCase):
    def test_resolve_blaster_mic_prefers_plughw_for_mono(self) -> None:
        dev = resolve_blaster_mic_capture_device(
            cards_text=MOCK_CARDS,
            arecord_list=MOCK_AREcord,
        )
        self.assertEqual(dev, "plughw:CARD=S3,DEV=0")

    def test_resolve_blaster_mic_fallback_plughw(self) -> None:
        dev = resolve_blaster_mic_capture_device(
            cards_text=MOCK_CARDS,
            arecord_list="",
        )
        self.assertEqual(dev, "plughw:CARD=S3,DEV=0")

    def test_resolve_uac2_playback(self) -> None:
        dev = resolve_uac2_playback_device(
            cards_text=MOCK_CARDS,
            aplay_list=MOCK_APLAY,
        )
        self.assertEqual(dev, "hw:CARD=UAC2Gadget,DEV=0")

    def test_missing_cards_returns_none(self) -> None:
        self.assertIsNone(resolve_blaster_mic_capture_device(cards_text="", arecord_list=""))
        self.assertIsNone(resolve_uac2_playback_device(cards_text="", aplay_list=""))


if __name__ == "__main__":
    unittest.main()
