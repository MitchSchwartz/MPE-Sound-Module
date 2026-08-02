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

    def test_norm_toggle_reload_asserts_unity_amp_when_off(self) -> None:
        """Norm off must positively send amp/volume=1.0 — never rely on Surge's stale state."""
        with tempfile.TemporaryDirectory() as tmp:
            user_path = Path(tmp) / "user.json"
            user_path.write_text(
                json.dumps({"Church - Mod": {"gain_db": 4.11, "enabled": True, "lufs_measured": -22.0}})
            )
            store = PatchNormalizationStore(user_path)
            loader = PatchLoader(normalization_store=store)
            loader.osc_client = FakeOscClient()
            loader.osc_enabled = True

            # Simulate a prior norm-on session leaving amp/volume pinned high.
            loader.reload_patch_after_norm_toggle("/patches/Church - Mod.fxp")
            on_volume = loader.osc_client.messages[-1][1]
            self.assertGreater(on_volume, 1.0)

            store.set_enabled("Church - Mod", False)
            loader.reload_patch_after_norm_toggle("/patches/Church - Mod.fxp")
            loads = [m for m in loader.osc_client.messages if m[0] == "/patch/load"]
            self.assertEqual(len(loads), 2)
            off_volume = loader.osc_client.messages[-1][1]
            self.assertAlmostEqual(off_volume, 1.0)
            self.assertNotAlmostEqual(off_volume, on_volume)

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

    def test_norm_off_sends_explicit_unity_at_default_trim(self) -> None:
        """Norm off must explicitly assert unity — not skip OSC and trust prior state."""
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
            self.assertGreater(len(loader.osc_client.messages), 0)
            self.assertAlmostEqual(loader.osc_client.messages[-1][1], 1.0)

            loader.user_volume_trim = 0.8
            loader.refresh_patch_volume("Loud")
            sent = loader.osc_client.messages[-1][1]
            self.assertAlmostEqual(sent, 0.8)

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
            loader.osc_client.messages.clear()
            loader.refresh_patch_volume("Lead")
            self.assertAlmostEqual(loader.osc_client.messages[-1][1], 1.0)

            loader.user_volume_trim = 0.8
            loader.refresh_patch_volume("Lead")
            off_volume = loader.osc_client.messages[-1][1]
            self.assertAlmostEqual(off_volume, 0.8)

            saved_off = json.loads(user_path.read_text())
            self.assertFalse(saved_off["_global"]["enabled"])
            self.assertTrue(saved_off["Lead"]["enabled"])
            self.assertEqual(saved_off["Lead"]["gain_db"], 4.0)

            store.set_globally_enabled(True)
            loader.user_volume_trim = 1.0
            loader.refresh_patch_volume("Lead")
            restored = loader.osc_client.messages[-1][1]
            self.assertAlmostEqual(on_volume, restored)


if __name__ == "__main__":
    unittest.main()
