"""Tests for Surge audio buffer / sample rate helpers."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from patch_browser import surge_audio


class SurgeAudioTests(unittest.TestCase):
    def test_buffer_presets_cycle(self) -> None:
        self.assertEqual(surge_audio.next_buffer_preset(512), 1024)
        self.assertEqual(surge_audio.next_buffer_preset(1024), 64)

    def test_jack_period_presets(self) -> None:
        self.assertNotIn(768, surge_audio.JACK_PERIOD_PRESETS)
        self.assertNotIn(32, surge_audio.JACK_PERIOD_PRESETS)

    def test_sample_rate_toggles(self) -> None:
        self.assertEqual(surge_audio.next_sample_rate(48000), 44100)
        self.assertEqual(surge_audio.next_sample_rate(44100), 48000)

    def test_buffer_latency_ms(self) -> None:
        self.assertAlmostEqual(surge_audio.buffer_latency_ms(768, 48000), 16.0)

    def test_read_int_from_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mpe.env"
            path.write_text("MPE_SURGE_BUFFER_SIZE=512\nMPE_SURGE_SAMPLE_RATE=48000\n", encoding="utf-8")
            self.assertEqual(surge_audio.read_int_from_env_file("MPE_SURGE_BUFFER_SIZE", path), 512)
            self.assertEqual(surge_audio.read_int_from_env_file("MPE_SURGE_SAMPLE_RATE", path), 48000)

    @mock.patch.dict(
        os.environ,
        {
            "MPE_JACK_BUFFER": "256",
            "MPE_JACK_PERIODS": "3",
            "MPE_SURGE_SAMPLE_RATE": "48000",
        },
        clear=False,
    )
    def test_settings_labels(self) -> None:
        self.assertIn("256 × 3", surge_audio.buffer_settings_label())
        self.assertIn("16 ms", surge_audio.buffer_settings_label())
        self.assertIn("kHz", surge_audio.sample_rate_settings_label())

    def test_graph_latency_ms(self) -> None:
        self.assertAlmostEqual(surge_audio.graph_latency_ms(256, 3, 48000), 16.0)

    def test_option_labels(self) -> None:
        self.assertEqual(surge_audio.buffer_option_label(768, 48000), "768 · 16 ms")
        self.assertEqual(surge_audio.sample_rate_option_label(44100), "44.1 kHz")

    @mock.patch("patch_browser.surge_audio.subprocess.run")
    def test_apply_buffer_rejects_non_jack_period(self, run_mock: mock.Mock) -> None:
        ok, message = surge_audio.apply_buffer(768)
        self.assertFalse(ok)
        self.assertIn("768", message)
        run_mock.assert_not_called()

    @mock.patch("patch_browser.surge_audio.subprocess.run")
    def test_apply_buffer_success(self, run_mock: mock.Mock) -> None:
        run_mock.return_value = mock.Mock(returncode=0, stdout="", stderr="")
        ok, message = surge_audio.apply_buffer(512)
        self.assertTrue(ok)
        self.assertIn("512", message)
        self.assertEqual(run_mock.call_args.args[0][3], "512")


if __name__ == "__main__":
    unittest.main()
