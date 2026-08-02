"""Tests for per-patch pressure floor store and remap math."""

from __future__ import annotations

import json
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from patch_browser.patch_pressure import (
    DEFAULT_PRESSURE_FLOOR,
    PatchPressureStore,
    effective_pressure_mult,
    remap_pressure_7bit,
)
from patch_browser.pressure_midi import remap_midi_message


class PatchPressureTests(unittest.TestCase):
    def test_effective_mult_pins_full_press(self) -> None:
        for floor in (0.0, 0.35, 0.9):
            self.assertAlmostEqual(effective_pressure_mult(1.0, floor), 1.0)

    def test_floor_raises_light_touch(self) -> None:
        self.assertGreater(effective_pressure_mult(0.0, 0.5), effective_pressure_mult(0.0, 0.0))
        self.assertGreater(remap_pressure_7bit(20, 0.5), remap_pressure_7bit(20, 0.0))

    def test_user_floor_persists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pressure.json"
            store = PatchPressureStore(path)
            store.set_user_floor("Duduk", 0.4)
            self.assertAlmostEqual(store.get_effective_floor("Duduk"), 0.4)
            saved = json.loads(path.read_text())
            self.assertAlmostEqual(saved["Duduk"]["user_floor"], 0.4)

    def test_clear_user_floor_resets_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pressure.json"
            store = PatchPressureStore(path)
            store.set_user_floor("Lead", 0.55)
            store.clear_user_floor("Lead")
            self.assertAlmostEqual(store.get_effective_floor("Lead"), DEFAULT_PRESSURE_FLOOR)

    def test_live_state_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            live = Path(tmp) / "live.json"
            with unittest.mock.patch("patch_browser.patch_pressure.LIVE_STATE_FILE", live):
                store = PatchPressureStore(Path(tmp) / "pressure.json")
                store.write_live_state("Guitar", 0.42)
                self.assertAlmostEqual(PatchPressureStore.read_live_floor(), 0.42)


class PressureMidiTests(unittest.TestCase):
    def test_channel_pressure_remapped_on_member_channel(self) -> None:
        msg = remap_midi_message([0xD2, 40], floor=0.5)
        self.assertEqual(msg[0], 0xD2)
        self.assertGreater(msg[1], 40)

    def test_master_channel_pressure_unchanged(self) -> None:
        msg = remap_midi_message([0xD0, 40], floor=0.5)
        self.assertEqual(msg, [0xD0, 40])

    def test_note_on_unchanged(self) -> None:
        msg = remap_midi_message([0x91, 60, 100], floor=0.5)
        self.assertEqual(msg, [0x91, 60, 100])


if __name__ == "__main__":
    unittest.main()
