"""Unit tests for Tier 3 offline seam merge."""

from __future__ import annotations

import struct
import tempfile
import unittest
import wave
from pathlib import Path

from scripts.sooperlooper.seam_merge import (
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
    def test_merge_blends_tail_into_loop_end(self) -> None:
        main = [(1.0, 1.0)] * 100 + [(0.0, 0.0)] * 20
        tail = [(0.5, 0.5)] * 20
        out = merge_stereo_frames(main, tail, merge_samples=16)
        self.assertEqual(len(out), len(main))
        self.assertAlmostEqual(out[-1][0], 0.5, places=3)
        self.assertAlmostEqual(out[99][0], 1.0, places=3)

    def test_merge_tail_at_seam_roundtrip_wav(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main_wav = root / "main.wav"
            tail_wav = root / "tail.wav"
            out_wav = root / "out.wav"
            _write_sine(main_wav, frames=4800, freq=440.0, amp=0.5)
            _write_sine(tail_wav, frames=480, freq=880.0, amp=0.25)
            merge_tail_at_seam(main_wav, tail_wav, out_wav, merge_samples=128)
            merged, rate = read_float32_stereo_wav(out_wav)
            self.assertEqual(rate, 48000)
            self.assertEqual(len(merged), 4800)
            self.assertGreater(abs(merged[-1][0]), 0.1)

    def test_rejects_non_float_wav(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.wav"
            with wave.open(str(path), "wb") as wf:
                wf.setnchannels(2)
                wf.setsampwidth(2)
                wf.setframerate(48000)
                wf.writeframes(struct.pack("<hh", 0, 0))
            with self.assertRaises(ValueError):
                read_float32_stereo_wav(path)


if __name__ == "__main__":
    unittest.main()
