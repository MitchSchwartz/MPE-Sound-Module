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

    def test_run_controls_order_and_safety(self) -> None:
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

        def sustain(*_a):
            line.append(("<capture>",))
            return next(readings)

        def stage(payload):
            line.append(("stage", payload))
            return True

        with (
            mock.patch.object(cal, "measure_sustain_anchor_lufs", side_effect=sustain),
            mock.patch.object(cal.time, "sleep"),
        ):
            results = cal.run_controls(loader, object(), "dev", Path("/p/Ref.fxp"), stage_sender=stage)
        self.assertEqual([r.name for r in results], ["positive", "repeatability", "isolation"])
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
        ])


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
