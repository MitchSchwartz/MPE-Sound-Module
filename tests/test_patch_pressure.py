"""Tests for per-patch pressure floor store and remap math."""

from __future__ import annotations

import json
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from patch_browser.patch_pressure import (
    DEFAULT_PRESSURE_FLOOR,
    PRESSURE_OFFSET_MAX,
    PRESSURE_OFFSET_MIN,
    PatchPressureStore,
    clamp_touch_offset,
    compute_pressure_floor,
    effective_floor_from_offset,
    effective_pressure_mult,
    remap_pressure_7bit,
    resolve_light_touch_target,
)
from patch_browser.pressure_midi import find_remap_output_port_index, normalize_midi_bytes, remap_midi_message


class PatchPressureTests(unittest.TestCase):
    def test_effective_mult_pins_full_press(self) -> None:
        for floor in (0.0, 0.35, 0.9):
            self.assertAlmostEqual(effective_pressure_mult(1.0, floor), 1.0)

    def test_floor_raises_light_touch(self) -> None:
        self.assertGreater(effective_pressure_mult(0.0, 0.5), effective_pressure_mult(0.0, 0.0))
        self.assertGreater(remap_pressure_7bit(20, 0.5), remap_pressure_7bit(20, 0.0))

    def test_user_touch_offset_persists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pressure.json"
            store = PatchPressureStore(path)
            store.set_calibration("Duduk", 0.52, -30.0)
            store.set_user_touch_offset("Duduk", 0.1)
            self.assertAlmostEqual(store.get_user_touch_offset("Duduk"), 0.1)
            self.assertAlmostEqual(store.get_effective_floor("Duduk"), 0.62)
            saved = json.loads(path.read_text())
            self.assertAlmostEqual(saved["Duduk"]["user_touch_offset"], 0.1)
            self.assertNotIn("user_floor", saved["Duduk"])

    def test_negative_offset_reduces_below_calibrated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PatchPressureStore(Path(tmp) / "pressure.json")
            store.set_calibration("Lead", 0.4, -30.0)
            store.set_user_touch_offset("Lead", -0.15)
            self.assertAlmostEqual(store.get_effective_floor("Lead"), 0.25)

    def test_offset_clamps_at_zero_floor(self) -> None:
        baseline = 0.2
        self.assertAlmostEqual(effective_floor_from_offset(baseline, -0.5), 0.0)

    def test_legacy_user_floor_migrates_to_offset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pressure.json"
            path.write_text(
                json.dumps({"Lead": {"cal_floor": 0.35, "user_floor": 0.55, "lufs_light": -30.0}})
            )
            store = PatchPressureStore(path)
            self.assertAlmostEqual(store.get_user_touch_offset("Lead"), 0.2)
            self.assertAlmostEqual(store.get_effective_floor("Lead"), 0.55)

    def test_clear_user_touch_offset_resets_to_calibrated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pressure.json"
            store = PatchPressureStore(path)
            store.set_calibration("Lead", 0.35, -30.0)
            store.set_user_touch_offset("Lead", 0.2, persist=False)
            store.save()
            store.clear_user_touch_offset("Lead")
            self.assertAlmostEqual(store.get_user_touch_offset("Lead"), 0.0)
            self.assertAlmostEqual(store.get_effective_floor("Lead"), 0.35)

    def test_clear_user_touch_offset_without_cal_resets_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pressure.json"
            store = PatchPressureStore(path)
            store.set_user_touch_offset("Lead", 0.15)
            store.clear_user_touch_offset("Lead")
            self.assertAlmostEqual(store.get_effective_floor("Lead"), DEFAULT_PRESSURE_FLOOR)

    def test_format_touch_offset(self) -> None:
        store = PatchPressureStore(Path("/tmp/unused.json"))
        self.assertEqual(store.format_touch_offset(0.0), "0")
        self.assertEqual(store.format_touch_offset(0.12), "+12")
        self.assertEqual(store.format_touch_offset(-0.08), "-8")

    def test_touch_offset_range(self) -> None:
        self.assertAlmostEqual(clamp_touch_offset(-0.9), PRESSURE_OFFSET_MIN)
        self.assertAlmostEqual(clamp_touch_offset(0.9), PRESSURE_OFFSET_MAX)

    def test_compute_pressure_floor_from_shortfall(self) -> None:
        self.assertAlmostEqual(compute_pressure_floor(-28.0, -28.0), 0.0)
        self.assertAlmostEqual(compute_pressure_floor(-34.0, -28.0), 6.0 / 18.0)
        self.assertAlmostEqual(compute_pressure_floor(-55.0, -28.0), 0.9)

    def test_resolve_light_touch_target(self) -> None:
        self.assertAlmostEqual(resolve_light_touch_target([-30.0, -28.0, -26.0]), -28.0)
        self.assertAlmostEqual(resolve_light_touch_target([-30.0]), -28.0)

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

    def test_find_remap_output_port_index(self) -> None:
        ports = [
            "Midi Through:Midi Through Port-0 14:0",
            "LUMI Keys BLOCK:LUMI Keys BLOCK MIDI 1 32:0",
        ]
        self.assertEqual(find_remap_output_port_index(ports), 0)

    def test_normalize_nested_rtmidi_payload(self) -> None:
        nested = ([0x91, 60, 100], 0.0)
        self.assertEqual(normalize_midi_bytes(nested), [0x91, 60, 100])
        self.assertEqual(
            remap_midi_message(nested, floor=0.5),
            [0x91, 60, 100],
        )


if __name__ == "__main__":
    unittest.main()
