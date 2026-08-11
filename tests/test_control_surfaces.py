"""Tests for ControlSurfaceMap dataclass behavior."""

from __future__ import annotations

import unittest

from patch_browser.control_surfaces import APC_MAP_MK1, LooperTransportAction
from patch_browser.control_surfaces.midi import looper_transport_from_message


class ControlSurfaceMapTests(unittest.TestCase):
    def test_grid_note_roundtrip(self) -> None:
        self.assertEqual(APC_MAP_MK1.grid_note(3, 2), 26)
        self.assertEqual(APC_MAP_MK1.grid_position(26), (3, 2))

    def test_looper_transport_from_message_uses_surface_channel(self) -> None:
        action = looper_transport_from_message(APC_MAP_MK1, [0x90, 82, 127])
        self.assertEqual(action, LooperTransportAction.RECORD)


if __name__ == "__main__":
    unittest.main()
