"""Tests for looper ring buffer and mix helpers."""

from __future__ import annotations

import contextlib
import platform
import statistics
import struct
import time
import unittest
from unittest import mock

from patch_browser import looper_engine
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


def _force_audioop_path():
    """Exercise the compiled ``audioop`` mixer.

    Path selection is ``audioop is not None`` — ``_AUDIOOP_BACKEND`` is reporting
    only — so there is nothing to patch here beyond confirming the module loaded.
    Parity tests skip without it; ``test_compiled_audioop_backend_available`` is
    the one that fails, so a missing backend surfaces once and unambiguously.
    """
    if looper_engine.audioop is None:
        raise unittest.SkipTest(
            "no audioop backend — see test_compiled_audioop_backend_available"
        )
    return contextlib.nullcontext()


def _force_python_path():
    """Exercise the pure-Python fallback mixer."""
    return mock.patch.object(looper_engine, "audioop", None)


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

    def test_apply_gain_half_scale_audioop_path(self) -> None:
        with _force_audioop_path():
            out = apply_gain_s16_stereo(_stereo_frame(16000, -16000) * 4, 0.5)
        left, right = struct.unpack("<hh", out[:4])
        self.assertAlmostEqual(left, 8000, delta=2)
        self.assertAlmostEqual(right, -8000, delta=2)

    def test_apply_gain_half_scale_python_path(self) -> None:
        with _force_python_path():
            out = apply_gain_s16_stereo(_stereo_frame(16000, -16000) * 4, 0.5)
        left, right = struct.unpack("<hh", out[:4])
        self.assertAlmostEqual(left, 8000, delta=2)
        self.assertAlmostEqual(right, -8000, delta=2)

    def test_mix_live_and_loops_backends_agree_three_layers(self) -> None:
        live = b"".join(_stereo_frame(9000, -9000) for _ in range(8))
        loops = [b"".join(_stereo_frame(v, -v) for _ in range(8)) for v in (12000, -7000, 21000)]

        with _force_python_path():
            expected = mix_live_and_loops(live, loops, live_gain=1.0, loop_gain=1.0)
        with _force_audioop_path():
            actual = mix_live_and_loops(live, loops, live_gain=1.0, loop_gain=1.0)

        self.assertEqual(len(actual), len(expected))
        for offset in range(0, len(expected), 2):
            self.assertAlmostEqual(
                struct.unpack_from("<h", actual, offset)[0],
                struct.unpack_from("<h", expected, offset)[0],
                delta=3,
                msg=f"sample at byte {offset}",
            )

    def test_mix_live_and_loops_layers_stay_under_ceiling(self) -> None:
        # Three hot layers pre-scaled by loop_gain/N must sum near the bus ceiling,
        # not saturate — including on intermediate sums, since audioop.add clips.
        layer = b"".join(_stereo_frame(30000, 30000) for _ in range(4))
        silence = b"\x00" * len(layer)
        for name, force in (("python", _force_python_path), ("audioop", _force_audioop_path)):
            with self.subTest(path=name), force():
                mixed = mix_live_and_loops(silence, [layer] * 3, live_gain=0.0, loop_gain=1.0)
                left, right = struct.unpack("<hh", mixed[:4])
                self.assertLess(left, 32767, "loop bus saturated")
                self.assertAlmostEqual(left, 30000, delta=3)
                self.assertAlmostEqual(right, 30000, delta=3)

    def test_mix_live_and_loops_two_layers(self) -> None:
        live = b"".join(_stereo_frame(100, 100) for _ in range(4))
        loop_a = b"".join(_stereo_frame(200, 0) for _ in range(4))
        loop_b = b"".join(_stereo_frame(0, 200) for _ in range(4))
        mixed = mix_live_and_loops(live, [loop_a, loop_b], live_gain=1.0, loop_gain=1.0)
        self.assertEqual(len(mixed), len(live))
        left, right = struct.unpack("<hh", mixed[:4])
        self.assertEqual(left, 100)
        self.assertEqual(right, 100)

    def test_mix_live_and_loops_playback_only_ignores_live(self) -> None:
        live = b"".join(_stereo_frame(1000, 1000) for _ in range(4))
        loop = b"".join(_stereo_frame(200, 100) for _ in range(4))
        mixed = mix_live_and_loops(live, [loop], live_gain=0.0, loop_gain=1.0)
        self.assertEqual(mixed, loop)

    def test_audio_mix_backend_reported(self) -> None:
        from patch_browser.looper_engine import audio_mix_backend

        self.assertIn(audio_mix_backend(), ("stdlib", "lts", "python"))

    def test_compiled_audioop_backend_available(self) -> None:
        """On the appliance a missing backend must FAIL, never skip.

        The interpreted fallback costs 13-14.5 ms against a 10.67 ms period on the
        Pi and this project shipped it unnoticed for its entire life. requirements.txt
        pins audioop-lts, so on the appliance its absence is a broken environment.

        Elsewhere this skips: a dev laptop is fast enough that even the pure-Python
        mixer stays under budget, so MixerPerformanceTests cannot detect the
        fallback there. Run `mpe test pi looper` for the meaningful check.
        """
        from patch_browser.looper_engine import audio_mix_backend

        if looper_engine.audioop is None:
            if platform.machine() == "aarch64":
                self.fail(
                    "no compiled audioop backend on the appliance — the mixer is "
                    "running the per-frame Python path (13-14.5 ms per 10.67 ms "
                    "period). Install it: pip3 install -r requirements.txt"
                )
            raise unittest.SkipTest(
                "no audioop backend on this host — mixer performance is unvalidated "
                "here; run `mpe test pi looper`. Install audioop-lts for full coverage."
            )
        self.assertIn(audio_mix_backend(), ("stdlib", "lts"))

    def test_frames_to_bytes_roundtrip(self) -> None:
        self.assertEqual(bytes_to_frames(frames_to_bytes(128)), 128)


