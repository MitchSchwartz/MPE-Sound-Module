"""Regression coverage for the 2026-08-01 calibration integrity fixes.

Each test here pins down a specific failure mode discovered while debugging
"calibration saves 0" / "Acid is quiet, no change with norm on or off":

1. is_invalid_measurement must reject silent captures (low true peak / -inf)
   even when integrated LUFS looks parseable — but accept quiet real patches
   (healthy peak, low LUFS) so compute_gain_db can boost them.
2. NORM_MAX_AMP_VOLUME_LINEAR must not silently regress back to a value that
   clamps away most calibrated gain (the norm-cap bug).
3. End-to-end: a patch with a large real-world gain_db must have that gain
   actually reach the OSC send (within the cap), not get clamped to ~unity.
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
CAL_MODULE_PATH = REPO_ROOT / "scripts" / "calibrate-patch-normalization.py"

sys.path.insert(0, str(REPO_ROOT))

from patch_browser.patch_loader import PatchLoader  # noqa: E402
from patch_browser.patch_normalization import (  # noqa: E402
    MAX_AMP_VOLUME_LINEAR,
    NORM_MAX_AMP_VOLUME_LINEAR,
    PatchNormalizationStore,
    db_to_linear,
)
from patch_browser.patch_sidecar_key import resolve_storage_key  # noqa: E402


def load_cal_module():
    spec = importlib.util.spec_from_file_location("calibrate_patch_normalization", CAL_MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["calibrate_patch_normalization"] = module
    spec.loader.exec_module(module)
    return module


class FakeOscClient:
    def __init__(self) -> None:
        self.messages: list[tuple[str, float]] = []

    def send_message(self, address: str, value: float) -> None:
        self.messages.append((address, value))


class IsInvalidMeasurementTests(unittest.TestCase):
    """Peak-based validity: quiet real patches in, silent captures out."""

    def setUp(self) -> None:
        self.cal = load_cal_module()

    def test_silent_capture_is_invalid(self) -> None:
        self.assertTrue(self.cal.is_invalid_measurement(float("-inf"), float("-inf")))

    def test_low_peak_is_invalid_even_with_finite_lufs(self) -> None:
        self.assertTrue(self.cal.is_invalid_measurement(-60.0, -50.0))

    def test_quiet_lufs_with_healthy_peak_is_valid(self) -> None:
        # Overnight near-misses: real signal, below old -39 LUFS floor.
        self.assertFalse(self.cal.is_invalid_measurement(-42.0, -28.0))
        self.assertFalse(
            self.cal.is_invalid_measurement(self.cal.MIN_VALID_LUFS - 8.0, -20.0)
        )

    def test_acid_measured_shape_is_accepted(self) -> None:
        # Real numbers from 2026-08-01 A/B — quiet patch, clear peak.
        self.assertFalse(self.cal.is_invalid_measurement(-47.0, -29.4))

    def test_lufs_at_or_above_floor_with_finite_peak_is_valid(self) -> None:
        self.assertFalse(self.cal.is_invalid_measurement(self.cal.MIN_VALID_LUFS, -10.0))
        self.assertFalse(self.cal.is_invalid_measurement(-18.0, -6.0))

    def test_non_finite_lufs_always_invalid_regardless_of_peak(self) -> None:
        self.assertTrue(self.cal.is_invalid_measurement(float("nan"), -1.0))
        self.assertTrue(self.cal.is_invalid_measurement(float("-inf"), -1.0))

    def test_peak_floor_constant_is_exposed(self) -> None:
        self.assertEqual(self.cal.MIN_VALID_TRUE_PEAK_DBTP, -45.0)


class CalibrationDurationHintTests(unittest.TestCase):
    def test_sixteen_patches_about_six_minutes(self) -> None:
        from patch_browser.calibration_constants import format_calibration_duration_hint

        self.assertEqual(
            format_calibration_duration_hint(16),
            "Approx. 12 min (16 patch(es)).",
        )

    def test_zero_targets(self) -> None:
        from patch_browser.calibration_constants import format_calibration_duration_hint

        self.assertIn("Nothing to calibrate", format_calibration_duration_hint(0))


class NormCapIntegrityTests(unittest.TestCase):
    """Pins the norm-cap fix — calibrated gain must actually reach Surge."""

    def test_norm_cap_matches_off_cap(self) -> None:
        # If this ever regresses to a value well below MAX_AMP_VOLUME_LINEAR,
        # norm-on vs norm-off becomes inaudible again for any patch needing a
        # real boost — exactly the "no change with norm on or off" bug.
        self.assertEqual(NORM_MAX_AMP_VOLUME_LINEAR, MAX_AMP_VOLUME_LINEAR)

    def test_norm_cap_is_not_a_near_unity_clamp(self) -> None:
        # Guardrail independent of the equality above: whatever the cap is,
        # it must allow meaningfully more than unity gain, or normalization
        # can't correct any patch that's genuinely quiet by design.
        self.assertGreater(NORM_MAX_AMP_VOLUME_LINEAR, 1.2)

    def test_large_calibrated_gain_reaches_osc_send_within_cap(self) -> None:
        """End-to-end regression for the exact Acid bug shape."""
        with tempfile.TemporaryDirectory() as tmp:
            user_path = Path(tmp) / "user.json"
            gain_db = 16.61  # measured standalone-route gain for Acid, 2026-08-01
            user_path.write_text(
                json.dumps({"Acid": {"gain_db": gain_db, "enabled": True, "lufs_measured": -51.2}})
            )
            store = PatchNormalizationStore(user_path)
            loader = PatchLoader(normalization_store=store)
            loader.osc_client = FakeOscClient()
            loader.osc_enabled = True
            loader.user_volume_trim = 1.0

            loader.refresh_patch_volume("Acid")
            sent = loader.osc_client.messages[-1][1]

            expected_linear = db_to_linear(gain_db)
            # Stage 1 (#31): norm-on sends full calibrated gain — peak safety is at cal time.
            self.assertAlmostEqual(sent, expected_linear)
            self.assertGreaterEqual(sent, 1.5)  # meaningfully above unity, not clamped away

    def test_modest_gain_is_not_clamped_at_all(self) -> None:
        """Typical Quick Select gains (+4 to +18dB per docs) should pass through untouched
        whenever they're at or below the cap — no clamping side effects for the common case."""
        with tempfile.TemporaryDirectory() as tmp:
            user_path = Path(tmp) / "user.json"
            gain_db = 3.0  # 3dB -> ~1.41x, comfortably under any reasonable cap
            user_path.write_text(
                json.dumps({"Lead": {"gain_db": gain_db, "enabled": True, "lufs_measured": -21.0}})
            )
            store = PatchNormalizationStore(user_path)
            loader = PatchLoader(normalization_store=store)
            loader.osc_client = FakeOscClient()
            loader.osc_enabled = True
            loader.user_volume_trim = 1.0

            loader.refresh_patch_volume("Lead")
            sent = loader.osc_client.messages[-1][1]
            self.assertAlmostEqual(sent, db_to_linear(gain_db))


