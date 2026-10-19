"""The trunk's stage gain: the formula, the store's schema, and what PatchLoader sends.

Off-hardware half of the fx-host gain stage (the C half is fx_selftest's
"stage gain" checks, run by tests/test_fx_host.py). Sequences are pinned
exactly, with Surge's OSC and the `gain` datagrams on one timeline, so an
added, dropped or reordered message fails.
"""

from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from patch_browser import patch_normalization as pn
from patch_browser.patch_loader import STAGE_KEEPALIVE_S, PatchLoader
from patch_browser.touch_ui_constants import VOLUME_MAX, VOLUME_MIN

CAL = pn.CAL_PATH


def gain(db: float) -> tuple[str, str]:
    linear = 0.0 if db == -math.inf else 10.0 ** (db / 20.0)
    return ("gain", f"{linear:.8f}")


class Timeline:
    """Surge's OSC and the stage datagrams, in the order they went out."""

    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    def send_message(self, address: str, value) -> None:
        self.events.append((address, value))

    def __call__(self, payload: bytes) -> bool:
        word, value = payload.decode("ascii").split()
        self.events.append((word, value))
        return True

    def stage(self) -> list[tuple[str, object]]:
        return [e for e in self.events if e[0] == "gain"]


def write_store(tmp: str, data: dict) -> pn.PatchNormalizationStore:
    path = Path(tmp) / "norm.json"
    path.write_text(json.dumps(data))
    return pn.PatchNormalizationStore(path)


def make_loader(store, authored: float | None = 0.9) -> tuple[PatchLoader, Timeline]:
    line = Timeline()
    loader = PatchLoader(normalization_store=store, stage_sender=line,
                         fx_policy=mock.Mock(), fx_store=mock.Mock())
    loader.osc_client = line
    loader.osc_enabled = True
    loader._query_osc_float = mock.Mock(return_value=authored)
    return loader, line


def load(loader: PatchLoader, name: str, **kw) -> bool:
    path = f"/p/{name}.fxp"
    with (
        mock.patch("patch_browser.patch_loader.ensure_reuse_single_patch", return_value=Path(path)),
        mock.patch("patch_browser.patch_loader.time.sleep",
                   side_effect=lambda _s: loader.osc_client.send_message("<settle>", None)),
        mock.patch.object(loader, "_apply_playback_policy",
                          side_effect=lambda _n: loader.osc_client.send_message("<policy>", None)),
        mock.patch.object(loader, "_capture_hold_baseline", return_value=True),
        mock.patch.object(loader, "_send_hold_osc", return_value=True),
        mock.patch.object(loader, "_resend_patch_mod"),
    ):
        return loader.load_patch(path, capture_hold=False, **kw)


class StageFormula(unittest.TestCase):
    def test_terms_add_in_db(self) -> None:
        self.assertEqual(pn.stage_db(norm_db=6.0, ceiling_db=None, headroom_db=-9.0, vol_db=-10.0), -13.0)

    def test_the_ceiling_holds_only_the_norm_part(self) -> None:
        self.assertEqual(pn.stage_db(norm_db=12.0, ceiling_db=5.0, headroom_db=-9.0, vol_db=-3.0), -7.0)
        self.assertEqual(pn.stage_db(norm_db=4.0, ceiling_db=5.0, headroom_db=0.0, vol_db=0.0), 4.0)

    def test_headroom_and_vol_only_attenuate(self) -> None:
        self.assertEqual(pn.stage_db(norm_db=0.0, ceiling_db=None, headroom_db=3.0, vol_db=2.0), 0.0)

    def test_mute_is_minus_infinity(self) -> None:
        self.assertEqual(pn.stage_db(norm_db=40.0, ceiling_db=None, headroom_db=0.0, vol_db=None), -math.inf)

    def test_ceiling_from_the_louder_anchor(self) -> None:
        entry = {"gain_db": 3.0, "cal_path": CAL, "true_peak_unity_dbtp": {"strike": -7.0, "sustain": -4.5}}
        self.assertEqual(pn.norm_ceiling_db(entry), 4.5)
        self.assertIsNone(pn.norm_ceiling_db({"gain_db": 3.0, "cal_path": CAL}))
        self.assertIsNone(pn.norm_ceiling_db({"gain_db": 3.0, "true_peak_unity_dbtp": {"s": -4.5}}))

    def test_at_ceiling(self) -> None:
        self.assertTrue(pn.norm_at_ceiling(6.0, 4.5))
        self.assertFalse(pn.norm_at_ceiling(4.5, 4.5))
        self.assertFalse(pn.norm_at_ceiling(60.0, None))

    def test_the_c_ceiling_and_trim_range_mirrors(self) -> None:
        from patch_browser import fx_host_protocol
        from patch_browser.mixer_controls import NormControl

        header = (Path(__file__).resolve().parents[1] / "native/mpe-fx-host/fx_dsp.h").read_text()
        self.assertIn(f"#define FX_GAIN_MAX_DB {fx_host_protocol.GAIN_MAX_DB}", header)
        self.assertEqual((NormControl.spec.min_value, NormControl.spec.max_value),
                         (pn.NORM_TRIM_DB_MIN, pn.NORM_TRIM_DB_MAX))
        self.assertEqual((pn.NORM_TRIM_DB_MIN, pn.NORM_TRIM_DB_MAX), (-30.0, 30.0))


