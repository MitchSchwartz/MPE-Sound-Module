"""Tests for looper ring buffer and mix helpers."""

from __future__ import annotations

import struct
import unittest

from patch_browser.looper_engine import (
    StereoRingBuffer,
    apply_gain_s16_stereo,
    bytes_to_frames,
    frames_to_bytes,
    loop_length_frames,
    mix_live_and_loops,
    mix_s16_stereo,
    quantize_loop_frames,
)


def _stereo_frame(left: int, right: int) -> bytes:
    return struct.pack("<hh", left, right)


class LooperEngineTests(unittest.TestCase):
    def test_loop_length_frames_four_bars_120bpm(self) -> None:
        # 4 bars * 4 beats * 60/120 = 8.0 s @ 48 kHz
        self.assertEqual(loop_length_frames(bars=4, bpm=120, sample_rate=48000), 384_000)

    def test_ring_write_and_read(self) -> None:
        ring = StereoRingBuffer(4)
        a = _stereo_frame(100, -100) * 2
        self.assertEqual(ring.write_frames(a), 2)
        self.assertEqual(ring.filled_frames, 2)
        out = ring.read_frames(0, 2)
        self.assertEqual(out, a)

    def test_ring_wrap_read(self) -> None:
        ring = StereoRingBuffer(3)
        frames = b"".join(_stereo_frame(i, i) for i in (1, 2, 3))
        ring.write_frames(frames)
        wrapped = ring.read_frames(2, 2)
        self.assertEqual(bytes_to_frames(len(wrapped)), 2)
        self.assertEqual(wrapped[:4], _stereo_frame(3, 3))
        self.assertEqual(wrapped[4:], _stereo_frame(1, 1))

    def test_quantize_loop_frames_rounds_up_to_bar(self) -> None:
        fpb = 96000
        self.assertEqual(
            quantize_loop_frames(1, frames_per_bar=fpb, capacity_frames=384_000),
            fpb,
        )
        self.assertEqual(
            quantize_loop_frames(fpb // 2, frames_per_bar=fpb, capacity_frames=384_000),
            fpb,
        )
        self.assertEqual(
            quantize_loop_frames(fpb * 2, frames_per_bar=fpb, capacity_frames=384_000),
            fpb * 2,
        )

    def test_read_frames_for_loop_partial_clip(self) -> None:
        ring = StereoRingBuffer(8)
        ring.write_frames(_stereo_frame(1, 1) * 3)
        out = ring.read_frames_for_loop(2, 4, loop_frames=4)
        self.assertEqual(out[:4], _stereo_frame(1, 1))
        self.assertEqual(out[4:8], b"\x00\x00\x00\x00")
        self.assertEqual(out[8:12], _stereo_frame(1, 1))
        self.assertEqual(out[12:16], _stereo_frame(1, 1))

    def test_mix_s16_stereo_clips(self) -> None:
        hot = _stereo_frame(30000, 30000)
        mixed = mix_s16_stereo(hot, hot, gains=(1.0, 1.0))
        left, right = struct.unpack("<hh", mixed)
        self.assertEqual(left, 32767)
        self.assertEqual(right, 32767)

    def test_apply_gain_identity(self) -> None:
        pcm = _stereo_frame(500, -500) * 2
        self.assertEqual(apply_gain_s16_stereo(pcm, 1.0), pcm)

    def test_mix_live_and_loops_two_layers(self) -> None:
        live = b"".join(_stereo_frame(100, 100) for _ in range(4))
        loop_a = b"".join(_stereo_frame(200, 0) for _ in range(4))
        loop_b = b"".join(_stereo_frame(0, 200) for _ in range(4))
        mixed = mix_live_and_loops(live, [loop_a, loop_b], live_gain=1.0, loop_gain=1.0)
        self.assertEqual(len(mixed), len(live))
        left, right = struct.unpack("<hh", mixed[:4])
        # Bus headroom: per-layer 0.5, loops + live peak at 200, × 1/2 headroom
        self.assertEqual(left, 100)
        self.assertEqual(right, 100)

    def test_mix_live_and_loops_three_layers_stays_below_clip(self) -> None:
        hot = _stereo_frame(28000, 28000)
        period = hot * 4
        live = period
        loops = [period, period, period]
        mixed = mix_live_and_loops(live, loops, live_gain=1.0, loop_gain=0.85)
        for i in range(4):
            left, right = struct.unpack("<hh", mixed[i * 4 : (i + 1) * 4])
            self.assertLessEqual(abs(left), 32767)
            self.assertLessEqual(abs(right), 32767)
            self.assertLess(abs(left), 28000)

    def test_frames_to_bytes_roundtrip(self) -> None:
        self.assertEqual(bytes_to_frames(frames_to_bytes(128)), 128)


if __name__ == "__main__":
    unittest.main()
