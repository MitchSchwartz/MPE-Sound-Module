"""Tests for per-patch normalization store and the loader's stage gain."""

from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from patch_browser.patch_loader import PatchLoader
from patch_browser.patch_normalization import CAL_PATH, PatchNormalizationStore
from patch_browser.touch_ui_constants import VOLUME_MAX, VOLUME_MIN


class RecordingStage:
    """Stands in for the UDP sender: keeps every `gain` datagram, as dB."""

    def __init__(self) -> None:
        self.payloads: list[bytes] = []

    def __call__(self, payload: bytes) -> bool:
        self.payloads.append(payload)
        return True

    @property
    def db(self) -> list[float]:
        out = []
        for payload in self.payloads:
            word, value = payload.decode("ascii").split()
            assert word == "gain", payload
            linear = float(value)
            out.append(-math.inf if linear == 0.0 else 20.0 * math.log10(linear))
        return out


def vol_db(position: float) -> float | None:
    from patch_browser.patch_normalization import volume_fader_trim_to_db

    return volume_fader_trim_to_db(position, fader_min=VOLUME_MIN, fader_max=VOLUME_MAX)


def make_loader(store) -> tuple[PatchLoader, RecordingStage]:
    stage = RecordingStage()
    loader = PatchLoader(normalization_store=store, stage_sender=stage)
    loader.osc_client = mock.Mock()
    loader.osc_enabled = True
    # Surge has confirmed the loaded patch: a boost goes out (keep_stage_alive).
    loader._surge_confirmed = True
    return loader, stage


def calibrated(gain_db: float, **extra) -> dict:
    return {"gain_db": gain_db, "cal_path": CAL_PATH, "enabled": True, **extra}


