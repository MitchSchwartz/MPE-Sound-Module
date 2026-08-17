"""Tests for per-patch normalization store and loader volume refresh."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from patch_browser.patch_loader import PatchLoader
from patch_browser.patch_normalization import PatchNormalizationStore


class FakeOscClient:
    def __init__(self) -> None:
        self.messages: list[tuple[str, float]] = []

    def send_message(self, address: str, value: float) -> None:
        self.messages.append((address, value))


class PatchNormalizationStoreTests(unittest.TestCase):
    def test_toggle_off_on_preserves_gain_and_applies_volume(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            user_path = Path(tmp) / "user.json"
            user_path.write_text(
                json.dumps(
                    {
                        "Lead": {
                            "gain_db": 6.0,
                            "enabled": True,
                            "lufs_measured": -24.0,
                        }
                    }
                )
            )
            store = PatchNormalizationStore(user_path)
            loader = PatchLoader(normalization_store=store)
            loader.osc_client = FakeOscClient()
            loader.osc_enabled = True
            loader.user_volume_trim = 0.8

            loader.refresh_patch_volume("Lead")
            on_volume = loader.osc_client.messages[-1][1]
            from patch_browser.patch_normalization import (
                NORM_MAX_AMP_VOLUME_LINEAR,
                db_to_linear,
                volume_fader_to_amp_linear,
            )
            from patch_browser.touch_ui_constants import VOLUME_MAX, VOLUME_MIN

            gain_linear = db_to_linear(6.0)
            expected_on = volume_fader_to_amp_linear(
                0.8,
                patch_gain_linear=gain_linear,
                cap=NORM_MAX_AMP_VOLUME_LINEAR,
                fader_min=VOLUME_MIN,
                fader_max=VOLUME_MAX,
                norm_active=True,
            )
            self.assertAlmostEqual(on_volume, expected_on)

            store.set_enabled("Lead", False)
            loader.refresh_patch_volume("Lead")
            off_volume = loader.osc_client.messages[-1][1]

            expected_off = volume_fader_to_amp_linear(
                0.8,
                patch_gain_linear=1.0,
                cap=1.5,
                fader_min=VOLUME_MIN,
                fader_max=VOLUME_MAX,
            )
            self.assertAlmostEqual(off_volume, expected_off)

            store.set_enabled("Lead", True)
            loader.refresh_patch_volume("Lead")
            on_again = loader.osc_client.messages[-1][1]
            self.assertAlmostEqual(on_volume, on_again)

            saved = json.loads(user_path.read_text())
            self.assertTrue(saved["Lead"]["enabled"])
            self.assertEqual(saved["Lead"]["gain_db"], 6.0)

    def test_load_merges_user_enabled_over_repo_calibration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            starter = Path(tmp) / "starter.json"
            user_path = Path(tmp) / "user.json"
            starter.write_text(
                json.dumps(
                    {
                        "Pad": {
                            "gain_db": -3.0,
                            "enabled": True,
                            "lufs_measured": -15.0,
                        }
                    }
                )
            )
            user_path.write_text(json.dumps({"Pad": {"enabled": False}}))

            with mock.patch(
                "patch_browser.patch_normalization.repo_starter_path",
                return_value=starter,
            ):
                store = PatchNormalizationStore(user_path)

            self.assertFalse(store.is_enabled("Pad"))
            self.assertIsNone(store.get_gain_db("Pad"))
            self.assertEqual(store.get_raw_gain_db("Pad"), -3.0)

            store.set_enabled("Pad", True)
            self.assertEqual(store.get_gain_db("Pad"), -3.0)

    def test_set_enabled_off_on_restores_starter_gain_after_minimal_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            starter = Path(tmp) / "starter.json"
            user_path = Path(tmp) / "user.json"
            starter.write_text(
                json.dumps({"Acid": {"gain_db": 12.0, "enabled": True, "lufs_measured": -40.0}})
            )
            user_path.write_text("{}")

            with mock.patch(
                "patch_browser.patch_normalization.repo_starter_path",
                return_value=starter,
            ):
                store = PatchNormalizationStore(user_path)
                store._data = {}
                store.set_enabled("Acid", False)
                self.assertFalse(store.is_enabled("Acid"))
                self.assertEqual(store.get_raw_gain_db("Acid"), 12.0)

                store.set_enabled("Acid", True)
                self.assertTrue(store.is_enabled("Acid"))
                self.assertEqual(store.get_raw_gain_db("Acid"), 12.0)

    def test_refresh_sends_volume_twice_when_norm_active(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            user_path = Path(tmp) / "user.json"
            user_path.write_text(
                json.dumps({"Lead": {"gain_db": 6.0, "enabled": True, "lufs_measured": -24.0}})
            )
            store = PatchNormalizationStore(user_path)
            loader = PatchLoader(normalization_store=store)
            loader.osc_client = FakeOscClient()
            loader.osc_enabled = True

            loader.refresh_patch_volume("Lead")
            self.assertEqual(len(loader.osc_client.messages), 4)
            self.assertEqual(loader.osc_client.messages[-1][1], loader.osc_client.messages[-2][1])

    def test_volume_trim_scales_when_norm_baseline_exceeds_cap(self) -> None:
        """Vol fader must not flatten when trim * baseline always hits the cap."""
        with tempfile.TemporaryDirectory() as tmp:
            user_path = Path(tmp) / "user.json"
            user_path.write_text(
                json.dumps({"Loud": {"gain_db": 18.0, "enabled": True, "lufs_measured": -40.0}})
            )
            store = PatchNormalizationStore(user_path)
            loader = PatchLoader(normalization_store=store)
            loader.osc_client = FakeOscClient()
            loader.osc_enabled = True

            loader.refresh_patch_volume("Loud")

            loader.set_volume(1.0)
            at_unity = loader.osc_client.messages[-1][1]

            loader.set_volume(0.5)
            at_half = loader.osc_client.messages[-1][1]

            loader.set_volume(0.0)
            at_mute = loader.osc_client.messages[-1][1]

            from patch_browser.patch_normalization import db_to_linear, volume_fader_to_amp_linear
            from patch_browser.touch_ui_constants import VOLUME_MAX, VOLUME_MIN

            gain_linear = db_to_linear(18.0)
            expected_unity = volume_fader_to_amp_linear(
                1.0,
                patch_gain_linear=gain_linear,
                cap=1.5,
                fader_min=VOLUME_MIN,
                fader_max=VOLUME_MAX,
                norm_active=True,
            )
            expected_half = volume_fader_to_amp_linear(
                0.5,
                patch_gain_linear=gain_linear,
                cap=1.5,
                fader_min=VOLUME_MIN,
                fader_max=VOLUME_MAX,
                norm_active=True,
            )
            expected_mute = volume_fader_to_amp_linear(
                0.0,
                patch_gain_linear=gain_linear,
                cap=1.5,
                fader_min=VOLUME_MIN,
                fader_max=VOLUME_MAX,
                norm_active=True,
            )

            self.assertAlmostEqual(at_unity, expected_unity)
            self.assertAlmostEqual(at_half, expected_half)
            self.assertAlmostEqual(at_mute, expected_mute)
            self.assertEqual(at_mute, 0.0)
            self.assertGreater(at_unity, at_half)
            self.assertGreater(at_half, at_mute)

    def test_volume_fader_db_linear_even_steps(self) -> None:
        import math

        from patch_browser.patch_normalization import volume_fader_to_amp_linear
        from patch_browser.touch_ui_constants import VOLUME_MAX, VOLUME_MIN

        cap = 1.5

        def at_pct(pct: float) -> float:
            trim = VOLUME_MIN + (pct / 100.0) * (VOLUME_MAX - VOLUME_MIN)
            return volume_fader_to_amp_linear(
                trim,
                patch_gain_linear=8.0,
                cap=cap,
                fader_min=VOLUME_MIN,
                fader_max=VOLUME_MAX,
            )

        def db(linear: float) -> float:
            if linear <= 0.0:
                return -120.0
            return 20.0 * math.log10(linear)

        low_span = db(at_pct(60.0)) - db(at_pct(40.0))
        high_span = db(at_pct(100.0)) - db(at_pct(80.0))
        self.assertAlmostEqual(low_span, high_span, delta=0.05)

    def test_volume_fader_mute_and_db_labels(self) -> None:
        from patch_browser.patch_normalization import (
            volume_fader_display_db,
            volume_fader_to_amp_linear,
        )
        from patch_browser.touch_ui_constants import VOLUME_MAX, VOLUME_MIN

        self.assertEqual(
            volume_fader_display_db(0.0, fader_min=VOLUME_MIN, fader_max=VOLUME_MAX),
            "-∞",
        )
        self.assertEqual(
            volume_fader_display_db(1.0, fader_min=VOLUME_MIN, fader_max=VOLUME_MAX),
            "0",
        )
        self.assertEqual(
            volume_fader_to_amp_linear(
                0.0,
                patch_gain_linear=2.0,
                cap=1.5,
                fader_min=VOLUME_MIN,
                fader_max=VOLUME_MAX,
            ),
            0.0,
        )

    def test_combined_volume_sends_full_norm_gain_when_active(self) -> None:
        """Norm-on applies full calibrated linear gain (#31 Stage 1)."""
        with tempfile.TemporaryDirectory() as tmp:
            user_path = Path(tmp) / "user.json"
            user_path.write_text(
                json.dumps({"Loud": {"gain_db": 18.0, "enabled": True, "lufs_measured": -40.0}})
            )
            store = PatchNormalizationStore(user_path)
            loader = PatchLoader(normalization_store=store)
            loader.osc_client = FakeOscClient()
            loader.osc_enabled = True
            loader.user_volume_trim = 1.0

            loader.refresh_patch_volume("Loud")
            sent = loader.osc_client.messages[-1][1]
            from patch_browser.patch_normalization import NORM_MAX_AMP_VOLUME_LINEAR, db_to_linear

            self.assertGreater(sent, NORM_MAX_AMP_VOLUME_LINEAR)
            self.assertAlmostEqual(sent, db_to_linear(18.0))

    def test_norm_off_uses_higher_volume_cap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            user_path = Path(tmp) / "user.json"
            user_path.write_text(
                json.dumps({"Loud": {"gain_db": 18.0, "enabled": True, "lufs_measured": -40.0}})
            )
            store = PatchNormalizationStore(user_path)
            loader = PatchLoader(normalization_store=store)
            loader.osc_client = FakeOscClient()
            loader.osc_enabled = True
            loader.user_volume_trim = 1.0

            store.set_enabled("Loud", False)
            loader.refresh_patch_volume("Loud")
            sent = loader.osc_client.messages[-1][1]
            self.assertAlmostEqual(sent, 1.0)

    def test_set_calibration_preserves_disabled_toggle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            user_path = Path(tmp) / "user.json"
            user_path.write_text(json.dumps({"Bass": {"gain_db": 1.0, "enabled": False}}))
            store = PatchNormalizationStore(user_path)
            store.set_calibration("Bass", 2.5, -20.0, true_peak_dbtp=-4.0)
            self.assertFalse(store.is_enabled("Bass"))
            self.assertEqual(store.get_raw_gain_db("Bass"), 2.5)

    def test_global_disable_skips_gain_without_wiping_per_patch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            user_path = Path(tmp) / "user.json"
            user_path.write_text(
                json.dumps(
                    {
                        "Lead": {
                            "gain_db": 4.0,
                            "enabled": True,
                            "lufs_measured": -22.0,
                        }
                    }
                )
            )
            store = PatchNormalizationStore(user_path)
            loader = PatchLoader(normalization_store=store)
            loader.osc_client = FakeOscClient()
            loader.osc_enabled = True
            loader.user_volume_trim = 1.0

            loader.refresh_patch_volume("Lead")
            on_volume = loader.osc_client.messages[-1][1]
            # gain_db=4.0 -> ~1.585x linear; norm-on cap now matches norm-off ceiling
            # (see NORM_MAX_AMP_VOLUME_LINEAR), so the calibrated gain actually comes
            # through instead of being clamped back down near unity.
            self.assertGreater(on_volume, 1.0)

            store.set_globally_enabled(False)
            self.assertTrue(store.is_enabled("Lead"))
            self.assertIsNone(store.get_gain_db("Lead"))
            loader.refresh_patch_volume("Lead")
            off_volume = loader.osc_client.messages[-1][1]
            self.assertAlmostEqual(off_volume, 1.0)

            saved_off = json.loads(user_path.read_text())
            self.assertFalse(saved_off["_global"]["enabled"])
            self.assertTrue(saved_off["Lead"]["enabled"])
            self.assertEqual(saved_off["Lead"]["gain_db"], 4.0)

            store.set_globally_enabled(True)
            loader.refresh_patch_volume("Lead")
            restored = loader.osc_client.messages[-1][1]
            self.assertAlmostEqual(on_volume, restored)

    def test_user_gain_override_takes_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            user_path = Path(tmp) / "user.json"
            user_path.write_text(
                json.dumps(
                    {
                        "Lead": {
                            "gain_db": 6.0,
                            "user_gain_db": 3.0,
                            "enabled": True,
                            "lufs_measured": -24.0,
                        }
                    }
                )
            )
            store = PatchNormalizationStore(user_path)
            self.assertEqual(store.get_effective_gain_db("Lead"), 3.0)
            self.assertEqual(store.get_calibrated_gain_db("Lead"), 6.0)
            self.assertTrue(store.has_user_gain_override("Lead"))

            loader = PatchLoader(normalization_store=store)
            loader.osc_client = FakeOscClient()
            loader.osc_enabled = True
            loader.user_volume_trim = 1.0
            loader.refresh_patch_volume("Lead")
            from patch_browser.patch_normalization import db_to_linear, NORM_MAX_AMP_VOLUME_LINEAR

            sent = loader.osc_client.messages[-1][1]
            expected = min(db_to_linear(3.0), NORM_MAX_AMP_VOLUME_LINEAR)
            self.assertAlmostEqual(sent, expected)

    def test_clear_user_gain_reverts_to_calibrated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            user_path = Path(tmp) / "user.json"
            user_path.write_text(
                json.dumps({"Lead": {"gain_db": 6.0, "user_gain_db": 2.0, "enabled": True}})
            )
            store = PatchNormalizationStore(user_path)
            store.clear_user_gain_db("Lead")
            self.assertFalse(store.has_user_gain_override("Lead"))
            self.assertEqual(store.get_effective_gain_db("Lead"), 6.0)

    def test_set_calibration_preserves_user_gain_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            user_path = Path(tmp) / "user.json"
            user_path.write_text(json.dumps({"Lead": {"gain_db": 4.0, "user_gain_db": 8.0}}))
            store = PatchNormalizationStore(user_path)
            store.set_calibration("Lead", 5.0, -20.0, true_peak_dbtp=-4.0)
            self.assertEqual(store.get_calibrated_gain_db("Lead"), 5.0)
            self.assertEqual(store.get_effective_gain_db("Lead"), 8.0)
            store.save()
            saved = json.loads(user_path.read_text())
            self.assertEqual(saved["Lead"]["gain_db"], 5.0)
            self.assertEqual(saved["Lead"]["user_gain_db"], 8.0)


if __name__ == "__main__":
    unittest.main()
