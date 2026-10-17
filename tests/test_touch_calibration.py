"""Tests for touch floor writes during normalization calibration."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from patch_browser.patch_pressure import (
    PatchPressureStore,
    compute_touch_calibration_floor,
    resolve_light_touch_target,
)


class TouchCalibrationBatchTests(unittest.TestCase):
    def test_batch_writes_cal_floors_from_cohort_median(self) -> None:
        measurements = [
            ("Quiet", -36.0, -38.0, -20.0),
            ("Mid", -30.0, -31.0, -29.0),
            ("Loud", -24.0, -26.0, -16.0),
        ]
        target = resolve_light_touch_target([v for _, v, _, _ in measurements])
        self.assertAlmostEqual(target, -30.0)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pressure.json"
            store = PatchPressureStore(path)
            for name, lufs_light, lufs_strike, lufs_sustain in measurements:
                floor = compute_touch_calibration_floor(
                    lufs_light, target, lufs_strike, lufs_sustain
                )
                store.set_calibration(name, floor, lufs_light)
            store.save()

            self.assertAlmostEqual(store.get_calibrated_floor("Quiet"), 0.333, places=2)
            self.assertAlmostEqual(store.get_calibrated_floor("Mid"), 0.0)
            self.assertAlmostEqual(store.get_calibrated_floor("Loud"), 0.0)

    def test_zero_floor_entry_is_stored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pressure.json"
            store = PatchPressureStore(path)
            store.set_calibration("Mid", 0.0, -30.0)
            store.save()
            self.assertAlmostEqual(store.get_calibrated_floor("Mid"), 0.0)


if __name__ == "__main__":
    unittest.main()


class TouchCalibrationWritesKeyedEntryTests(unittest.TestCase):
    """The calibration script writes Touch under the key the browser reads."""

    def test_keyed_entry_is_updated_and_keeps_the_user_offset(self) -> None:
        import importlib.util
        import json
        import sys

        module_path = Path(__file__).resolve().parent.parent / "scripts" / "calibrate-patch-normalization.py"
        spec = importlib.util.spec_from_file_location("calibrate_patch_normalization", module_path)
        cal = importlib.util.module_from_spec(spec)
        sys.modules["calibrate_patch_normalization"] = cal
        spec.loader.exec_module(cal)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            third = root / "patches_3rdparty"
            patch = third / "Dan Maurer" / "Keys" / "Forte Piano.fxp"
            patch.parent.mkdir(parents=True)
            patch.write_bytes(b"x")
            key = "thirdparty:Dan Maurer/Keys/Forte Piano"
            store_path = root / "pressure.json"
            store_path.write_text(json.dumps({key: {"cal_floor": 0.5, "user_touch_offset": 0.2}}))

            cal.write_touch_calibrations(
                store_path,
                [("Forte Piano", -30.0, -31.0, -29.0, patch),
                 ("Other", -30.0, -31.0, -29.0, third / "X" / "Other.fxp")],
                [third],
            )

            data = json.loads(store_path.read_text())
            self.assertNotIn("Forte Piano", data)  # no bare-stem entry beside the keyed one
            self.assertEqual(data[key]["user_touch_offset"], 0.2)
            self.assertNotEqual(data[key]["cal_floor"], 0.5)