class Schema(unittest.TestCase):
    def test_only_a_stage_calibration_is_calibrated(self) -> None:
        self.assertTrue(pn.is_calibrated({"gain_db": 2.0, "cal_path": CAL}))
        for entry in ({"gain_db": 2.0}, {"gain_db": 2.0, "cal_path": "amp-v0"},
                      {"cal_path": CAL}, {"gain_db": float("nan"), "cal_path": CAL}, None):
            with self.subTest(entry=entry):
                self.assertFalse(pn.is_calibrated(entry))

    def test_a_stale_schema_row_reads_as_uncalibrated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = write_store(tmp, {"Old": {"gain_db": -6.0, "user_trim_db": 2.0, "enabled": True}})
            self.assertIsNone(store.get_calibrated_gain_db("Old"))
            self.assertIsNone(store.get_effective_gain_db("Old"))
            self.assertIsNone(store.get_gain_db("Old"))
            self.assertEqual(store.list_missing([{"name": "Old"}]), ["Old"])

    def test_a_pre_stage_row_keeps_the_level_it_played_at(self) -> None:
        """Until it is re-measured: amp/volume g·t was 60·log10(min(1, g·t)) re 1.0."""
        cases = [(-10.0, 0.0, 0.9), (-10.0, 3.0, 0.5), (6.0, 0.0, 0.7), (-20.0, -4.0, 1.0)]
        for g, t, v in cases:
            with self.subTest(g=g, t=t, v=v):
                entry = {"gain_db": g, "user_trim_db": t}
                stage = pn.legacy_norm_db(entry, v)
                old_level = 60.0 * math.log10(min(1.0, 10.0 ** ((g + t) / 20.0)))
                self.assertAlmostEqual(60.0 * math.log10(v) + stage, old_level, places=9)

    def test_an_unknown_authored_volume_errs_quiet(self) -> None:
        entry = {"gain_db": -10.0}
        for v in (0.3, 0.7, 1.0):
            self.assertLessEqual(pn.legacy_norm_db(entry, None), pn.legacy_norm_db(entry, v))

    def test_stale_when_the_authored_volume_moved(self) -> None:
        entry = {"gain_db": 2.0, "cal_path": CAL, "authored_volume": 0.8}
        self.assertFalse(pn.is_stale(entry, 0.8 + pn.AUTHORED_VOLUME_TOLERANCE / 2))
        self.assertTrue(pn.is_stale(entry, 0.85))
        self.assertFalse(pn.is_stale(entry, None))

    def test_stage_norm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = write_store(tmp, {
                "Cal": {"gain_db": 4.0, "user_trim_db": 3.0, "cal_path": CAL, "authored_volume": 0.8,
                        "true_peak_unity_dbtp": {"strike": -8.0, "sustain": -9.0}},
                "Old": {"gain_db": -10.0},
                "Off": {"gain_db": 4.0, "cal_path": CAL, "enabled": False},
            })
            self.assertEqual(store.stage_norm("Cal", authored_volume=0.8), (7.0, 8.0))
            self.assertEqual(store.stage_norm("Cal", authored_volume=0.5), (0.0, None))
            self.assertEqual(store.stage_norm("Off", authored_volume=0.8), (0.0, None))
            self.assertEqual(store.stage_norm("Nobody", authored_volume=0.8), (0.0, None))
            norm, ceiling = store.stage_norm("Old", authored_volume=1.0)
            self.assertAlmostEqual(norm, -30.0)
            self.assertIsNone(ceiling)
            store.set_globally_enabled(False)
            self.assertEqual(store.stage_norm("Cal", authored_volume=0.8), (0.0, None))