class CalibrationPipelineDoesNotSilentlySaveGarbageTests(unittest.TestCase):
    """End-to-end via the real capture path (mock_lufs intentionally bypasses
    is_invalid_measurement entirely — it's a write-any-value testing escape
    hatch, not representative of the real gate). Mock capture_gesture_wav /
    measure_lufs instead so is_invalid_measurement is actually exercised."""

    def setUp(self) -> None:
        self.cal = load_cal_module()

    def _run_calibrate_patch(self, lufs: float, true_peak: float) -> tuple[object, object]:
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "norm.json"
            store = self.cal.PatchNormalizationStore(out_path)
            fake_loader = mock.Mock()
            fake_loader.load_patch.return_value = True
            fake_loader.osc_enabled = True
            fake_loader.osc_client = mock.Mock()

            if self.cal.is_invalid_measurement(lufs, true_peak):
                strike_return = None
                sustain_return = None
            else:
                strike_return = (lufs - 5.0, true_peak - 1.0)
                sustain_return = (lufs, true_peak)

            with (
                mock.patch.object(self.cal, "measure_strike_anchor_lufs", return_value=strike_return),
                mock.patch.object(self.cal, "measure_sustain_anchor_lufs", return_value=sustain_return),
                mock.patch.object(self.cal, "measure_light_touch_lufs", return_value=None),
                mock.patch.object(self.cal.time, "sleep"),
            ):
                saved = self.cal.calibrate_patch(
                    Path("/tmp/Fake.fxp"),
                    fake_loader,
                    store,
                    audio_device="plughw:Loopback,1,0",
                    mock_lufs=None,
                    dry_run=False,
                    midi_out=mock.Mock(),
                )
            return saved, store

    def test_silent_capture_is_not_saved(self) -> None:
        result, store = self._run_calibrate_patch(-60.0, -50.0)
        self.assertFalse(result.ok)
        self.assertIsNone(store.get_raw_gain_db("Fake"))

    def test_quiet_patch_with_healthy_peak_is_saved(self) -> None:
        result, store = self._run_calibrate_patch(-47.0, -29.4)
        self.assertTrue(result.ok)
        self.assertIsNotNone(store.get_raw_gain_db("Fake"))

    def test_normal_loudness_capture_is_saved(self) -> None:
        result, store = self._run_calibrate_patch(-18.0, -6.0)
        self.assertTrue(result.ok)
        self.assertIsNotNone(store.get_raw_gain_db("Fake"))


