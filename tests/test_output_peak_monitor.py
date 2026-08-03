"""Tests for ALSA output peak monitor helpers."""

from __future__ import annotations

import struct
import unittest

from patch_browser.output_peak_monitor import OutputPeakMonitor, linear_to_dbtp


class OutputPeakMonitorTests(unittest.TestCase):
    def test_peak_from_pcm_full_scale(self) -> None:
        data = struct.pack("<hh", 32767, -3000)
        peak = OutputPeakMonitor._peak_from_pcm(data)
        self.assertAlmostEqual(peak, 1.0, places=3)

    def test_linear_to_dbtp(self) -> None:
        self.assertAlmostEqual(linear_to_dbtp(1.0), 0.0, places=2)
        self.assertLess(linear_to_dbtp(0.1), -18.0)


if __name__ == "__main__":
    unittest.main()