class TrimMigration(unittest.TestCase):
    """D8: a re-measured row's trim reproduces the level heard before."""

    def _check(self, g_old, t_old, g_new, v) -> tuple[float, bool]:
        trim, clamped = pn.migrate_trim_to_stage(
            gain_db_old=g_old, trim_db_old=t_old, gain_db_new=g_new, authored_volume=v
        )
        if not clamped:
            old_level = 60.0 * math.log10(min(1.0, 10.0 ** ((g_old + t_old) / 20.0)))
            self.assertAlmostEqual(60.0 * math.log10(v) + g_new + trim, old_level, places=9)
        return trim, clamped

    def test_round_trips(self) -> None:
        for args in ((-6.0, 2.0, -15.0, 0.9), (-3.0, -4.0, -18.0, 0.75), (4.0, 0.0, 3.0, 1.0),
                     (0.0, 6.0, 2.0, 0.6)):
            with self.subTest(args=args):
                self.assertFalse(self._check(*args)[1])

    def test_a_row_past_the_range_is_clamped_and_reported(self) -> None:
        trim, clamped = self._check(-20.0, -10.0, 10.0, 1.0)  # wants -100 dB
        self.assertTrue(clamped)
        self.assertEqual(trim, pn.NORM_TRIM_DB_MIN)

    def test_set_calibration_migrates_and_keeps_the_old_trim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = write_store(tmp, {"Lead": {"gain_db": -6.0, "user_trim_db": 2.0, "enabled": True}})
            record = store.set_calibration("Lead", -15.0, -18.0, true_peak_dbtp=-3.0, authored_volume=0.9)
            entry = store.get_entry("Lead")
            expected, _ = pn.migrate_trim_to_stage(gain_db_old=-6.0, trim_db_old=2.0,
                                                   gain_db_new=-15.0, authored_volume=0.9)
            self.assertEqual(entry["user_trim_db"], round(expected, 3))
            self.assertEqual(entry["user_trim_db_legacy"], 2.0)
            self.assertEqual(entry["cal_path"], CAL)
            self.assertEqual(record, {"key": "Lead", "old": 2.0, "new": round(expected, 3), "clamped": False})
            # Re-measuring again keeps the migrated trim: no second conversion.
            self.assertIsNone(store.set_calibration("Lead", -14.0, -18.0, authored_volume=0.9))
            self.assertEqual(store.get_entry("Lead")["user_trim_db"], round(expected, 3))
            self.assertEqual(store.get_entry("Lead")["user_trim_db_legacy"], 2.0)

    def test_a_row_without_a_trim_needs_no_migration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = write_store(tmp, {"Lead": {"gain_db": -6.0}})
            self.assertIsNone(store.set_calibration("Lead", -15.0, -18.0, authored_volume=0.9))
            self.assertNotIn("user_trim_db", store.get_entry("Lead"))