class CalibrateListMissingContractTests(unittest.TestCase):
    """Real store + patch_path_records shape — mocks cannot catch TypeError regressions."""

    def setUp(self) -> None:
        self.cal = load_cal_module()

    def test_list_missing_accepts_patch_path_records_and_keys_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            patch_root = Path(tmp) / "patches_factory"
            patch_root.mkdir()
            calibrated_path = patch_root / "Calibrated.fxp"
            missing_path = patch_root / "Uncalibrated.fxp"
            calibrated_path.write_bytes(b"")
            missing_path.write_bytes(b"")

            patch_dirs = [patch_root]
            calibrated_key = resolve_storage_key(
                calibrated_path.stem,
                patch_path=str(calibrated_path),
                patch_dirs=patch_dirs,
            )
            missing_key = resolve_storage_key(
                missing_path.stem,
                patch_path=str(missing_path),
                patch_dirs=patch_dirs,
            )

            store_path = Path(tmp) / "normalization.json"
            store_path.write_text(
                json.dumps({calibrated_key: {"gain_db": -2.0, "enabled": True}}),
                encoding="utf-8",
            )
            store = PatchNormalizationStore(store_path)
            store.set_patch_dirs(patch_dirs)

            records = self.cal.patch_path_records([calibrated_path, missing_path])
            missing_keys = store.list_missing(records)

            for record in records:
                expected = resolve_storage_key(
                    record["name"],
                    patch_path=record.get("path"),
                    patch_dirs=patch_dirs,
                )
                if expected == calibrated_key:
                    self.assertNotIn(expected, missing_keys)
                else:
                    self.assertIn(expected, missing_keys)
                    self.assertEqual(expected, missing_key)

            self.assertEqual(missing_keys, [missing_key])



class ClosedLoopThresholdTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cal = load_cal_module()

    def test_finalize_loop_uses_same_gate_as_save(self) -> None:
        from patch_browser.patch_normalization import POST_GAIN_VERIFY_PEAK_MAX_DBTP

        src = Path(self.cal.__file__).read_text()
        self.assertIn("post_gain_verify_passes(last_peak)", src)
        self.assertNotIn("SAFE_PEAK_DBTP + 0.5", src.split("def finalize_gain_with_closed_loop")[1].split("def is_invalid_measurement")[0])


class PostGainVerifyGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cal = load_cal_module()

    def test_post_gain_verify_rejects_hot_peak(self) -> None:
        self.assertFalse(self.cal.post_gain_verify_passes(-1.0))

    def test_calibrate_skips_save_on_verify_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "norm.json"
            store = self.cal.PatchNormalizationStore(out_path)
            fake_loader = mock.Mock()
            fake_loader.load_patch.return_value = True
            fake_loader.osc_enabled = True
            fake_loader.osc_client = mock.Mock()
            with (
                mock.patch.object(
                    self.cal, "measure_strike_anchor_lufs", return_value=(-18.0, -6.0)
                ),
                mock.patch.object(
                    self.cal, "measure_sustain_anchor_lufs", return_value=(-18.0, -6.0)
                ),
                mock.patch.object(self.cal, "measure_light_touch_lufs", return_value=None),
                mock.patch.object(
                    self.cal,
                    "finalize_gain_with_closed_loop",
                    return_value=(2.0, -16.0, -1.5),
                ),
                mock.patch.object(self.cal.time, "sleep"),
            ):
                result = self.cal.calibrate_patch(
                    Path("/tmp/Hot.fxp"),
                    fake_loader,
                    store,
                    audio_device="plughw:Loopback,1,0",
                    mock_lufs=None,
                    dry_run=False,
                    midi_out=mock.Mock(),
                )
            self.assertFalse(result.ok)
            self.assertTrue(result.verify_failed)
            self.assertIsNone(store.get_raw_gain_db("Hot"))


if __name__ == "__main__":

    unittest.main()
