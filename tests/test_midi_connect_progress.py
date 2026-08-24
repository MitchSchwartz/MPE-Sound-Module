"""Tests for ROLI hot-plug connecting state."""

from __future__ import annotations

import time
import unittest
from unittest import mock

from patch_browser import midi_connect_progress


class MidiConnectProgressTests(unittest.TestCase):
    def test_not_connecting_when_file_missing(self) -> None:
        with mock.patch.object(midi_connect_progress, "CONNECT_STATE_PATH") as path:
            path.is_file.return_value = False
            self.assertFalse(midi_connect_progress.is_connecting())
            self.assertIsNone(midi_connect_progress.connecting_toast())

    def test_connecting_when_file_fresh(self) -> None:
        with mock.patch.object(midi_connect_progress, "CONNECT_STATE_PATH") as path:
            path.is_file.return_value = True
            path.read_text.return_value = f"connecting {int(time.time())}\n"
            self.assertTrue(midi_connect_progress.is_connecting())
            self.assertEqual(midi_connect_progress.connecting_toast_base(), "Connecting keyboard")
            self.assertEqual(midi_connect_progress.connecting_toast(), "Connecting keyboard…")

    def test_stale_connecting_ignored(self) -> None:
        with mock.patch.object(midi_connect_progress, "CONNECT_STATE_PATH") as path:
            path.is_file.return_value = True
            path.read_text.return_value = "connecting 1\n"
            self.assertFalse(midi_connect_progress.is_connecting())

    def test_disconnecting_blocks_audio_toast(self) -> None:
        with mock.patch.object(midi_connect_progress, "CONNECT_STATE_PATH") as path:
            path.is_file.return_value = True
            path.read_text.return_value = f"disconnecting {int(time.time())}\n"
            self.assertTrue(midi_connect_progress.is_disconnecting())
            self.assertTrue(midi_connect_progress.blocks_audio_recovery_toast())
            self.assertIsNone(midi_connect_progress.connecting_toast_base())


if __name__ == "__main__":
    unittest.main()