class VolumeLaws(unittest.TestCase):
    def test_pad_through_surges_volume_law(self) -> None:
        for v in (1.0, 0.8, 0.31):
            padded = pn.padded_volume(v, 12.0)
            self.assertAlmostEqual(60.0 * math.log10(padded / v), -12.0, places=9)

    def test_fader_inverse(self) -> None:
        for law in (pn.VOL_FADER_LAW_CONSOLE, pn.VOL_FADER_LAW_LINEAR):
            for t in (0.05, 0.2, 0.5, 0.77, 1.0):
                with self.subTest(law=law, t=t):
                    db = pn.volume_fader_trim_to_db(t, fader_min=VOLUME_MIN, fader_max=VOLUME_MAX, law=law)
                    back = pn.volume_fader_db_to_trim(db, fader_min=VOLUME_MIN, fader_max=VOLUME_MAX, law=law)
                    self.assertAlmostEqual(back, t, places=9)

    def test_a_saved_vol_fader_keeps_its_sound(self) -> None:
        """The old fader's label reached Surge as three times its dB."""
        def label(t):
            return pn.volume_fader_trim_to_db(t, fader_min=VOLUME_MIN, fader_max=VOLUME_MAX)

        for t in (0.6, 0.8, 0.9, 0.95):
            with self.subTest(t=t):
                moved = pn.legacy_vol_fader_to_stage(t, fader_min=VOLUME_MIN, fader_max=VOLUME_MAX)
                self.assertAlmostEqual(label(moved), 3.0 * label(t), places=6)
        self.assertEqual(pn.legacy_vol_fader_to_stage(VOLUME_MAX, fader_min=VOLUME_MIN, fader_max=VOLUME_MAX), VOLUME_MAX)
        self.assertEqual(pn.legacy_vol_fader_to_stage(VOLUME_MIN, fader_min=VOLUME_MIN, fader_max=VOLUME_MAX), VOLUME_MIN)
        # -30 dB on the old fader cut 90: past the floor, so mute.
        low = pn.volume_fader_db_to_trim(-30.0, fader_min=VOLUME_MIN, fader_max=VOLUME_MAX)
        self.assertEqual(pn.legacy_vol_fader_to_stage(low, fader_min=VOLUME_MIN, fader_max=VOLUME_MAX), VOLUME_MIN)

    def test_ui_prefs_convert_once(self) -> None:
        from patch_browser import ui_prefs

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "volume.json"
            path.write_text(json.dumps({"volume": 0.8}))
            with mock.patch.object(ui_prefs, "VOLUME_STATE_FILE", path):
                first = ui_prefs.load_volume_level()
                self.assertAlmostEqual(
                    first, pn.legacy_vol_fader_to_stage(0.8, fader_min=VOLUME_MIN, fader_max=VOLUME_MAX))
                self.assertEqual(json.loads(path.read_text()),
                                 {"volume": first, "law": ui_prefs.VOLUME_LAW})
                self.assertEqual(ui_prefs.load_volume_level(), first)


