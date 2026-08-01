"""Calibration loader cancel/progress messaging."""

from __future__ import annotations

import sys
import unittest
from unittest import mock

if "pygame" not in sys.modules:
    sys.modules["pygame"] = mock.MagicMock()

from patch_browser.calibration_loader import format_cancel_message


class CalibrationLoaderMessageTests(unittest.TestCase):
    def test_cancel_with_saved_and_attempted(self) -> None:
        msg = format_cancel_message(saved=42, attempted=1200)
        self.assertIn("42 calibration(s) saved before cancel", msg)
        self.assertNotIn("attempted", msg)

    def test_cancel_when_all_attempts_saved(self) -> None:
        msg = format_cancel_message(saved=3, attempted=3)
        self.assertEqual(msg, "Cancelled — 3 patch(es) saved")
        self.assertNotIn("attempted", msg)

    def test_cancel_with_attempts_but_no_saves(self) -> None:
        msg = format_cancel_message(saved=0, attempted=1200)
        self.assertIn("saved 0 calibrations", msg)
        self.assertIn("none measured successfully", msg)

    def test_cancel_before_first_patch(self) -> None:
        msg = format_cancel_message(saved=0, attempted=0)
        self.assertEqual(msg, "Cancelled — saved 0 calibrations")


if __name__ == "__main__":
    unittest.main()