class MixerPerformanceTests(unittest.TestCase):
    """Guard the mixer against silently falling back to the per-frame Python path.

    A 512-frame period at 48 kHz must be produced in under 10.67 ms or the DAC
    starves. The compiled path costs a fraction of that; the pure-Python
    fallback measured 13-14.5 ms on the Pi, which is why it crackled.
    """

    PERIOD_FRAMES = 512
    BUDGET_MS = PERIOD_FRAMES * 1000.0 / 48000
    # Half the period. Generous enough not to flake on a loaded shared runner,
    # tight enough that the interpreted fallback cannot pass.
    LIMIT_MS = BUDGET_MS / 2

    def _period(self, seed: int) -> bytes:
        return b"".join(
            _stereo_frame((seed * 137 + i * 31) % 20000 - 10000, (seed * 71 + i * 17) % 20000 - 10000)
            for i in range(self.PERIOD_FRAMES)
        )

    def _measure_ms(self, layers: int) -> float:
        live = self._period(0)
        loops = [self._period(n + 1) for n in range(layers)]
        for _ in range(5):  # warm up
            mix_live_and_loops(live, loops, live_gain=1.0, loop_gain=1.0)
        samples = []
        for _ in range(30):
            start = time.perf_counter()
            mix_live_and_loops(live, loops, live_gain=1.0, loop_gain=1.0)
            samples.append((time.perf_counter() - start) * 1000.0)
        return statistics.median(samples)

    def test_mix_meets_period_budget_up_to_six_layers(self) -> None:
        # Deliberately unguarded: measure whatever path the environment actually
        # takes. If the compiled backend is missing the mix runs ~14 ms and this
        # fails, which is the regression this test exists to catch. Skipping here
        # would turn that exact failure green.
        measured = {n: self._measure_ms(n) for n in (1, 3, 6)}
        report = " ".join(f"{n}layer={ms:.3f}ms" for n, ms in measured.items())
        print(f"[perf] mixer budget={self.BUDGET_MS:.2f}ms limit={self.LIMIT_MS:.2f}ms {report}")
        for layers, ms in measured.items():
            self.assertLess(
                ms,
                self.LIMIT_MS,
                f"{layers}-layer mix took {ms:.3f}ms of a {self.BUDGET_MS:.2f}ms period "
                "— the compiled audioop path is not being used",
            )


if __name__ == "__main__":
    unittest.main()
