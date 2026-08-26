"""Unit tests for Tier 3 offline seam merge."""

from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

from scripts.sooperlooper.seam_merge import (
    DEFAULT_DECLICK_SAMPLES,
    merge_stereo_frames,
    merge_tail_at_seam,
    read_float32_stereo_wav,
    write_float32_stereo_wav,
)


def _write_sine(path: Path, *, frames: int, freq: float, amp: float, rate: int = 48000) -> None:
    import math

    samples = [
        (amp * math.sin(2 * math.pi * freq * i / rate), amp * math.sin(2 * math.pi * freq * i / rate))
        for i in range(frames)
    ]
    write_float32_stereo_wav(path, samples, sample_rate=rate)


class SeamMergeTests(unittest.TestCase):
    def test_take_is_preserved_outside_the_tail(self) -> None:
        """The recorded take is never overwritten — only summed onto."""
        main = [(1.0, 1.0)] * 1000
        tail = [(0.3, 0.3)] * 400
        out = merge_stereo_frames(main, tail, declick_samples=0)
        self.assertEqual(len(out), len(main))
        # Everything past the tail is the take, untouched.
        for i in range(400, 1000):
            self.assertEqual(out[i], (1.0, 1.0))
        # The head carries take + ring-out, summed.
        self.assertAlmostEqual(out[0][0], 1.3, places=6)
        self.assertAlmostEqual(out[399][0], 1.3, places=6)

    def test_no_full_scale_step_at_the_seam(self) -> None:
        """Regression: the old head/end crossfade stepped 0.30 -> 1.00 at i=M.

        Any wrap-adjacent sample-to-sample jump is a click. Walk the whole
        buffer, including the wrap from out[-1] back to out[0].
        """
        main = [(1.0, 1.0)] * 1000
        tail = [(0.3, 0.3)] * 400
        out = merge_stereo_frames(main, tail)
        ring = out + [out[0]]
        worst = max(
            abs(ring[i + 1][0] - ring[i][0]) for i in range(len(ring) - 1)
        )
        # 0.3 of tail spread over a 256-sample fade is ~0.0012/sample.
        self.assertLess(worst, 0.01, f"seam step of {worst:.3f} full-scale")

    def test_declick_fades_are_linear_not_equal_power(self) -> None:
        """Equal-power on a fade-to-silence lifts the middle ~3 dB.

        That bump is the 'tail gets loud at that part' symptom. Halfway through
        the fade a linear ramp must sit at ~0.5, not at 0.707.
        """
        main = [(0.0, 0.0)] * 1000
        tail = [(1.0, 1.0)] * 800
        d = DEFAULT_DECLICK_SAMPLES
        out = merge_stereo_frames(main, tail, declick_samples=d)
        self.assertAlmostEqual(out[d // 2][0], 0.5, places=2)
        self.assertGreater(out[d][0], 0.99)

    def test_tail_longer_than_the_loop_is_capped_at_one_pass(self) -> None:
        """A 15 s ring-out over a 2 s loop must not bake 7 stacked copies."""
        main = [(0.0, 0.0)] * 100
        tail = [(0.5, 0.5)] * 250
        out = merge_stereo_frames(main, tail, declick_samples=0)
        self.assertEqual(len(out), 100)
        # Exactly one pass of tail everywhere — never 2x or 3x stacked.
        for i, frame in enumerate(out):
            self.assertAlmostEqual(frame[0], 0.5, places=6, msg=f"at {i}")

    def test_offset_places_the_tail_later_in_the_head(self) -> None:
        main = [(0.0, 0.0)] * 1000
        tail = [(0.4, 0.4)] * 100
        out = merge_stereo_frames(
            main, tail, declick_samples=0, offset_samples=500
        )
        self.assertEqual(out[499], (0.0, 0.0))
        self.assertAlmostEqual(out[500][0], 0.4, places=6)
        self.assertEqual(out[600], (0.0, 0.0))

    def test_empty_tail_returns_the_take_unchanged(self) -> None:
        main = [(0.7, -0.7)] * 64
        self.assertEqual(merge_stereo_frames(main, []), main)

    def test_rejects_non_float_wav(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.wav"
            # PCM int16 stereo — format tag 1, not IEEE float.
            fmt = struct.pack("<HHIIHH", 1, 2, 48000, 48000 * 4, 4, 16)
            data = struct.pack("<hh", 0, 0)
            body = (
                b"RIFF"
                + struct.pack("<I", 36)
                + b"WAVEfmt "
                + struct.pack("<I", len(fmt))
                + fmt
                + b"data"
                + struct.pack("<I", len(data))
                + data
            )
            path.write_bytes(body)
            with self.assertRaises(ValueError):
                read_float32_stereo_wav(path)

    def test_roundtrip_float_wav(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "float.wav"
            frames = [(0.25, -0.25), (0.5, 0.5)]
            write_float32_stereo_wav(path, frames, sample_rate=48000)
            back, rate = read_float32_stereo_wav(path)
            self.assertEqual(rate, 48000)
            self.assertEqual(len(back), 2)
            self.assertAlmostEqual(back[0][0], 0.25, places=5)


if __name__ == "__main__":
    unittest.main()
