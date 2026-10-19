"""Calibration for the stage: Surge alone at authored volume, the gain by arithmetic.

The capture is faked; the arithmetic, the pad path, what is written and the
instrument controls' pass/fail rules are real.
"""

from __future__ import annotations

import json
import math
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

from patch_browser import calibration_controls as cc
from patch_browser import patch_normalization as pn

from test_calibration_integrity import load_cal_module


def anchor(cal, lufs, peak, sample=None):
    return cal.Anchor(lufs, peak, sample)


class Clip(unittest.TestCase):
    def setUp(self) -> None:
        self.cal = load_cal_module()

    def test_sample_peak_decides(self) -> None:
        self.assertTrue(self.cal.is_clipped(anchor(self.cal, -12.0, 0.5, -0.05)))
        self.assertFalse(self.cal.is_clipped(anchor(self.cal, -12.0, 0.5, -0.2)))

    def test_without_a_sample_peak_the_true_peak_stands_in(self) -> None:
        self.assertTrue(self.cal.is_clipped((-12.0, -0.1)))
        self.assertFalse(self.cal.is_clipped((-12.0, -0.3)))

    def test_wav_sample_peak(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.wav"
            with wave.open(str(path), "wb") as w:
                w.setnchannels(2)
                w.setsampwidth(2)
                w.setframerate(48000)
                w.writeframes(struct.pack("<4h", 0, -16384, 100, 8192))
            self.assertAlmostEqual(self.cal.wav_sample_peak_dbfs(path), 20 * math.log10(0.5), places=9)
            self.assertIsNone(self.cal.wav_sample_peak_dbfs(Path(tmp) / "missing.wav"))


class UnityAnchors(unittest.TestCase):
    def setUp(self) -> None:
        self.cal = load_cal_module()
        self.loader = mock.Mock(osc_enabled=True)
        self.sent: list[tuple[str, float]] = []
        self.loader.osc_client.send_message.side_effect = lambda a, v: self.sent.append((a, v))

    def _run(self, strikes, sustains, authored=0.8):
        with (
            mock.patch.object(self.cal, "measure_strike_anchor_lufs", side_effect=strikes),
            mock.patch.object(self.cal, "measure_sustain_anchor_lufs", side_effect=sustains),
            mock.patch.object(self.cal.time, "sleep"),
        ):
            return self.cal.measure_unity_anchors(self.loader, object(), "dev", authored, name="P")

    def test_clean_capture_touches_nothing(self) -> None:
        got = self._run([anchor(self.cal, -20.0, -6.0, -6.5)], [anchor(self.cal, -22.0, -7.0, -7.5)])
        self.assertEqual((got.strike, got.sustain, got.padded_db), ((-20.0, -6.0), (-22.0, -7.0), 0.0))
        self.assertEqual(self.sent, [])

    def test_a_clipped_capture_is_padded_and_the_pad_added_back(self) -> None:
        got = self._run(
            [anchor(self.cal, -8.0, 0.4, 0.0), anchor(self.cal, -19.0, -11.0, -11.5)],
            [anchor(self.cal, -10.0, -1.0, -1.2), anchor(self.cal, -21.0, -12.0, -12.5)],
        )
        pad = pn.CAPTURE_PAD_DB
        self.assertEqual(got.padded_db, pad)
        self.assertEqual(got.strike, (-19.0 + pad, -11.0 + pad))
        self.assertEqual(got.sustain, (-21.0 + pad, -12.0 + pad))
        padded = pn.padded_volume(0.8, pad)
        self.assertEqual(self.sent, [
            ("/param/a/amp/volume", padded), ("/param/b/amp/volume", padded),
            ("/param/a/amp/volume", 0.8), ("/param/b/amp/volume", 0.8),
        ])

    def test_clipped_with_no_authored_volume_fails_and_touches_nothing(self) -> None:
        got = self._run([anchor(self.cal, -8.0, 0.4, 0.0)], [anchor(self.cal, -10.0, -1.0, -1.2)],
                        authored=None)
        self.assertIsNone(got)
        self.assertEqual(self.sent, [])

    def test_still_clipped_padded_fails_and_restores(self) -> None:
        got = self._run(
            [anchor(self.cal, -8.0, 0.4, 0.0), anchor(self.cal, -8.0, 0.4, 0.0)],
            [anchor(self.cal, -10.0, -1.0, -1.2), anchor(self.cal, -10.0, -1.0, -1.2)],
        )
        self.assertIsNone(got)
        self.assertEqual(self.sent[-2:], [("/param/a/amp/volume", 0.8), ("/param/b/amp/volume", 0.8)])


    def test_a_capture_that_fails_mid_pad_still_restores(self) -> None:
        # Safe whether or not Surge keeps amp/volume across patch loads: the pad
        # never outlives the capture it was set for.
        with self.assertRaises(RuntimeError):
            self._run(
                [anchor(self.cal, -8.0, 0.4, 0.0), RuntimeError("capture died")],
                [anchor(self.cal, -10.0, -1.0, -1.2)],
            )
        self.assertEqual(self.sent[-2:], [("/param/a/amp/volume", 0.8), ("/param/b/amp/volume", 0.8)])


class CalibratePatch(unittest.TestCase):
    def setUp(self) -> None:
        self.cal = load_cal_module()

    def test_writes_a_stage_entry_by_arithmetic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = pn.PatchNormalizationStore(Path(tmp) / "norm.json")
            store.set_patch_dirs([])
            loader = mock.Mock(osc_enabled=True, _authored_volume=0.85)
            loader.load_patch.return_value = True
            with (
                mock.patch.object(self.cal, "measure_strike_anchor_lufs",
                                  return_value=anchor(self.cal, -20.0, -8.0, -8.5)),
                mock.patch.object(self.cal, "measure_sustain_anchor_lufs",
                                  return_value=anchor(self.cal, -24.0, -9.0, -9.5)),
                mock.patch.object(self.cal, "measure_light_touch_lufs", return_value=None),
                mock.patch.object(self.cal.time, "sleep"),
            ):
                result = self.cal.calibrate_patch(
                    Path("/tmp/Lead.fxp"), loader, store, audio_device="dev",
                    mock_lufs=None, dry_run=False, midi_out=mock.Mock(),
                )
            self.assertTrue(result.ok)
            entry = store.get_entry("Lead", patch_path="/tmp/Lead.fxp")
            gain = pn.compute_gain_db_dual_anchor(-20.0, -8.0, -24.0, -9.0)
            self.assertEqual(entry["cal_path"], pn.CAL_PATH)
            self.assertEqual(entry["gain_db"], round(gain, 2))
            self.assertEqual(entry["lufs_unity"], {"strike": -20.0, "sustain": -24.0})
            self.assertEqual(entry["true_peak_unity_dbtp"], {"strike": -8.0, "sustain": -9.0})
            self.assertEqual(entry["authored_volume"], 0.85)
            self.assertEqual(entry["capture_padded_db"], 0.0)
            self.assertAlmostEqual(entry["post_gain_peak_dbtp"], round(-8.0 + gain, 2))
            # No amp/volume and no closed loop: one load, nothing sent to Surge.
            loader.osc_client.send_message.assert_not_called()

    def _calibrate(self, store, name: str, *, authored=0.85, authored_b=None):
        import contextlib
        import io

        loader = mock.Mock(osc_enabled=True, _authored_volume=authored, _authored_volume_b=authored_b)
        loader.load_patch.return_value = True
        out = io.StringIO()
        with (
            mock.patch.object(self.cal, "measure_strike_anchor_lufs",
                              return_value=anchor(self.cal, -20.0, -8.0, -8.5)),
            mock.patch.object(self.cal, "measure_sustain_anchor_lufs",
                              return_value=anchor(self.cal, -24.0, -9.0, -9.5)),
            mock.patch.object(self.cal, "measure_light_touch_lufs", return_value=None),
            mock.patch.object(self.cal.time, "sleep"),
            contextlib.redirect_stdout(out),
        ):
            result = self.cal.calibrate_patch(
                Path(f"/tmp/{name}.fxp"), loader, store, audio_device="dev",
                mock_lufs=None, dry_run=False, midi_out=mock.Mock(),
            )
        return result, out.getvalue()

    def test_every_row_reports_its_level_before_and_after(self) -> None:
        """Not only trimmed rows: an untrimmed pre-stage row, a new one, and a
        re-measured one each print [level], and a row that gets louder says so."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "norm.json"
            path.write_text(json.dumps({
                "Old": {"gain_db": -10.0, "enabled": True},
                "Cut": {"gain_db": -30.0, "cal_path": pn.CAL_PATH, "authored_volume": 0.85,
                        "enabled": True},
            }))
            store = pn.PatchNormalizationStore(path)
            store.set_patch_dirs([])
            gain = round(pn.compute_gain_db_dual_anchor(-20.0, -8.0, -24.0, -9.0), 3)
            after = min(gain, 0.0 - (-8.0))
            want_old_before = pn.legacy_norm_db({"gain_db": -10.0}, 0.85)
            for name, before in (("Old", want_old_before), ("New", 0.0), ("Cut", -30.0)):
                with self.subTest(name=name):
                    result, out = self._calibrate(store, name)
                    self.assertTrue(result.ok)
                    self.assertAlmostEqual(result.level_before_db, before, places=6)
                    self.assertAlmostEqual(result.level_after_db, after, places=6)
                    lines = [line for line in out.splitlines() if line.strip().startswith("[level]")]
                    self.assertEqual(lines, [self.cal.level_report_line(name, before, after)])
                    self.assertEqual("LOUDER" in lines[0], after - before > self.cal.LEVEL_REPORT_LOUDER_DB)
            self.assertIn("LOUDER", self.cal.level_report_line("Cut", -30.0, after))
            self.assertNotIn("LOUDER", self.cal.level_report_line("X", 2.0, 2.4))

    def test_scene_b_is_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = pn.PatchNormalizationStore(Path(tmp) / "norm.json")
            store.set_patch_dirs([])
            self._calibrate(store, "Lead", authored=0.85, authored_b=0.6)
            entry = store.get_entry("Lead", patch_path="/tmp/Lead.fxp")
            self.assertEqual((entry["authored_volume"], entry["authored_volume_b"]), (0.85, 0.6))

    def test_a_padded_capture_is_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = pn.PatchNormalizationStore(Path(tmp) / "norm.json")
            store.set_patch_dirs([])
            loader = mock.Mock(osc_enabled=True, _authored_volume=0.85)
            loader.load_patch.return_value = True
            with (
                mock.patch.object(self.cal, "measure_strike_anchor_lufs", side_effect=[
                    anchor(self.cal, -8.0, 0.4, 0.0), anchor(self.cal, -20.0, -12.0, -12.5)]),
                mock.patch.object(self.cal, "measure_sustain_anchor_lufs", side_effect=[
                    anchor(self.cal, -10.0, -1.0, -1.2), anchor(self.cal, -24.0, -13.0, -13.5)]),
                mock.patch.object(self.cal, "measure_light_touch_lufs", return_value=None),
                mock.patch.object(self.cal.time, "sleep"),
            ):
                result = self.cal.calibrate_patch(
                    Path("/tmp/Hot.fxp"), loader, store, audio_device="dev",
                    mock_lufs=None, dry_run=False, midi_out=mock.Mock(),
                )
            self.assertTrue(result.ok)
            entry = store.get_entry("Hot", patch_path="/tmp/Hot.fxp")
            self.assertEqual(entry["capture_padded_db"], pn.CAPTURE_PAD_DB)
            self.assertEqual(entry["lufs_unity"]["sustain"], -24.0 + pn.CAPTURE_PAD_DB)


class Controls(unittest.TestCase):
    def test_positive(self) -> None:
        self.assertTrue(cc.positive_control((-20.0, -6.0), (-26.2, -11.8), pad_db=6.0).ok)
        self.assertFalse(cc.positive_control((-20.0, -6.0), (-26.4, -12.0), pad_db=6.0).ok)
        self.assertFalse(cc.positive_control((-20.0, -6.0), (-26.0, -11.6), pad_db=6.0).ok)
        self.assertFalse(cc.positive_control((-20.0, -6.0), (-20.0, -6.0), pad_db=6.0).ok, "blind")
        self.assertFalse(cc.positive_control((-20.0, -6.0), (-math.inf, -6.0), pad_db=6.0).ok)

    def test_repeatability(self) -> None:
        self.assertTrue(cc.repeatability_control((-20.0, -6.0), (-20.29, -5.71)).ok)
        self.assertFalse(cc.repeatability_control((-20.0, -6.0), (-20.31, -6.0)).ok)
        self.assertFalse(cc.repeatability_control((-20.0, -6.0), (-20.0, -6.31)).ok)

    def test_isolation(self) -> None:
        self.assertTrue(cc.isolation_control((-20.0, -6.0), (-20.1, -6.1)).ok)
        self.assertFalse(cc.isolation_control((-20.0, -6.0), (-30.0, -16.0)).ok)
        self.assertFalse(cc.isolation_control((-20.0, -6.0), (-math.inf, -math.inf)).ok, "muted = in path")

    def test_stage_audit(self) -> None:
        self.assertTrue(cc.stage_audit(-18.0, (-18.5, -2.8)).ok)
        self.assertFalse(cc.stage_audit(-18.0, (-18.51, -4.0)).ok)
        self.assertFalse(cc.stage_audit(-18.0, (-18.0, -2.79)).ok)

    def _run_controls(self, *, host_running=True, before=0.5):
        """run_controls on a faked capture, a fake stage and a fake mpe-fx-host whose
        state file follows what it is sent while it is running."""
        cal = load_cal_module()
        loader = mock.Mock(osc_enabled=True, _authored_volume=0.8)
        loader.load_patch.return_value = True
        line: list[tuple] = []
        loader.osc_client.send_message.side_effect = lambda a, v: line.append((a, v))
        readings = iter([
            anchor(cal, -20.0, -6.0, -6.5),   # first
            anchor(cal, -26.0, -12.0, -12.5),  # padded
            anchor(cal, -20.1, -6.1, -6.6),   # repeat
            anchor(cal, -20.0, -6.0, -6.5),   # stage muted
        ])
        state = {"gain": before}

        def sustain(*_a):
            line.append(("<capture>",))
            return next(readings)

        def stage(payload):
            line.append(("stage", payload))
            if host_running:
                state["gain"] = float(payload.split()[1])
            return True

        with (
            mock.patch.object(cal, "measure_sustain_anchor_lufs", side_effect=sustain),
            mock.patch.object(cal.time, "sleep"),
        ):
            results = cal.run_controls(loader, object(), "dev", Path("/p/Ref.fxp"), stage_sender=stage,
                                       gain_state=lambda: state["gain"])
        return cal, loader, line, results, state

    def test_run_controls_order_and_safety(self) -> None:
        cal, loader, line, results, state = self._run_controls()
        self.assertEqual([r.name for r in results], ["positive", "repeatability", "isolation"])
        # The control patch is measured only once Surge confirms it loaded (#198).
        self.assertEqual(loader.load_patch.call_args.kwargs["confirm_timeout_s"],
                         cal.PATCH_LOAD_CONFIRM_TIMEOUT_S)
        self.assertTrue(all(r.ok for r in results), results)
        padded = pn.padded_volume(0.8, cal.CONTROL_PAD_DB)
        self.assertEqual(line, [
            ("<capture>",),
            ("/param/a/amp/volume", padded), ("/param/b/amp/volume", padded),
            ("<capture>",),
            ("/param/a/amp/volume", 0.8), ("/param/b/amp/volume", 0.8),
            ("<capture>",),
            ("stage", b"gain 0.00000000"),  # the safe direction: mute, never a boost
            ("<capture>",),
            ("stage", b"gain 0.50000000"),  # and the insert's own gain put back
        ])
        self.assertEqual(state["gain"], 0.5)

    def test_isolation_fails_when_no_insert_took_the_mute(self) -> None:
        """No mpe-fx-host: the capture would match because nothing was muted."""
        _cal, _loader, line, results, _state = self._run_controls(host_running=False)
        isolation = results[2]
        self.assertFalse(isolation.ok)
        self.assertIn("did not take the mute", isolation.detail)
        self.assertEqual(line[-2:], [("stage", b"gain 0.00000000"), ("stage", b"gain 0.50000000")],
                         "nothing captured against a mute that did not land; the gain still put back")

    def test_a_capture_that_dies_muted_still_puts_the_gain_back(self) -> None:
        cal = load_cal_module()
        sent: list[bytes] = []
        state = {"gain": 0.25}

        def stage(payload):
            sent.append(payload)
            state["gain"] = float(payload.split()[1])
            return True

        loader = mock.Mock(osc_enabled=True, _authored_volume=0.8)
        loader.load_patch.return_value = True
        readings = iter([anchor(cal, -20.0, -6.0, -6.5)] * 3 + [RuntimeError("capture died")])

        def sustain(*_a):
            reading = next(readings)
            if isinstance(reading, Exception):
                raise reading
            return reading

        with (
            mock.patch.object(cal, "measure_sustain_anchor_lufs", side_effect=sustain),
            mock.patch.object(cal.time, "sleep"),
            self.assertRaises(RuntimeError),
        ):
            cal.run_controls(loader, object(), "dev", Path("/p/Ref.fxp"), stage_sender=stage,
                             gain_state=lambda: state["gain"])
        self.assertEqual(sent, [b"gain 0.00000000", b"gain 0.25000000"])
        self.assertEqual(state["gain"], 0.25)

    def test_an_already_muted_stage_is_probed_and_left_muted(self) -> None:
        _cal, _loader, line, results, state = self._run_controls(before=0.0)
        self.assertTrue(results[2].ok, results[2])
        stage = [e for e in line if e[0] == "stage"]
        self.assertEqual(stage, [("stage", b"gain 0.00000100"), ("stage", b"gain 0.00000000")])
        self.assertEqual(state["gain"], 0.0)

    def test_with_no_gain_on_record_the_stage_stays_muted(self) -> None:
        _cal, _loader, line, results, state = self._run_controls(before=None)
        self.assertTrue(results[2].ok, results[2])
        self.assertEqual([e for e in line if e[0] == "stage"], [("stage", b"gain 0.00000000")])
        self.assertEqual(state["gain"], 0.0)


class AuditAndBackup(unittest.TestCase):
    def test_audit_predicts_from_the_stored_unity_sustain(self) -> None:
        cal = load_cal_module()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "norm.json"
            path.write_text(json.dumps({"Lead": {
                "gain_db": 5.0, "user_trim_db": 1.0, "cal_path": pn.CAL_PATH, "authored_volume": 0.8,
                "lufs_unity": {"strike": -20.0, "sustain": -24.0},
                "true_peak_unity_dbtp": {"strike": -10.0, "sustain": -11.0}}}))
            store = pn.PatchNormalizationStore(path)
            store.set_patch_dirs([])
            with mock.patch.object(cal, "measure_lufs", return_value=(-27.0, -13.0)):
                result = cal.audit_patch(store, Path("/x/Lead.fxp"), Path("w.wav"), headroom_db=-9.0)
            self.assertTrue(result.ok, result.detail)  # -24 + 6 - 9 = -27
            with mock.patch.object(cal, "measure_lufs", return_value=(-26.0, -13.0)):
                self.assertFalse(cal.audit_patch(store, Path("/x/Lead.fxp"), Path("w.wav"),
                                                 headroom_db=-9.0).ok)

    def test_backup_copies_the_stores(self) -> None:
        cal = load_cal_module()
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "norm.json"
            a.write_text("{}")
            dest = cal.backup_store(a, Path(tmp) / "absent.json", root=Path(tmp) / "bk")
            self.assertEqual([p.name for p in dest.iterdir()], ["norm.json"])
            self.assertIsNone(cal.backup_store(Path(tmp) / "absent.json", root=Path(tmp) / "bk"))


if __name__ == "__main__":
    unittest.main()
