"""Tests for APC mk1 LED velocity helpers."""

from __future__ import annotations

import unittest

from patch_browser.control_surfaces import APC_MAP_MK1
from patch_browser.control_surfaces.apc_led import (
    ApcLedColor,
    led_note_on_bytes,
    transport_led_notes,
)


class ApcLedTests(unittest.TestCase):
    def test_led_note_on_bytes(self) -> None:
        self.assertEqual(
            led_note_on_bytes(note=82, color=ApcLedColor.RED_BLINK),
            [0x90, 82, 4],
        )

    def test_transport_led_notes_mk1(self) -> None:
        notes = transport_led_notes(APC_MAP_MK1)
        self.assertEqual(notes["record"], 82)
        self.assertEqual(notes["play_stop"], 86)
        self.assertEqual(notes["clear"], 89)


if __name__ == "__main__":
    unittest.main()
