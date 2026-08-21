"""Patch normalization v2 — Norm trim offset, legacy migration, vol fader law."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from patch_browser.patch_loader import PatchLoader
from patch_browser.patch_normalization import (
    NORM_TRIM_DB_MAX,
    NORM_TRIM_DB_MIN,
    POST_GAIN_VERIFY_PEAK_MAX_DBTP,
    VOL_FADER_LAW_LINEAR,
    PatchNormalizationStore,
    clamp_user_trim_db,
    db_to_linear,
    post_gain_verify_passes,
    volume_fader_law,
    volume_fader_trim_to_db,
)
from patch_browser.touch_ui_constants import VOLUME_MAX, VOLUME_MIN


class FakeOscClient:
    def __init__(self) -> None:
        self.messages: list[tuple[str, float]] = []

    def send_message(self, address: str, value: float) -> None:
        self.messages.append((address, value))


class PostGainVerifyTests(unittest.TestCase):
    def test_passes_at_safe_peak(self) -> None:
        self.assertTrue(post_gain_verify_passes(-3.0))
        self.assertTrue(post_gain_verify_passes(POST_GAIN_VERIFY_PEAK_MAX_DBTP))

    def test_fails_above_tolerance(self) -> None:
        self.assertFalse(post_gain_verify_passes(-2.0))
        self.assertFalse(post_gain_verify_passes(float("nan")))
        self.assertFalse(post_gain_verify_passes(float("inf")))


class UserTrimTests(unittest.TestCase):
    def test_effective_gain_is_calibrated_plus_trim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            user_path = Path(tmp) / "user.json"
            user_path.write_text(
                json.dumps(
                    {
                        "Lead": {
                            "gain_db": 6.0,
                            "user_trim_db": -2.0,
                            "enabled": True,
                        }
                    }
                )
            )
            store = PatchNormalizationStore(user_path)
            self.assertEqual(store.get_effective_gain_db("Lead"), 4.0)
            self.assertEqual(store.get_user_trim_db("Lead"), -2.0)

    def test_slider_default_is_zero_trim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            user_path = Path(tmp) / "user.json"
            user_path.write_text(json.dumps({"Lead": {"gain_db": 6.0, "enabled": True}}))
            store = PatchNormalizationStore(user_path)
            self.assertEqual(store.get_slider_default_gain_db("Lead"), 0.0)

    def test_set_user_trim_drops_legacy_user_gain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            user_path = Path(tmp) / "user.json"
            user_path.write_text(json.dumps({"Lead": {"gain_db": 4.0, "user_gain_db": 8.0}}))
            store = PatchNormalizationStore(user_path)
            store.set_user_trim_db("Lead", -3.0)
            store.save()
            saved = json.loads(user_path.read_text())
            self.assertEqual(saved["Lead"]["user_trim_db"], -3.0)
            self.assertNotIn("user_gain_db", saved["Lead"])

    def test_clear_user_trim_clears_both_legacy_and_trim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            user_path = Path(tmp) / "user.json"
            user_path.write_text(
                json.dumps({"Lead": {"gain_db": 6.0, "user_gain_db": 2.0, "enabled": True}})
            )
            store = PatchNormalizationStore(user_path)
            store.clear_user_trim_db("Lead")
            self.assertFalse(store.has_user_trim_override("Lead"))
            self.assertEqual(store.get_effective_gain_db("Lead"), 6.0)

    def test_legacy_migration_on_load_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            user_path = Path(tmp) / "user.json"
            user_path.write_text(
                json.dumps({"Lead": {"gain_db": 6.0, "user_gain_db": 3.0, "enabled": True}})
            )
            store = PatchNormalizationStore(user_path)
            self.assertEqual(store.get_user_trim_db("Lead"), -3.0)
            saved = json.loads(user_path.read_text())
            self.assertEqual(saved["Lead"]["user_trim_db"], -3.0)
            self.assertNotIn("user_gain_db", saved["Lead"])

    def test_extreme_v1_override_clamped_to_trim_ceiling(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            user_path = Path(tmp) / "user.json"
            user_path.write_text(
                json.dumps({"Lead": {"gain_db": 0.0, "user_gain_db": 24.0, "enabled": True}})
            )
            store = PatchNormalizationStore(user_path)
            self.assertEqual(store.get_user_trim_db("Lead"), NORM_TRIM_DB_MAX)

    def test_trim_clamp(self) -> None:
        self.assertEqual(clamp_user_trim_db(-30.0), NORM_TRIM_DB_MIN)
        self.assertEqual(clamp_user_trim_db(20.0), NORM_TRIM_DB_MAX)


class RuntimeTrimTests(unittest.TestCase):
    def test_trim_reaches_osc_send(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            user_path = Path(tmp) / "user.json"
            user_path.write_text(
                json.dumps(
                    {
                        "Lead": {
                            "gain_db": 6.0,
                            "user_trim_db": -2.0,
                            "enabled": True,
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
            sent = loader.osc_client.messages[-1][1]
            self.assertAlmostEqual(sent, db_to_linear(4.0))


class VolFaderLawTests(unittest.TestCase):
    def test_default_is_console(self) -> None:
        os.environ.pop("MPE_VOL_FADER_LAW", None)
        self.assertEqual(volume_fader_law(), "console")

    def test_linear_law_even_step_at_half(self) -> None:
        os.environ["MPE_VOL_FADER_LAW"] = VOL_FADER_LAW_LINEAR
        try:
            half = VOLUME_MIN + 0.5 * (VOLUME_MAX - VOLUME_MIN)
            db_half = volume_fader_trim_to_db(
                half, fader_min=VOLUME_MIN, fader_max=VOLUME_MAX
            )
            db_top = volume_fader_trim_to_db(
                VOLUME_MAX, fader_min=VOLUME_MIN, fader_max=VOLUME_MAX
            )
            assert db_half is not None and db_top is not None
            self.assertLessEqual(abs((db_top - db_half) - 30.0), 3.0)
        finally:
            os.environ.pop("MPE_VOL_FADER_LAW", None)


if __name__ == "__main__":
    unittest.main()
