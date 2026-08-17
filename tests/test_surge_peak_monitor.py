"""Tests for live peak meter math and offline monitor behavior."""

from __future__ import annotations

import math
import unittest
from unittest.mock import MagicMock

from patch_browser.peak_meter_math import (
    PEAK_METER_FLOOR_DBFS,
    dbfs_to_meter_ratio,
    linear_peak_to_dbfs,
    peak_meter_color_dbfs,
)
from patch_browser.surge_peak_monitor import SurgePeakMonitor, _buffer_peak


class PeakMeterMathTests(unittest.TestCase):
    def test_silence_returns_none(self) -> None:
        self.assertIsNone(linear_peak_to_dbfs(0.0))

    def test_unity_peak_is_zero_dbfs(self) -> None:
        db = linear_peak_to_dbfs(1.0)
        assert db is not None
        self.assertAlmostEqual(db, 0.0, places=6)

    def test_half_peak_is_minus_six_db(self) -> None:
        db = linear_peak_to_dbfs(0.5)
        assert db is not None
        self.assertAlmostEqual(db, -6.0206, places=3)

    def test_floor_maps_to_zero_ratio(self) -> None:
        self.assertEqual(dbfs_to_meter_ratio(PEAK_METER_FLOOR_DBFS), 0.0)

    def test_zero_dbfs_maps_to_full_bar(self) -> None:
        self.assertEqual(dbfs_to_meter_ratio(0.0), 1.0)

    def test_color_buckets(self) -> None:
        self.assertEqual(peak_meter_color_dbfs(-24.0), "ok")
        self.assertEqual(peak_meter_color_dbfs(-12.0), "warn")
        self.assertEqual(peak_meter_color_dbfs(-3.0), "hot")


class BufferPeakTests(unittest.TestCase):
    def test_empty_buffer(self) -> None:
        self.assertEqual(_buffer_peak(b"", 0), 0.0)

    def test_finds_max_abs_sample(self) -> None:
        import struct

        samples = struct.pack("<fff", 0.1, -0.8, 0.3)
        self.assertAlmostEqual(_buffer_peak(samples, 3), 0.8)


class SurgePeakMonitorOfflineTests(unittest.TestCase):
    def test_offline_when_surge_unhealthy(self) -> None:
        surge = MagicMock()
        surge.check_health.return_value = (False, "down")
        monitor = SurgePeakMonitor(surge)
        monitor._poll_once()
        snap = monitor.snapshot()
        self.assertFalse(snap["online"])
        self.assertIsNone(snap["dbfs"])
        self.assertEqual(snap["source"], "none")


if __name__ == "__main__":
    unittest.main()
