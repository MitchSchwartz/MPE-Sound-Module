"""Conformance for the loop-alignment analyser, on synthetic audio.

The analyser is the part that turns a recorded loop into a timing error, so it
is the part that can silently report a comfortable number. These are its
positive and negative controls, and they run in CI rather than on the Pi:
build a loop whose answer is known by construction, and require that answer.
"""

from __future__ import annotations

import importlib.util
import math
import struct
import sys
import tempfile
import unittest
import wave
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

_spec = importlib.util.spec_from_file_location(
    "measure_loop_alignment", REPO / "scripts" / "measure-loop-alignment.py"
)
mla = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(mla)

RATE = 48000
BEAT_S = 0.5


def _write_loop(path: Path, click_offsets_ms: list[float], beats: int = 8) -> None:
    """A loop of `beats` beats with a short click near each beat boundary."""
    total = int(beats * BEAT_S * RATE)
    samples = [0] * total
    for b in range(beats):
        for off in click_offsets_ms:
            start = int((b * BEAT_S + off / 1000.0) * RATE)
            if not (0 <= start < total - 500):
                continue
            for i in range(400):          # short, sharp burst
                env = 1.0 - (i / 400.0)
                samples[start + i] = int(22000 * env * math.sin(2 * math.pi * 900 * i / RATE))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(RATE)
        wf.writeframes(b"".join(struct.pack("<h", s) for s in samples))


class AnalyserConformanceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_perfectly_aligned_loop_reads_about_zero(self):
        """POSITIVE CONTROL: clicks exactly on the beat must read ~0 ms."""
        p = self.tmp / "aligned.wav"
        _write_loop(p, [0.0])
        r = mla.analyse(p, BEAT_S)
        self.assertAlmostEqual(r["median_error_ms"], 0.0, delta=0.5)

    def test_a_known_late_shift_is_recovered_at_full_size(self):
        """POSITIVE CONTROL: the whole point. A 10 ms late click must read +10."""
        p = self.tmp / "late.wav"
        _write_loop(p, [10.0])
        r = mla.analyse(p, BEAT_S)
        self.assertAlmostEqual(r["median_error_ms"], 10.0, delta=0.6)

    def test_a_known_early_shift_reads_negative(self):
        """Early must not be reported as a large positive error — the wrap to
        +/- half a beat is what makes 'early' and 'late' distinguishable."""
        p = self.tmp / "early.wav"
        _write_loop(p, [-8.0])
        r = mla.analyse(p, BEAT_S)
        self.assertAlmostEqual(r["median_error_ms"], -8.0, delta=0.6)

    def test_silence_halts_rather_than_reporting_perfect_timing(self):
        """NEGATIVE CONTROL. Digital silence has no onsets; reporting 0 ms error
        would be the instrument publishing its blindness as a perfect score."""
        p = self.tmp / "silence.wav"
        _write_loop(p, [])
        with self.assertRaises(mla.Halt):
            mla.analyse(p, BEAT_S)

    def test_signal_below_threshold_halts(self):
        """NEGATIVE CONTROL: audio present but nothing crossing the detector."""
        p = self.tmp / "quiet.wav"
        _write_loop(p, [0.0])
        with self.assertRaises(mla.Halt):
            mla.analyse(p, BEAT_S, threshold_ratio=1.5)

    def test_every_beat_contributes_an_independent_sample(self):
        p = self.tmp / "many.wav"
        _write_loop(p, [4.0], beats=8)
        r = mla.analyse(p, BEAT_S)
        self.assertGreaterEqual(r["onsets"], 6)
        self.assertEqual(len(r["errors_ms"]), r["onsets"])

    def test_reported_fields_are_all_present_and_numeric(self):
        """A field that can come back missing or 'unknown' is how a result gets
        believed without being read."""
        p = self.tmp / "fields.wav"
        _write_loop(p, [2.0])
        r = mla.analyse(p, BEAT_S)
        for key in ("rate", "loop_seconds", "beat_seconds", "onsets",
                    "median_error_ms", "mean_error_ms"):
            self.assertIn(key, r)
            self.assertIsInstance(r[key], (int, float), f"{key} is not numeric")


class OptInGateTests(unittest.TestCase):
    def test_the_harness_refuses_without_the_opt_in(self):
        """It RESETS a loop. It must never be reachable by accident."""
        source = (REPO / "scripts" / "measure-loop-alignment.py").read_text(encoding="utf-8")
        self.assertIn("MPE_ALLOW_LOOP_MEASURE", source)
        self.assertIn("_refuse_unless_opted_in()", source)

    def test_it_is_not_referenced_by_any_unit(self):
        offenders = [
            u.name for u in sorted((REPO / "config").glob("*.service"))
            if "measure-loop-alignment" in u.read_text(encoding="utf-8", errors="ignore")
        ]
        self.assertEqual(offenders, [], f"units reference the harness: {offenders}")


if __name__ == "__main__":
    unittest.main()
