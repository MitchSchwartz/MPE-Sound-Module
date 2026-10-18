"""The calibration halts when its readings stop following the patch."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


def _load_cal():
    module_path = Path(__file__).resolve().parent.parent / "scripts" / "calibrate-patch-normalization.py"
    spec = importlib.util.spec_from_file_location("calibrate_patch_normalization", module_path)
    cal = importlib.util.module_from_spec(spec)
    sys.modules["calibrate_patch_normalization"] = cal
    spec.loader.exec_module(cal)
    return cal


class PlateauGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cal = _load_cal()

    def test_the_2026_10_17_run_halts_on_its_fifth_patch(self) -> None:
        # Strike / sustain of that run's first patches: one real reading, then stuck.
        readings = [(-16.8, -22.6), (-20.6, -20.2), (-20.7, -20.3), (-20.5, -20.1),
                    (-20.5, -20.2), (-20.6, -20.2)]
        guard = self.cal.PlateauGuard()
        halted_at = None
        for i, (strike, sustain) in enumerate(readings):
            if guard.add(Path(f"/p/{i}.fxp"), strike, sustain):
                halted_at = i
                break
        self.assertEqual(halted_at, 4)

    def test_patches_that_differ_never_halt(self) -> None:
        guard = self.cal.PlateauGuard()
        for i, strike in enumerate([-20.0, -20.2, -24.0, -20.1, -20.3, -31.0, -20.0, -20.2]):
            self.assertIsNone(guard.add(Path(f"/p/{i}.fxp"), strike, strike - 2.0))

    def test_a_matching_strike_with_a_different_sustain_is_not_a_plateau(self) -> None:
        guard = self.cal.PlateauGuard()
        for i in range(8):
            self.assertIsNone(guard.add(Path(f"/p/{i}.fxp"), -20.0, -20.0 - 3.0 * (i % 2)))

    def test_the_halt_names_every_patch_on_the_plateau(self) -> None:
        guard = self.cal.PlateauGuard()
        paths = [Path(f"/p/{i}.fxp") for i in range(4)]
        out = None
        for p in paths:
            out = guard.add(p, -20.6, -20.2)
        self.assertEqual(out, paths)


class RollBackTests(unittest.TestCase):
    def test_entries_return_to_their_run_start_state(self) -> None:
        cal = _load_cal()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "norm.json"
            snapshot = {"a": {"gain_db": 1.0, "user_trim_db": -3.0}, "keep": {"gain_db": 2.0}}
            path.write_text(json.dumps({
                "a": {"gain_db": 9.0, "user_trim_db": -3.0},
                "b": {"gain_db": 9.0},
                "keep": {"gain_db": 5.0},
            }))
            cal.roll_back_entries(path, snapshot, ["a", "b"])
            data = json.loads(path.read_text())
        self.assertEqual(data["a"], {"gain_db": 1.0, "user_trim_db": -3.0})
        self.assertNotIn("b", data)
        self.assertEqual(data["keep"], {"gain_db": 5.0})


if __name__ == "__main__":
    unittest.main()
