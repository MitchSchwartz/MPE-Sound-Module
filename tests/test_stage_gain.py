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
    """Surge's OSC and the stage datagrams, in the order they went out.

    ``/q/patch`` is Surge's load confirmation: it answers with the last
    ``/patch/load`` path while ``surge_confirms`` is True, and not at all when False.
    """

    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []
        self.surge_confirms = True
        self.last_load: str | None = None

    def query_loaded_patch_path(self, *_a, **_k) -> str | None:
        self.events.append(("/q/patch", None))
        return self.last_load if self.surge_confirms else None

    def send_message(self, address: str, value) -> None:
        self.events.append((address, value))
        if address == "/patch/load":
            self.last_load = value[0]

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
    loader.query_loaded_patch_path = line.query_loaded_patch_path
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
        # One /q/patch per load: a browse asks once and goes on.
        mock.patch("patch_browser.patch_loader.BROWSE_CONFIRM_TIMEOUT_S", 0.0),
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

    def test_a_cut_past_the_fader_is_kept_exactly(self) -> None:
        """Wants -100 dB: clamping to -30 would play it 70 dB louder than the owner left it."""
        trim, clamped = self._check(-20.0, -10.0, 10.0, 1.0)
        self.assertFalse(clamped)
        self.assertAlmostEqual(trim, -100.0, places=9)
        trim, clamped = self._check(-20.0, -4.0, 3.0, 0.9)  # wants -52.3 dB
        self.assertFalse(clamped)
        self.assertLess(trim, pn.NORM_TRIM_DB_MIN)

    def test_only_a_raise_past_the_fader_is_clamped_and_reported(self) -> None:
        """Wants +45.7 dB (a quiet authored volume): held to +30, which plays quieter."""
        trim, clamped = self._check(0.0, 0.0, -30.0, 0.5)
        self.assertTrue(clamped)
        self.assertEqual(trim, pn.NORM_TRIM_DB_MAX)
        wanted = 0.0 - 60.0 * math.log10(0.5) + 30.0
        self.assertLess(trim, wanted)

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
    def test_pad_through_surges_master_volume(self) -> None:
        """-48..0 dB, linear in the value (Surge ct_decibel_attenuation_clipper)."""
        for v in (1.0, 0.9063, 0.5):
            padded = pn.padded_master_volume(v, 12.0)
            self.assertAlmostEqual(48.0 * (padded - v), -12.0, places=9)
        self.assertIsNone(pn.padded_master_volume(0.2, 12.0))
        self.assertEqual(pn.padded_master_volume(0.25, 12.0), 0.0)

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
            ("/q/patch", None),
            ("<policy>", None),
            gain(-10.0),
        ])

    def test_load_with_a_raise_waits_for_surges_confirmation(self) -> None:
        loader, line = make_loader(self.store)
        load(loader, "Hot")
        line.events.clear()
        load(loader, "Quiet")
        self.assertEqual(line.events, [
            ("/patch/load", ["/p/Quiet"]),
            ("/q/patch", None),
            ("<policy>", None),
            gain(6.0),
        ])
        self.assertEqual(loader._query_osc_float.call_args_list[-2:],
                         [mock.call("/param/a/amp/volume"), mock.call("/param/b/amp/volume")])

    def test_an_unconfirmed_raise_is_held_until_surge_reports_the_patch(self) -> None:
        loader, line = make_loader(self.store)
        load(loader, "Hot")
        line.surge_confirms = False
        line.events.clear()
        loader._query_osc_float.reset_mock()
        self.assertTrue(load(loader, "Quiet"), "a browse never fails on a slow answer")
        self.assertEqual(line.events, [
            ("/patch/load", ["/p/Quiet"]),
            ("/q/patch", None),
            ("<policy>", None),
            gain(-10.0),
        ])
        # The authored volume Surge reports now could be the old patch's.
        loader._query_osc_float.assert_not_called()
        line.events.clear()
        loader.keep_stage_alive(now=1000.0)
        self.assertEqual(line.events, [("/q/patch", None), gain(-10.0)], "still unconfirmed: held")
        line.surge_confirms = True
        line.events.clear()
        loader.keep_stage_alive(now=1000.0 + STAGE_KEEPALIVE_S)
        self.assertEqual(line.events, [("/q/patch", None), gain(6.0)])
        self.assertEqual(loader._query_osc_float.call_args_list,
                         [mock.call("/param/a/amp/volume"), mock.call("/param/b/amp/volume")])
        line.events.clear()
        loader.keep_stage_alive(now=1000.0 + 2 * STAGE_KEEPALIVE_S)
        self.assertEqual(line.events, [("/q/patch", None), gain(6.0)],
                         "a boost is re-sent only for a patch Surge confirms on this tick")
        self.assertEqual(loader._query_osc_float.call_count, 2, "the authored volume is read once")

    def test_a_fader_move_while_held_stays_on_the_quieter_side(self) -> None:
        loader, line = make_loader(self.store)
        load(loader, "Hot")
        line.surge_confirms = False
        load(loader, "Quiet")
        line.events.clear()
        loader.set_volume(0.5)
        vol = pn.volume_fader_trim_to_db(0.5, fader_min=VOLUME_MIN, fader_max=VOLUME_MAX)
        self.assertEqual(line.events, [gain(-10.0 + vol)])

    def test_an_unconfirmed_cut_still_lands_before_the_load(self) -> None:
        loader, line = make_loader(self.store)
        load(loader, "Quiet")
        line.surge_confirms = False
        line.events.clear()
        load(loader, "Hot")
        self.assertEqual(line.events, [
            gain(-10.0),
            ("/patch/load", ["/p/Hot"]),
            ("/q/patch", None),
            ("<policy>", None),
            gain(-10.0),
        ])

    def test_a_confirmed_load_clears_a_held_raise(self) -> None:
        loader, line = make_loader(self.store)
        load(loader, "Hot")
        line.surge_confirms = False
        load(loader, "Quiet")
        line.surge_confirms = True
        line.events.clear()
        load(loader, "Quiet")
        self.assertEqual(line.stage(), [gain(6.0)], "the confirmed load raises at once")
        line.events.clear()
        loader.keep_stage_alive(now=5000.0)
        self.assertEqual(line.events, [("/q/patch", None), gain(6.0)])

    def test_a_calibration_load_surge_does_not_confirm_sends_no_raise(self) -> None:
        loader, line = make_loader(self.store)
        line.surge_confirms = False
        with mock.patch("patch_browser.patch_loader.time.monotonic", side_effect=[0.0, 1.0]):
            ok = load(loader, "Quiet", confirm_timeout_s=0.5)
        self.assertFalse(ok)
        self.assertEqual(line.events, [("/patch/load", ["/p/Quiet"]), ("/q/patch", None)])

    def test_the_first_load_counts_from_unity(self) -> None:
        loader, line = make_loader(self.store)
        load(loader, "Quiet")
        self.assertEqual(line.events[0], ("/patch/load", ["/p/Quiet"]))

    def test_an_edited_patch_keeps_its_cut(self) -> None:
        """Stale (authored 0.5 then, 0.9 now): min(norm, 0), never the patch as authored."""
        loader, line = make_loader(self.store, authored=0.9)
        load(loader, "Edited")
        self.assertEqual(line.stage(), [gain(-10.0), gain(-10.0)])

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


    def test_min_below_the_fader(self) -> None:
        """A migrated cut past -30 is kept, and the fader says so rather than "-30.0"."""
        from patch_browser.mixer_controls import NormControl

        with tempfile.TemporaryDirectory() as tmp:
            store = write_store(tmp, {"Lead": {"gain_db": 2.0, "user_trim_db": -52.3, "cal_path": CAL}})
            patch = {"name": "Lead", "path": "/p/Lead.fxp"}
            browser = mock.Mock(detail_patch=patch, loaded_patch_info=patch)
            browser.loader.normalization = store
            browser.loader.norm_at_ceiling = False
            self.assertEqual(NormControl().format_for(browser, NormControl.spec.min_value), "min")
            self.assertEqual(store.stage_norm("Lead", authored_volume=None)[0], 2.0 - 52.3)
            store.set_user_trim_db("Lead", -30.0)
            self.assertEqual(NormControl().format_for(browser, -30.0), "-30.0")


