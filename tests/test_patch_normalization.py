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
            self.assertGreater(on_volume, 0.8)

            store.set_enabled("Lead", False)
            loader.refresh_patch_volume("Lead")
            off_volume = loader.osc_client.messages[-1][1]
            self.assertAlmostEqual(off_volume, 0.8)

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

    def test_combined_volume_clamps_above_max_amp_linear(self) -> None:
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
            from patch_browser.patch_normalization import NORM_MAX_AMP_VOLUME_LINEAR

            self.assertLessEqual(sent, NORM_MAX_AMP_VOLUME_LINEAR)
            self.assertAlmostEqual(sent, NORM_MAX_AMP_VOLUME_LINEAR)

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
            self.assertLess(on_volume, 1.0)

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


if __name__ == "__main__":
    unittest.main()
