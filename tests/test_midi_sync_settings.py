"""Tests for looper sync settings persistence helpers."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from patch_browser import midi_sync_settings as mss


class TestMidiSyncSettings(unittest.TestCase):
    def test_current_quantize_defaults_off(self) -> None:
        with mock.patch.object(mss, "read_str_from_env_file", return_value=None):
            with mock.patch.dict(os.environ, {}, clear=True):
                self.assertEqual(mss.current_quantize(), "off")

    def test_settings_summary(self) -> None:
        with mock.patch.object(mss, "current_quantize", return_value="16th"):
            with mock.patch.object(mss, "current_triplet", return_value=False):
                with mock.patch.object(mss, "current_offset_auto", return_value=True):
                    with mock.patch.object(mss, "offset_summary", return_value="Auto (−21 ms)"):
                        self.assertEqual(
                            mss.settings_summary(),
                            "Quantize: 16th note · Auto (−21 ms)",
                        )

    def test_settings_summary_triplet(self) -> None:
        with mock.patch.object(mss, "current_quantize", return_value="8th"):
            with mock.patch.object(mss, "current_triplet", return_value=True):
                with mock.patch.object(mss, "current_offset_auto", return_value=True):
                    with mock.patch.object(mss, "offset_summary", return_value="Auto (−21 ms)"):
                        self.assertIn("triplet", mss.settings_summary())

    def test_read_str_from_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mpe.env"
            path.write_text("MPE_MIDI_QUANTIZE=16th\nMPE_MIDI_OUTPUT_OFFSET_AUTO=1\n")
            self.assertEqual(mss.read_str_from_env_file("MPE_MIDI_QUANTIZE", path), "16th")
            self.assertEqual(mss.read_str_from_env_file("MPE_MIDI_MISSING", path), None)


if __name__ == "__main__":
    unittest.main()