class PatchNormalizationStoreTests(unittest.TestCase):
    def _store(self, tmp: str, data: dict) -> tuple[PatchNormalizationStore, Path]:
        user_path = Path(tmp) / "user.json"
        user_path.write_text(json.dumps(data))
        return PatchNormalizationStore(user_path), user_path

    def test_toggle_off_on_preserves_gain_and_moves_the_stage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store, user_path = self._store(tmp, {"Lead": calibrated(6.0, lufs_measured=-24.0)})
            loader, stage = make_loader(store)
            loader.user_volume_trim = 0.8

            loader.refresh_patch_volume("Lead")
            self.assertAlmostEqual(stage.db[-1], 6.0 + vol_db(0.8), places=5)

            store.set_enabled("Lead", False)
            loader.refresh_patch_volume("Lead")
            self.assertAlmostEqual(stage.db[-1], vol_db(0.8), places=5)

            store.set_enabled("Lead", True)
            loader.refresh_patch_volume("Lead")
            self.assertAlmostEqual(stage.db[-1], 6.0 + vol_db(0.8), places=5)

            saved = json.loads(user_path.read_text())
            self.assertTrue(saved["Lead"]["enabled"])
            self.assertEqual(saved["Lead"]["gain_db"], 6.0)

    def test_load_merges_user_enabled_over_repo_calibration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            starter = Path(tmp) / "starter.json"
            user_path = Path(tmp) / "user.json"
            starter.write_text(json.dumps({"Pad": calibrated(-3.0, lufs_measured=-15.0)}))
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
            starter.write_text(json.dumps({"Acid": calibrated(12.0, lufs_measured=-40.0)}))
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

    def test_refresh_sends_the_stage_once(self) -> None:
        """One datagram: the stage has no first-message quirk to repeat for."""
        with tempfile.TemporaryDirectory() as tmp:
            store, _ = self._store(tmp, {"Lead": calibrated(6.0)})
            loader, stage = make_loader(store)
            loader.refresh_patch_volume("Lead")
            self.assertEqual(stage.payloads, [b"gain 1.99526231"])
            loader.osc_client.send_message.assert_not_called()

    def test_vol_fader_moves_a_boosted_patch_down_to_mute(self) -> None:
        """The fader acts at any norm gain: nothing caps the boost under it."""
        with tempfile.TemporaryDirectory() as tmp:
            store, _ = self._store(tmp, {"Loud": calibrated(18.0, lufs_measured=-40.0)})
            loader, stage = make_loader(store)
            loader.refresh_patch_volume("Loud")

            loader.set_volume(1.0)
            loader.set_volume(0.5)
            loader.set_volume(0.0)
            at_unity, at_half, at_mute = stage.db[-3:]
            self.assertAlmostEqual(at_unity, 18.0, places=5)
            self.assertAlmostEqual(at_half, 18.0 + vol_db(0.5), places=5)
            self.assertEqual(at_mute, -math.inf)
            self.assertEqual(stage.payloads[-1], b"gain 0.00000000")

    def test_volume_fader_uses_console_taper_not_linear_db(self) -> None:
        """Top of travel must be gentler than the bottom (IEC 60268-17 fader law).

        Replaces test_volume_fader_db_linear_even_steps, which asserted equal dB per
        unit of travel. Linear-in-dB over 60 dB crams everything usable (0..-12 dB)
        into the top 20% and wastes the bottom half below -30 dB — the steep dropoff
        reported on the appliance.
        """
        from patch_browser.patch_normalization import VOLUME_FADER_FLOOR_DB

        def db_at(pct: float) -> float:
            value = vol_db(VOLUME_MIN + (pct / 100.0) * (VOLUME_MAX - VOLUME_MIN))
            return VOLUME_FADER_FLOOR_DB if value is None else value

        top_span = db_at(100.0) - db_at(80.0)
        bottom_span = db_at(40.0) - db_at(20.0)
        self.assertLess(
            top_span,
            bottom_span,
            "fader is still linear in dB — the top 20% must cover fewer dB than the bottom",
        )

        # Endpoints unchanged: unity at the top, floor at the bottom.
        self.assertAlmostEqual(db_at(100.0), 0.0, delta=0.01)
        self.assertAlmostEqual(db_at(0.0), VOLUME_FADER_FLOOR_DB, delta=0.01)

        # Monotonic, and half travel still leaves a usable level rather than a cliff.
        values = [db_at(p) for p in range(0, 101, 5)]
        self.assertEqual(values, sorted(values))
        self.assertGreater(db_at(50.0), -22.0)

    def test_volume_fader_mute_and_db_labels(self) -> None:
        from patch_browser.patch_normalization import stage_db, volume_fader_display_db

        self.assertEqual(
            volume_fader_display_db(0.0, fader_min=VOLUME_MIN, fader_max=VOLUME_MAX),
            "-∞",
        )
        self.assertEqual(
            volume_fader_display_db(1.0, fader_min=VOLUME_MIN, fader_max=VOLUME_MAX),
            "0",
        )
        self.assertEqual(
            stage_db(norm_db=6.0, ceiling_db=None, headroom_db=0.0, vol_db=vol_db(0.0)),
            -math.inf,
        )

    def test_norm_on_sends_the_full_calibrated_gain(self) -> None:
        """No amp/volume cap stands between a quiet patch and its gain (#31 Stage 1)."""
        with tempfile.TemporaryDirectory() as tmp:
            store, _ = self._store(tmp, {"Loud": calibrated(18.0, lufs_measured=-40.0)})
            loader, stage = make_loader(store)
            loader.refresh_patch_volume("Loud")
            self.assertAlmostEqual(stage.db[-1], 18.0, places=5)

    def test_norm_off_plays_the_patch_as_authored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store, _ = self._store(tmp, {"Loud": calibrated(18.0, lufs_measured=-40.0)})
            loader, stage = make_loader(store)
            store.set_enabled("Loud", False)
            loader.refresh_patch_volume("Loud")
            self.assertEqual(stage.payloads[-1], b"gain 1.00000000")

    def test_set_calibration_preserves_disabled_toggle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store, _ = self._store(tmp, {"Bass": {"gain_db": 1.0, "enabled": False}})
            store.set_calibration("Bass", 2.5, -20.0, true_peak_dbtp=-4.0)
            self.assertFalse(store.is_enabled("Bass"))
            self.assertEqual(store.get_raw_gain_db("Bass"), 2.5)

    def test_global_disable_skips_gain_without_wiping_per_patch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store, user_path = self._store(tmp, {"Lead": calibrated(4.0, lufs_measured=-22.0)})
            loader, stage = make_loader(store)

            loader.refresh_patch_volume("Lead")
            self.assertAlmostEqual(stage.db[-1], 4.0, places=5)

            store.set_globally_enabled(False)
            self.assertTrue(store.is_enabled("Lead"))
            self.assertIsNone(store.get_gain_db("Lead"))
            loader.refresh_patch_volume("Lead")
            self.assertEqual(stage.payloads[-1], b"gain 1.00000000")

            saved_off = json.loads(user_path.read_text())
            self.assertFalse(saved_off["_global"]["enabled"])
            self.assertTrue(saved_off["Lead"]["enabled"])
            self.assertEqual(saved_off["Lead"]["gain_db"], 4.0)

            store.set_globally_enabled(True)
            loader.refresh_patch_volume("Lead")
            self.assertAlmostEqual(stage.db[-1], 4.0, places=5)

    def test_user_gain_override_takes_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store, _ = self._store(
                tmp, {"Lead": calibrated(6.0, user_gain_db=3.0, lufs_measured=-24.0)}
            )
            self.assertEqual(store.get_effective_gain_db("Lead"), 3.0)
            self.assertEqual(store.get_calibrated_gain_db("Lead"), 6.0)
            self.assertEqual(store.get_user_trim_db("Lead"), -3.0)
            self.assertTrue(store.has_user_trim_override("Lead"))

            loader, stage = make_loader(store)
            loader.refresh_patch_volume("Lead")
            self.assertAlmostEqual(stage.db[-1], 3.0, places=5)  # 6 + (-3) trim

    def test_clear_user_gain_reverts_to_calibrated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store, _ = self._store(tmp, {"Lead": calibrated(6.0, user_gain_db=2.0)})
            store.clear_user_trim_db("Lead")
            self.assertFalse(store.has_user_trim_override("Lead"))
            self.assertEqual(store.get_effective_gain_db("Lead"), 6.0)

    def test_recalibration_preserves_a_stage_trim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store, user_path = self._store(tmp, {"Lead": calibrated(4.0, user_gain_db=8.0)})
            self.assertIsNone(store.set_calibration("Lead", 5.0, -20.0, true_peak_dbtp=-4.0))
            self.assertEqual(store.get_calibrated_gain_db("Lead"), 5.0)
            self.assertEqual(store.get_effective_gain_db("Lead"), 9.0)  # trim +4 preserved on re-cal
            store.save()
            saved = json.loads(user_path.read_text())
            self.assertEqual(saved["Lead"]["gain_db"], 5.0)
            self.assertEqual(saved["Lead"]["user_trim_db"], 4.0)
            self.assertNotIn("user_gain_db", saved["Lead"])
            self.assertNotIn("user_trim_db_legacy", saved["Lead"])


if __name__ == "__main__":
    unittest.main()
