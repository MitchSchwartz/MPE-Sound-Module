"""Tests for patch_browser/usb_audio_recovery.py."""

from __future__ import annotations

import time
import unittest
from pathlib import Path
from unittest import mock

from patch_browser import usb_audio_recovery as recovery


class UsbAudioRecoveryTests(unittest.TestCase):
    def test_not_recovering_when_file_missing(self) -> None:
        with mock.patch.object(recovery, "RECOVERY_STATE_PATH", Path("/nonexistent/state")):
            self.assertFalse(recovery.is_recovering())
            self.assertIsNone(recovery.status_subtitle())

    def test_recovering_when_file_fresh(self) -> None:
        with mock.patch.object(recovery, "RECOVERY_STATE_PATH") as path:
            path.is_file.return_value = True
            path.read_text.return_value = f"recovering {int(time.time())}\n"
            self.assertTrue(recovery.is_recovering())
            self.assertEqual(recovery.status_subtitle(), "Recovering USB audio for DAW…")

    def test_stale_flag_ignored(self) -> None:
        with mock.patch.object(recovery, "RECOVERY_STATE_PATH") as path:
            path.is_file.return_value = True
            path.read_text.return_value = "recovering 1\n"
            self.assertFalse(recovery.is_recovering())


if __name__ == "__main__":
    unittest.main()