class NormFaderLaw(unittest.TestCase):
    """A Norm fader move of x dB moves the stage by x dB, on any row. A trim
    written before the stage was heard through the cube law: it is shown in
    stage dB and only migrated, never read as plain dB."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _browser(self, store, name: str, authored: float | None):
        from patch_browser.mixer_controls import NormControl

        patch = {"name": name, "path": f"/p/{name}.fxp"}
        browser = mock.Mock(detail_patch=patch, loaded_patch_info=patch)
        browser.loader.normalization = store
        browser.loader.osc_enabled = False
        browser.loader.norm_at_ceiling = False
        browser.loader._authored_volume = authored
        browser.loader._authored_volume_b = None
        return NormControl(), browser

    def _nudge(self, data: dict, name: str, authored: float | None, played_at: float,
               step: float = 1.0) -> tuple[float, float]:
        store = write_store(self._tmp.name, data)
        control, browser = self._browser(store, name, authored)
        before = store.stage_norm(name, authored_volume=played_at)[0]
        control.write(browser, control.read(browser) + step, persist=False)
        return before, store.stage_norm(name, authored_volume=played_at)[0]

    def test_one_db_on_an_uncalibrated_patch_is_one_db(self) -> None:
        """Authored 0.547, no row: a +1 dB nudge was +16.7 dB on the stage."""
        before, after = self._nudge({}, "U", 0.547, 0.547)
        self.assertEqual(before, 0.0)
        self.assertAlmostEqual(after - before, 1.0, places=6)
        before, after = self._nudge({}, "D", 0.547, 0.547, step=-1.0)
        self.assertAlmostEqual(after - before, -1.0, places=6)

    def test_an_old_law_trim_moves_from_where_it_plays(self) -> None:
        for row in ({"user_trim_db": -6.0}, {"gain_db": -4.0, "user_trim_db": -3.0},
                    {"gain_db": 2.0, "user_trim_db": 1.5}):
            with self.subTest(row=row):
                before, after = self._nudge({"O": dict(row)}, "O", 0.547, 0.547)
                self.assertAlmostEqual(after - before, 1.0, delta=0.001)  # stored to 3 places

    def test_an_unknown_authored_volume_errs_quiet(self) -> None:
        """Shown against an unknown authored volume (detail patch not loaded),
        the move lands no louder than the fader said."""
        for row in ({"user_trim_db": -6.0}, {"gain_db": -4.0, "user_trim_db": -3.0}, {}):
            with self.subTest(row=row):
                before, after = self._nudge({"O": dict(row)}, "O", None, 0.547)
                self.assertLessEqual(after - before, 1.001)

    def test_another_patchs_authored_volume_is_not_used(self) -> None:
        """The detail patch is not the loaded one: the loader's authored volume
        (here a quiet 0.3) belongs to the other patch and must not place the fader."""
        store = write_store(self._tmp.name, {"O": {"user_trim_db": -6.0}})
        control, browser = self._browser(store, "O", 0.3)
        browser.loaded_patch_info = {"name": "Other", "path": "/p/Other.fxp"}
        before = store.stage_norm("O", authored_volume=0.9)[0]
        control.write(browser, control.read(browser) + 1.0, persist=False)
        self.assertLessEqual(store.stage_norm("O", authored_volume=0.9)[0] - before, 1.001)

    def test_a_stage_law_trim_on_a_bridged_row_is_capped(self) -> None:
        """Authored 0.12: the bridge alone is held at +30, and the trim moves from there."""
        cut = {"gain_db": 0.0, "user_trim_db": -10.0, "trim_law": pn.TRIM_LAW_STAGE}
        self.assertAlmostEqual(pn.legacy_norm_db(cut, 0.12), pn.LEGACY_NORM_MAX_DB - 10.0, places=9)
        boost = {**cut, "user_trim_db": 10.0}
        self.assertEqual(pn.legacy_norm_db(boost, 0.12), pn.LEGACY_NORM_MAX_DB)

    def test_the_old_law_row_shows_its_stage_level(self) -> None:
        entry = {"user_trim_db": -6.0}
        self.assertAlmostEqual(pn.norm_fader_db(entry, 0.547),
                               pn.legacy_norm_db(entry, 0.547), places=9)
        self.assertEqual(pn.norm_fader_db({"user_trim_db": -6.0, "trim_law": pn.TRIM_LAW_STAGE}, 0.547),
                         -6.0)
        self.assertEqual(pn.norm_fader_db({"gain_db": 3.0, "user_trim_db": -2.0, "cal_path": CAL}), -2.0)
        self.assertEqual(pn.norm_fader_db(None), 0.0)

    def test_recalibrating_a_stage_law_row_keeps_what_it_plays(self) -> None:
        store = write_store(self._tmp.name, {"S": {"user_trim_db": -6.0, "trim_law": pn.TRIM_LAW_STAGE}})
        heard = store.stage_norm("S", authored_volume=0.8)[0]
        record = store.set_calibration("S", 4.0, -18.0, authored_volume=0.8)
        self.assertIsNotNone(record)
        self.assertAlmostEqual(store.stage_norm("S", authored_volume=0.8)[0], heard, places=2)

    def test_the_fader_bottom_leaves_a_min_row_alone(self) -> None:
        """A cut kept below -30: the fader at its bottom is a no-op, not -30 (louder)."""
        store = write_store(self._tmp.name, {"M": {"gain_db": 2.0, "user_trim_db": -52.3, "cal_path": CAL}})
        control, browser = self._browser(store, "M", 0.8)
        control.write(browser, control.spec.min_value, persist=False)
        control.write(browser, control.spec.min_value - 5.0, persist=False)
        self.assertEqual(store.get_entry("M")["user_trim_db"], -52.3)
        control.write(browser, control.spec.min_value + 1.0, persist=False)
        self.assertEqual(store.get_entry("M")["user_trim_db"], control.spec.min_value + 1.0,
                         "control: a move up off the bottom is written")
        store.set_user_trim_db("M", -10.0)
        control.write(browser, control.spec.min_value, persist=False)
        self.assertEqual(store.get_entry("M")["user_trim_db"], control.spec.min_value,
                         "control: a row above the bottom is written down to it")


class LegacyAndStaleRows(unittest.TestCase):
    """Rows the stage did not measure: each keeps the owner's cut; none plays loud."""

    def test_a_trim_only_row_keeps_its_cut(self) -> None:
        """The old code took a missing gain as 0: the trim alone was heard."""
        entry = {"user_trim_db": -8.0, "enabled": True}
        stage = pn.legacy_norm_db(entry, 0.9)
        self.assertAlmostEqual(60.0 * math.log10(0.9) + stage, 3.0 * -8.0, places=9)
        self.assertIsNone(pn.legacy_norm_db({"enabled": True}, 0.9), "no gain, no trim: as authored")
        with tempfile.TemporaryDirectory() as tmp:
            store = write_store(tmp, {"T": entry})
            self.assertAlmostEqual(store.stage_norm("T", authored_volume=0.9)[0], stage, places=9)

    def test_the_norm_fader_acts_on_an_uncalibrated_patch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = write_store(tmp, {})
            self.assertEqual(store.stage_norm("U", authored_volume=1.0), (0.0, None))
            store.set_user_trim_db("U", -6.0)
            self.assertEqual(store.get_entry("U")[pn.TRIM_LAW_KEY], pn.TRIM_LAW_STAGE)
            self.assertAlmostEqual(store.stage_norm("U", authored_volume=1.0)[0], -6.0, places=9)
            self.assertAlmostEqual(store.stage_norm("U", authored_volume=0.547)[0], -6.0, places=9)
            store.clear_user_trim_db("U")
            self.assertNotIn(pn.TRIM_LAW_KEY, store.get_entry("U"))

    def test_a_trim_only_row_migrates_on_recalibration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = write_store(tmp, {"T": {"user_trim_db": -8.0, "enabled": True}})
            record = store.set_calibration("T", -4.0, -18.0, authored_volume=0.9)
            expected, _ = pn.migrate_trim_to_stage(gain_db_old=0.0, trim_db_old=-8.0,
                                                   gain_db_new=-4.0, authored_volume=0.9)
            self.assertIsNotNone(record)
            self.assertEqual(store.get_entry("T")["user_trim_db"], round(expected, 3))
            heard = 60.0 * math.log10(0.9) + store.stage_norm("T", authored_volume=0.9)[0]
            self.assertAlmostEqual(heard, -24.0, places=2)

    def test_the_legacy_bridge_is_capped(self) -> None:
        # Authored 0.12 (plausible, -55 dB): undoing it would take +55 dB.
        stage = pn.legacy_norm_db({"gain_db": 0.0}, 0.12)
        self.assertEqual(stage, pn.LEGACY_NORM_MAX_DB)
        self.assertLess(pn.legacy_norm_db({"gain_db": 0.0}, 0.5), pn.LEGACY_NORM_MAX_DB)

    def test_an_implausible_authored_volume_is_unknown(self) -> None:
        """legacy_norm_db({'gain_db': 10}, 0.05) was +78 dB."""
        for bad in (0.05, 0.0, -0.2, 1.5, float("nan"), True):
            with self.subTest(bad=bad):
                self.assertIsNone(pn.plausible_authored_volume(bad))
                self.assertEqual(pn.authored_level_db(bad), 0.0)
                self.assertEqual(pn.legacy_norm_db({"gain_db": 10.0}, bad),
                                 pn.legacy_norm_db({"gain_db": 10.0}, None))
                self.assertLessEqual(pn.legacy_norm_db({"gain_db": 10.0}, bad), 0.0)
        self.assertEqual(pn.plausible_authored_volume(0.1), 0.1)

    def test_the_legacy_bridge_uses_the_louder_scene(self) -> None:
        entry = {"gain_db": -10.0}
        self.assertEqual(pn.legacy_norm_db(entry, 0.5, 0.9), pn.legacy_norm_db(entry, 0.9))
        self.assertEqual(pn.legacy_norm_db(entry, 0.9, 0.5), pn.legacy_norm_db(entry, 0.9))
        self.assertEqual(pn.legacy_norm_db(entry, None, 0.9), pn.legacy_norm_db(entry, None))

    def test_a_stale_row_keeps_its_cut_and_loses_its_boost(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = write_store(tmp, {
                "Cut": {"gain_db": -12.0, "cal_path": CAL, "authored_volume": 0.8,
                        "true_peak_unity_dbtp": {"s": -20.0}},
                "Boost": {"gain_db": 9.0, "cal_path": CAL, "authored_volume": 0.8,
                          "true_peak_unity_dbtp": {"s": -20.0}},
            })
            self.assertEqual(store.stage_norm("Cut", authored_volume=0.6), (-12.0, None))
            self.assertEqual(store.stage_norm("Boost", authored_volume=0.6), (0.0, None))
            self.assertEqual(store.stage_norm("Cut", authored_volume=0.8), (-12.0, 20.0))

    def test_an_edit_to_scene_b_is_stale(self) -> None:
        entry = {"gain_db": 9.0, "cal_path": CAL, "authored_volume": 0.8, "authored_volume_b": 0.7}
        self.assertFalse(pn.is_stale(entry, 0.8, 0.7))
        self.assertTrue(pn.is_stale(entry, 0.8, 0.5))
        self.assertTrue(pn.is_stale(entry, 0.6, 0.7))
        self.assertFalse(pn.is_stale(entry, 0.8, None), "unknown now: not compared")
        self.assertFalse(pn.is_stale({**entry, "authored_volume_b": None}, 0.8, 0.5),
                         "not recorded then: not compared")
        with tempfile.TemporaryDirectory() as tmp:
            store = write_store(tmp, {"P": entry})
            self.assertEqual(store.stage_norm("P", authored_volume=0.8, authored_volume_b=0.5), (0.0, None))


class KeepaliveConfirms(unittest.TestCase):
    """The keepalive re-sends a boost only for a patch Surge confirms on that tick.

    Surge can hold a patch the UI did not load: a measurement harness's load, a
    crash restart, an audio-profile switch whose reload is still queued. A boost
    for the UI's patch is wrong there and can be loud.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = write_store(self._tmp.name, {
            "Quiet": {"gain_db": 6.0, "cal_path": CAL, "enabled": True},
            "Hot": {"gain_db": -10.0, "cal_path": CAL, "enabled": True},
        })
        self.loader, self.line = make_loader(self.store)
        self.now = 1000.0

    def tick(self) -> list:
        self.line.events.clear()
        self.loader.keep_stage_alive(now=self.now)
        self.now += STAGE_KEEPALIVE_S
        return list(self.line.events)

    def test_another_patch_in_surge_loses_the_boost(self) -> None:
        load(self.loader, "Quiet")
        self.assertEqual(self.tick(), [("/q/patch", None), gain(6.0)])
        self.line.last_load = "/p/Someone else's"
        self.assertEqual(self.tick(), [("/q/patch", None), gain(0.0)])
        self.assertEqual(self.tick(), [("/q/patch", None), gain(0.0)])
        self.line.last_load = "/p/Quiet"
        self.assertEqual(self.tick(), [("/q/patch", None), gain(6.0)], "confirmed again: back")

    def test_a_silent_surge_loses_the_boost(self) -> None:
        load(self.loader, "Quiet")
        self.line.surge_confirms = False
        self.assertEqual(self.tick(), [("/q/patch", None), gain(0.0)])

    def test_the_cap_keeps_the_vol_fader_and_headroom(self) -> None:
        load(self.loader, "Quiet")
        self.loader.set_volume(0.5, headroom_db=-6.0)
        vol = pn.volume_fader_trim_to_db(0.5, fader_min=VOLUME_MIN, fader_max=VOLUME_MAX)
        self.line.surge_confirms = False
        self.assertEqual(self.tick(), [("/q/patch", None), gain(-6.0 + vol)])

    def test_an_unconfirmed_load_between_two_boosts_sends_no_boost(self) -> None:
        """Held at the quieter of +6 and +3, and that is still a boost on a patch
        Surge has not confirmed: it goes out capped. The cut to +3 lands first."""
        self.store.set_calibration("Mild", 3.0, -18.0)
        load(self.loader, "Quiet")
        self.line.surge_confirms = False
        self.line.events.clear()
        load(self.loader, "Mild")
        self.assertEqual(self.line.stage(), [gain(3.0), gain(0.0)])

    def test_a_cut_needs_no_confirmation(self) -> None:
        """A cut is right for any patch: no question, and it still goes out."""
        load(self.loader, "Hot")
        self.line.last_load = "/p/Someone else's"
        self.assertEqual(self.tick(), [gain(-10.0)])

    def test_a_fader_move_after_a_mismatch_stays_capped(self) -> None:
        load(self.loader, "Quiet")
        self.line.surge_confirms = False
        self.tick()
        self.line.events.clear()
        self.loader.set_volume(VOLUME_MAX)
        self.loader.refresh_patch_volume("Quiet")
        self.assertEqual(self.line.stage(), [gain(0.0), gain(0.0)])

    def test_the_wait_is_short(self) -> None:
        from patch_browser import patch_loader

        load(self.loader, "Quiet")
        with mock.patch.object(self.loader, "query_loaded_patch_path", return_value=None) as query:
            self.loader.keep_stage_alive(now=self.now)
        query.assert_called_once_with(timeout_s=patch_loader.KEEPALIVE_CONFIRM_TIMEOUT_S)
        self.assertLessEqual(patch_loader.KEEPALIVE_CONFIRM_TIMEOUT_S, 0.1)


class SurgeReplies(unittest.TestCase):
    """Real OSC datagrams, over a real socket, into the authored-volume read.

    Surge's OSC out carries echoes of every parameter that moves, and each reply
    has a display string in its parameter's own units. Only the reply for the
    queried parameter, and only its float, is a volume.
    """

    @staticmethod
    def datagram(address: str, *args) -> bytes:
        from pythonosc.osc_message_builder import OscMessageBuilder

        builder = OscMessageBuilder(address=address)
        for arg in args:
            builder.add_arg(arg)
        return builder.build().dgram

    def test_the_parser_takes_only_the_queried_parameter(self) -> None:
        parse = PatchLoader._parse_surge_param_query
        path = "/param/a/amp/volume"
        self.assertAlmostEqual(parse(self.datagram(path, 0.8, "-5.81 dB"), path), 0.8, places=6)
        self.assertIsNone(parse(self.datagram("/param/a/aeg/sustain", 0.05, "5.00 %"), path))
        self.assertIsNone(parse(self.datagram(path, "9.64 %"), path), "a display string is not a value")
        self.assertIsNone(parse(self.datagram(path, "0.5"), path))
        self.assertIsNone(parse(self.datagram(path, 1.7, "x"), path))
        self.assertIsNone(parse(self.datagram("/patch", "/p/Quiet"), path))
        self.assertIsNone(parse(b"garbage", path))

    def test_the_query_skips_echoes_over_a_real_socket(self) -> None:
        import socket
        import threading

        def free_port() -> int:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.bind(("127.0.0.1", 0))
                return s.getsockname()[1]

        out_port = free_port()
        surge = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        surge.bind(("127.0.0.1", 0))
        self.addCleanup(surge.close)
        replies = {
            "/q/param/a/amp/volume": [
                self.datagram("/param/a/aeg/sustain", 0.05, "5.00 %"),   # an echo first
                self.datagram("/param/a/amp/volume", "9.64 %"),         # no float
                self.datagram("/param/a/amp/volume", 0.8, "-5.81 dB"),   # the answer
            ],
            "/q/param/b/amp/volume": [self.datagram("/param/b/aeg/sustain", 0.3, "30.00 %")],
        }

        def serve() -> None:
            from pythonosc.osc_message import OscMessage

            surge.settimeout(2.0)
            for _ in range(2):
                try:
                    data, _addr = surge.recvfrom(4096)
                except OSError:
                    return
                for reply in replies.get(OscMessage(data).address, []):
                    surge.sendto(reply, ("127.0.0.1", out_port))

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        loader = PatchLoader(osc_port=surge.getsockname()[1], osc_out_port=out_port,
                             fx_policy=mock.Mock(), fx_store=mock.Mock())
        self.assertAlmostEqual(loader._query_osc_float("/param/a/amp/volume"), 0.8, places=6)
        self.assertIsNone(loader._query_osc_float("/param/b/amp/volume"),
                          "only another parameter's echo came back: unknown")
        thread.join(3.0)

    def test_a_near_zero_authored_reading_is_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = write_store(tmp, {"Old": {"gain_db": 10.0, "enabled": True}})
            loader, line = make_loader(store, authored=0.05)
            load(loader, "Old")
            self.assertIsNone(loader._authored_volume)
            self.assertLessEqual(loader._norm_db, 0.0)
            self.assertEqual(line.stage()[-1], gain(0.0))


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
            loader._surge_confirmed = True
            loader.set_volume(VOLUME_MAX)
        self.assertEqual(line.stage(), [gain(fx_host_protocol.GAIN_MAX_DB)])


if __name__ == "__main__":
    unittest.main()
