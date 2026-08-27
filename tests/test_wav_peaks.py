"""Peak analysis for the audio-path check (2026-08-26).

The decision this makes — "did host audio arrive inside the interface?" — was
the one question nothing on the appliance could answer while a Scarlett in
standalone mode discarded every sample for hours. It is tested here without an
interface, a tone, or a person to listen.
"""

from __future__ import annotations

import math
import struct
import sys
import tempfile
import unittest
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "lib"))

import wav_peaks  # noqa: E402


def _write(path: str, channels: int, frames: list[list[int]], width: int = 4) -> None:
    with wave.open(path, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(48000)
        fmt = {1: "<b", 2: "<h", 4: "<i"}[width]
        w.writeframes(b"".join(struct.pack(fmt, s) for fr in frames for s in fr))


class TestPeaks(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.path = str(Path(self.dir.name) / "t.wav")

    def tearDown(self) -> None:
        self.dir.cleanup()

    def test_silence_is_exactly_zero(self) -> None:
        """The failing case that mattered: a digitally silent channel."""
        _write(self.path, 2, [[0, 0]] * 100)
        self.assertEqual(wav_peaks.peaks(self.path), [0, 0])
        self.assertEqual(wav_peaks.peaks_fs(self.path), [0.0, 0.0])

    def test_per_channel_independence(self) -> None:
        """One live channel beside a dead one must not mask it — the real
        capture had signal on ch5/6 while ch1-4 were silent."""
        _write(self.path, 3, [[0, 1 << 20, -(1 << 24)]] * 50)
        self.assertEqual(wav_peaks.peaks(self.path), [0, 1 << 20, 1 << 24])

    def test_negative_peaks_counted(self) -> None:
        _write(self.path, 1, [[-500], [10]])
        self.assertEqual(wav_peaks.peaks(self.path), [500])

    def test_full_scale_fraction(self) -> None:
        _write(self.path, 1, [[1 << 30]])
        self.assertAlmostEqual(wav_peaks.peaks_fs(self.path)[0], 0.5, places=6)

    def test_16_bit_width(self) -> None:
        _write(self.path, 1, [[16384]], width=2)
        self.assertAlmostEqual(wav_peaks.peaks_fs(self.path)[0], 0.5, places=6)

    def test_truncated_frame_does_not_crash(self) -> None:
        """A capture cut short mid-frame must report, not raise."""
        _write(self.path, 2, [[1000, 1000]] * 10)
        raw = Path(self.path).read_bytes()
        Path(self.path).write_bytes(raw[:-3])
        self.assertEqual(len(wav_peaks.peaks(self.path)), 2)


class TestSignalThreshold(unittest.TestCase):
    def test_digital_silence_is_not_signal(self) -> None:
        self.assertFalse(wav_peaks.has_signal(0.0))

    def test_capture_noise_floor_is_not_signal(self) -> None:
        """Measured idle analogue inputs on the Scarlett: ~0.006% FS. That must
        read as silence, or the check passes on a dead interface."""
        self.assertFalse(wav_peaks.has_signal(0.00006))

    def test_real_programme_is_signal(self) -> None:
        for level in (0.01, 0.1, 0.5, 1.0):
            self.assertTrue(wav_peaks.has_signal(level), level)

    def test_threshold_sits_above_the_measured_noise_floor(self) -> None:
        self.assertGreater(wav_peaks.SIGNAL_THRESHOLD_FS, 0.00006 * 5)


class TestCli(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.path = str(Path(self.dir.name) / "t.wav")

    def tearDown(self) -> None:
        self.dir.cleanup()

    def test_exit_1_when_a_checked_channel_is_silent(self) -> None:
        _write(self.path, 4, [[1 << 28, 0, 0, 0]] * 20)
        self.assertEqual(wav_peaks.main(["x", self.path, "1", "2"]), 1)

    def test_exit_0_when_all_checked_channels_have_signal(self) -> None:
        _write(self.path, 4, [[1 << 28] * 4] * 20)
        self.assertEqual(wav_peaks.main(["x", self.path, "1", "2", "3", "4"]), 0)

    def test_unchecked_channels_are_ignored(self) -> None:
        """Only the PCM channels decide the verdict; analogue-input noise on
        other channels must not rescue a failing check."""
        _write(self.path, 6, [[0, 0, 0, 0, 1 << 28, 1 << 28]] * 20)
        self.assertEqual(wav_peaks.main(["x", self.path, "1", "2", "3", "4"]), 1)
        self.assertEqual(wav_peaks.main(["x", self.path, "5", "6"]), 0)

    def test_missing_argument(self) -> None:
        self.assertEqual(wav_peaks.main(["x"]), 2)


if __name__ == "__main__":
    unittest.main()