class LoaderSequences(unittest.TestCase):
    """Exact timelines: Surge's OSC and the stage, in one list."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = write_store(self._tmp.name, {
            "Hot": {"gain_db": -10.0, "cal_path": CAL, "enabled": True},
            "Quiet": {"gain_db": 6.0, "cal_path": CAL, "enabled": True},
            "Edited": {"gain_db": -10.0, "cal_path": CAL, "authored_volume": 0.5, "enabled": True},
        })

    def test_load_with_a_cut_ducks_before_the_load(self) -> None:
        loader, line = make_loader(self.store)
        self.assertTrue(load(loader, "Hot"))
        self.assertEqual(line.events, [
            gain(-10.0),
            ("/patch/load", ["/p/Hot"]),
            ("<settle>", None),
            ("<policy>", None),
            gain(-10.0),
        ])

    def test_load_with_a_raise_waits_for_the_settle(self) -> None:
        loader, line = make_loader(self.store)
        load(loader, "Hot")
        line.events.clear()
        load(loader, "Quiet")
        self.assertEqual(line.events, [
            ("/patch/load", ["/p/Quiet"]),
            ("<settle>", None),
            ("<policy>", None),
            gain(6.0),
        ])

    def test_the_first_load_counts_from_unity(self) -> None:
        loader, line = make_loader(self.store)
        load(loader, "Quiet")
        self.assertEqual(line.events[0], ("/patch/load", ["/p/Quiet"]))

    def test_an_edited_patch_plays_as_authored(self) -> None:
        loader, line = make_loader(self.store, authored=0.9)
        load(loader, "Edited")
        self.assertEqual(line.stage(), [gain(-10.0), gain(0.0)])

    def test_normalization_off_for_the_load(self) -> None:
        loader, line = make_loader(self.store)
        load(loader, "Hot", apply_normalization=False)
        self.assertEqual(line.stage(), [gain(0.0)])

    def test_a_fader_drag_is_one_datagram_per_step(self) -> None:
        loader, line = make_loader(self.store)
        load(loader, "Quiet")
        line.events.clear()
        steps = (0.9, 0.7, 0.5, 0.0, 0.5)
        for position in steps:
            loader.set_volume(position)
        expected = []
        for position in steps:
            vol = pn.volume_fader_trim_to_db(position, fader_min=VOLUME_MIN, fader_max=VOLUME_MAX)
            expected.append(gain(-math.inf if vol is None else 6.0 + vol))
        self.assertEqual(line.events, expected)

    def test_device_and_trim_switch(self) -> None:
        from patch_browser.device_volume import DEVICE_MODE_SURGE_TRIM_DB, apply_stage_volume

        loader, line = make_loader(self.store)
        load(loader, "Quiet")
        line.events.clear()
        apply_stage_volume(loader, device_active=True, level=0.3)
        apply_stage_volume(loader, device_active=False, level=VOLUME_MAX)
        apply_stage_volume(loader, device_active=True, level=VOLUME_MAX)
        self.assertEqual(line.events, [
            gain(6.0 + DEVICE_MODE_SURGE_TRIM_DB), gain(6.0), gain(6.0 + DEVICE_MODE_SURGE_TRIM_DB),
        ])

    def test_keepalive_resends_once_a_second_after_the_first_send(self) -> None:
        loader, line = make_loader(self.store)
        loader.keep_stage_alive(now=100.0)
        self.assertEqual(line.events, [], "nothing to keep alive before a first send")
        load(loader, "Hot")
        line.events.clear()
        for now in (101.0, 101.5, 101.0 + STAGE_KEEPALIVE_S, 102.9, 103.0 + STAGE_KEEPALIVE_S):
            loader.keep_stage_alive(now=now)
        self.assertEqual(line.events, [gain(-10.0)] * 3)

    def test_at_ceiling_shows(self) -> None:
        store = write_store(self._tmp.name, {"Lead": {
            "gain_db": 4.0, "user_trim_db": 6.0, "cal_path": CAL,
            "true_peak_unity_dbtp": {"strike": -7.0, "sustain": -8.0}}})
        loader, line = make_loader(store)
        load(loader, "Lead")
        self.assertTrue(loader.norm_at_ceiling)
        self.assertEqual(line.stage()[-1], gain(7.0))


class NormFaderLabel(unittest.TestCase):
    def test_max_while_the_ceiling_holds(self) -> None:
        from patch_browser.mixer_controls import NormControl

        patch = {"name": "Lead", "path": "/p/Lead.fxp"}
        browser = mock.Mock(detail_patch=patch, loaded_patch_info=patch)
        browser.loader.normalization.refs_match.return_value = True
        browser.loader.norm_at_ceiling = True
        self.assertEqual(NormControl().format_for(browser, 6.0), "max")
        browser.loader.norm_at_ceiling = False
        self.assertEqual(NormControl().format_for(browser, 6.0), "+6.0")
        browser.loader.norm_at_ceiling = True
        browser.loader.normalization.refs_match.return_value = False
        self.assertEqual(NormControl().format_for(browser, 6.0), "+6.0")



class SenderSafety(unittest.TestCase):
    def test_no_sender_unless_one_is_passed(self) -> None:
        """Tests and calibration build loaders too; only production passes a sender."""
        with mock.patch("patch_browser.patch_loader.UdpStageSender") as udp:
            loader = PatchLoader()
            self.assertIsNone(loader._stage_sender)
            loader.set_volume(VOLUME_MAX)
            loader.keep_stage_alive(now=1e9)
            self.assertIsNone(loader._stage_sent_db, "nothing was sent")
        udp.assert_not_called()

    def test_the_send_is_capped_at_the_client_ceiling(self) -> None:
        from patch_browser import fx_host_protocol

        with tempfile.TemporaryDirectory() as tmp:
            loader, line = make_loader(write_store(tmp, {}))
            loader._norm_db = fx_host_protocol.GAIN_MAX_DB + 12.0
            loader.set_volume(VOLUME_MAX)
        self.assertEqual(line.stage(), [gain(fx_host_protocol.GAIN_MAX_DB)])


if __name__ == "__main__":
    unittest.main()
