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


def _write_float32_loop(path: Path, click_offsets_ms: list[float], beats: int = 8) -> None:
    """A float32 WAV, which is what SooperLooper actually saves."""
    import struct as _struct
    total = int(beats * BEAT_S * RATE)
    samples = [0.0] * total
    for b in range(beats):
        for off in click_offsets_ms:
            start = int((b * BEAT_S + off / 1000.0) * RATE)
            if not (0 <= start < total - 500):
                continue
            for i in range(400):
                env = 1.0 - (i / 400.0)
                samples[start + i] = 0.67 * env * math.sin(2 * math.pi * 900 * i / RATE)
    payload = b"".join(_struct.pack("<f", v) for v in samples)
    fmt = _struct.pack("<HHIIHH", 3, 1, RATE, RATE * 4, 4, 32)
    body = (b"WAVE"
            + b"fmt " + _struct.pack("<I", len(fmt)) + fmt
            + b"data" + _struct.pack("<I", len(payload)) + payload)
    path.write_bytes(b"RIFF" + _struct.pack("<I", len(body)) + body)


class Float32ReaderTests(unittest.TestCase):
    """SooperLooper saves 32-bit IEEE float. Python's `wave` module cannot read
    it at all ("unknown format: 3"), which killed the first live run after a
    perfectly good recording."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_float32_loop_is_read_and_timed(self):
        p = self.tmp / "f32.wav"
        _write_float32_loop(p, [6.0])
        r = mla.analyse(p, BEAT_S)
        self.assertAlmostEqual(r["median_error_ms"], 6.0, delta=0.6)

    def test_float32_silence_still_halts(self):
        p = self.tmp / "f32-silent.wav"
        _write_float32_loop(p, [])
        with self.assertRaises(mla.Halt):
            mla.analyse(p, BEAT_S)

    def test_a_non_riff_file_halts_rather_than_decoding_garbage(self):
        p = self.tmp / "junk.bin"
        p.write_bytes(b"not a wav file at all" * 100)
        with self.assertRaises(mla.Halt):
            mla.analyse(p, BEAT_S)

    def test_unsupported_encoding_halts_by_name(self):
        import struct as _struct
        p = self.tmp / "weird.wav"
        fmt = _struct.pack("<HHIIHH", 7, 1, RATE, RATE, 1, 8)   # mu-law
        payload = b"\x00" * 1000
        body = (b"WAVE" + b"fmt " + _struct.pack("<I", len(fmt)) + fmt
                + b"data" + _struct.pack("<I", len(payload)) + payload)
        p.write_bytes(b"RIFF" + _struct.pack("<I", len(body)) + body)
        with self.assertRaises(mla.Halt):
            mla.analyse(p, BEAT_S)


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


def _write_overdub_loop(path: Path, pass2_late_ms: float, beats: int = 8,
                        start_phase_ms: float = 0.0) -> None:
    """A loop with a take on the beats and an overdub on the offbeats.

    `start_phase_ms` shifts BOTH passes together — it is the loop's own start
    phase, which the live run measured at 145.5 ms. A correct differential
    analyser must be blind to it.
    """
    total = int(beats * BEAT_S * RATE)
    samples = [0.0] * total

    def click(at_s: float) -> None:
        start = int(at_s * RATE)
        if not (0 <= start < total - 500):
            return
        for i in range(400):
            env = 1.0 - (i / 400.0)
            samples[start + i] += 0.6 * env * math.sin(2 * math.pi * 900 * i / RATE)

    for b in range(beats):
        base = b * BEAT_S + start_phase_ms / 1000.0
        click(base)
        click(base + mla.OVERDUB_SHIFT_BEATS * BEAT_S + pass2_late_ms / 1000.0)

    import struct as _struct
    payload = b"".join(_struct.pack("<f", max(-1.0, min(1.0, v))) for v in samples)
    fmt = _struct.pack("<HHIIHH", 3, 1, RATE, RATE * 4, 4, 32)
    body = (b"WAVE" + b"fmt " + _struct.pack("<I", len(fmt)) + fmt
            + b"data" + _struct.pack("<I", len(payload)) + payload)
    path.write_bytes(b"RIFF" + _struct.pack("<I", len(body)) + body)


class OverdubAnalyserTests(unittest.TestCase):
    """The differential analyser. The previous design measured onset-mod-beat,
    which on the live appliance reported a rock-steady 145.5 ms that was the
    loop's start phase and not a timing error at all."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_a_perfect_overdub_reads_about_zero(self):
        p = self.tmp / "od-perfect.wav"
        _write_overdub_loop(p, pass2_late_ms=0.0)
        r = mla.analyse_overdub(p, BEAT_S)
        self.assertAlmostEqual(r["median_error_ms"], 0.0, delta=0.5)

    def test_a_late_overdub_is_recovered_at_full_size(self):
        """POSITIVE CONTROL: an overdub 12 ms late must read +12, not 12/2 and
        not 250 - 12."""
        p = self.tmp / "od-late.wav"
        _write_overdub_loop(p, pass2_late_ms=12.0)
        r = mla.analyse_overdub(p, BEAT_S)
        self.assertAlmostEqual(r["median_error_ms"], 12.0, delta=0.6)

    def test_an_early_overdub_reads_negative(self):
        p = self.tmp / "od-early.wav"
        _write_overdub_loop(p, pass2_late_ms=-9.0)
        r = mla.analyse_overdub(p, BEAT_S)
        self.assertAlmostEqual(r["median_error_ms"], -9.0, delta=0.6)

    def test_the_loops_own_start_phase_cancels(self):
        """THE POINT OF THE REDESIGN. A loop starting 145.5 ms off the beat grid
        — exactly what the appliance produced — must still report the overdub
        error, not the start phase."""
        p = self.tmp / "od-phased.wav"
        _write_overdub_loop(p, pass2_late_ms=7.0, start_phase_ms=145.5)
        r = mla.analyse_overdub(p, BEAT_S)
        self.assertAlmostEqual(r["median_error_ms"], 7.0, delta=0.6)

    def test_a_single_pass_halts_rather_than_comparing_nothing(self):
        """NEGATIVE CONTROL: with no overdub there is no alternation, and the
        beat-spaced intervals must be rejected, not read as a huge error."""
        p = self.tmp / "od-one-pass.wav"
        _write_loop(p, [0.0])
        with self.assertRaises(mla.Halt):
            mla.analyse_overdub(p, BEAT_S)

    def test_the_release_transient_is_not_counted_as_an_onset(self):
        """The live run detected every note twice: NOTE_LEN_S is 120 ms and the
        old fixed 50 ms gap let each note-off through as a fresh onset."""
        p = self.tmp / "twins.wav"
        _write_loop(p, [0.0, 100.0], beats=8)
        r = mla.analyse(p, BEAT_S)
        self.assertLessEqual(r["onsets"], 8)
        self.assertAlmostEqual(r["median_error_ms"], 0.0, delta=0.5)


