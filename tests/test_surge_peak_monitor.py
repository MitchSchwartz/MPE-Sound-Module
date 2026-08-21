"""Tests for live peak meter math and compiled-meter reader behavior."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from patch_browser.peak_meter_math import (
    PEAK_METER_CLIP_DBFS,
    PEAK_METER_FLOOR_DBFS,
    PEAK_METER_ORANGE_DBFS,
    PEAK_METER_RED_DBFS,
    PEAK_METER_YELLOW_DBFS,
    dbfs_to_meter_ratio,
    linear_peak_to_dbfs,
    peak_meter_color_dbfs,
)
from patch_browser.surge_peak_monitor import (
    PEAK_METER_ENV,
    SurgePeakMonitor,
    peak_meter_enabled,
)


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

    def test_clip_dbfs_maps_to_full_bar(self) -> None:
        self.assertEqual(dbfs_to_meter_ratio(PEAK_METER_CLIP_DBFS), 1.0)

    def test_color_buckets(self) -> None:
        self.assertEqual(peak_meter_color_dbfs(PEAK_METER_YELLOW_DBFS - 0.1), "ok")
        self.assertEqual(peak_meter_color_dbfs(PEAK_METER_YELLOW_DBFS), "warn")
        self.assertEqual(peak_meter_color_dbfs(PEAK_METER_ORANGE_DBFS - 0.1), "warn")
        self.assertEqual(peak_meter_color_dbfs(PEAK_METER_ORANGE_DBFS), "orange")
        self.assertEqual(peak_meter_color_dbfs(PEAK_METER_RED_DBFS - 0.1), "orange")
        self.assertEqual(peak_meter_color_dbfs(PEAK_METER_RED_DBFS), "hot")
        self.assertEqual(peak_meter_color_dbfs(PEAK_METER_CLIP_DBFS), "hot")


def _healthy_surge() -> MagicMock:
    surge = MagicMock()
    surge.check_health.return_value = (True, None)
    return surge


class PeakMeterEnableTests(unittest.TestCase):
    def test_disabled_by_default(self) -> None:
        with patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop(PEAK_METER_ENV, None)
            self.assertFalse(peak_meter_enabled())

    def test_enabled_by_env(self) -> None:
        with patch.dict("os.environ", {PEAK_METER_ENV: "1"}):
            self.assertTrue(peak_meter_enabled())

    def test_offline_when_meter_disabled(self) -> None:
        monitor = SurgePeakMonitor(_healthy_surge())
        with patch.dict("os.environ", {PEAK_METER_ENV: "0"}):
            monitor._poll_once()
        snap = monitor.snapshot()
        self.assertFalse(snap["online"])
        self.assertIsNone(snap["dbfs"])


class SurgePeakMonitorStateFileTests(unittest.TestCase):
    def test_reads_compiled_meter_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "meter.state"
            state_path.write_text(
                "peak_linear=0.5\nwired=1\nonline=1\nsource=jack\n",
                encoding="utf-8",
            )
            monitor = SurgePeakMonitor(_healthy_surge(), state_path=state_path)
            with patch.dict("os.environ", {PEAK_METER_ENV: "1"}):
                monitor._poll_once()
            snap = monitor.snapshot()
            self.assertTrue(snap["online"])
            self.assertAlmostEqual(snap["peak_linear"], 0.5)
            self.assertEqual(snap["source"], "jack")

    def test_offline_when_surge_unhealthy(self) -> None:
        surge = MagicMock()
        surge.check_health.return_value = (False, "down")
        monitor = SurgePeakMonitor(surge)
        monitor._poll_once()
        snap = monitor.snapshot()
        self.assertFalse(snap["online"])
        self.assertIsNone(snap["dbfs"])
        self.assertEqual(snap["source"], "none")

    def test_unwired_state_reads_offline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "meter.state"
            state_path.write_text("peak_linear=0.1\nwired=0\nonline=0\n", encoding="utf-8")
            monitor = SurgePeakMonitor(_healthy_surge(), state_path=state_path)
            with patch.dict("os.environ", {PEAK_METER_ENV: "1"}):
                monitor._poll_once()
            snap = monitor.snapshot()
            self.assertFalse(snap["online"])



    def test_uses_file_peak_without_extra_decay(self) -> None:
        """Compiled meter owns hold/decay — UI must not decay again."""
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "meter.state"
            state_path.write_text("peak_linear=0.4\nwired=1\nsource=jack\n", encoding="utf-8")
            monitor = SurgePeakMonitor(_healthy_surge(), state_path=state_path)
            with patch.dict("os.environ", {PEAK_METER_ENV: "1"}):
                monitor._poll_once()
            self.assertAlmostEqual(monitor.snapshot()["peak_linear"], 0.4)
            state_path.write_text("peak_linear=0.2\nwired=1\nsource=jack\n", encoding="utf-8")
            with patch.dict("os.environ", {PEAK_METER_ENV: "1"}):
                monitor._poll_once()
            self.assertAlmostEqual(monitor.snapshot()["peak_linear"], 0.2)

if __name__ == "__main__":
    unittest.main()