class LoopPhaseAssertionTests(unittest.TestCase):
    def test_a_whole_beat_loop_passes_and_reports_its_error(self):
        err = mla.assert_loop_is_whole_beats(5.4997, 0.5)      # 11 beats, live
        self.assertLess(err, mla.LOOP_LEN_TOLERANCE_MS)

    def test_a_loop_out_of_phase_with_the_grid_halts(self):
        """NEGATIVE CONTROL: a loop that does not repeat in phase drifts the
        overdub by a different amount every repetition."""
        with self.assertRaises(mla.Halt):
            mla.assert_loop_is_whole_beats(5.37, 0.5)


class OverdubSignRobustnessTests(unittest.TestCase):
    """The defect the earlier design hid: at a half-beat shift the take->overdub
    and overdub->take intervals are (half+d) and (half-d), so the two signs of
    one displacement cancel and the median lands on whichever direction happens
    to be counted once more. A 20 ms live control read 18.3 ms with sd 21.7 --
    the right magnitude by an accident of parity. The synthetic tests passed for
    the same reason, so they were not controls at all.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_both_interval_directions_are_present_and_agree(self):
        p = self.tmp / "od-signs.wav"
        _write_overdub_loop(p, pass2_late_ms=15.0)
        r = mla.analyse_overdub(p, BEAT_S)
        kinds = set(r["interval_kinds"]) - {"skip"}
        self.assertEqual(kinds, {"short", "long"},
                         "one direction only — the sign test is vacuous")
        # Every sample must carry the SAME sign. Under the half-beat design
        # these came in as +15 and -15 and averaged each other away.
        self.assertTrue(all(e > 0 for e in r["errors_ms"]),
                        f"directions disagree in sign: {r['errors_ms']}")
        self.assertAlmostEqual(r["median_error_ms"], 15.0, delta=0.6)
        self.assertLess(r["sd_ms"], 1.5, "spread should be sub-millisecond")

    def test_the_answer_does_not_depend_on_how_many_intervals_are_counted(self):
        """Truncating one interval must not move the result. Under the old
        design it moved it by 2d."""
        p = self.tmp / "od-parity.wav"
        _write_overdub_loop(p, pass2_late_ms=15.0, beats=8)
        full = mla.analyse_overdub(p, BEAT_S)
        q = self.tmp / "od-parity-odd.wav"
        _write_overdub_loop(q, pass2_late_ms=15.0, beats=7)
        odd = mla.analyse_overdub(q, BEAT_S)
        self.assertAlmostEqual(full["median_error_ms"], odd["median_error_ms"],
                               delta=0.5)
