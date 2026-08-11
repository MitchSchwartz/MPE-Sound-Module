"""Tests for ~/.mpe_looper_timing.json read/write."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from patch_browser.looper_timing_state import (
    clear_timing_state,
    read_timing_state,
    write_timing_state,
)


class LooperTimingStateTests(unittest.TestCase):
    def test_write_and_read_active_transport(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "timing.json"
            now = time.monotonic()
            write_timing_state(
                active=True,
                bpm=120.0,
                beat_in_bar=2,
                beats_per_bar=4,
                bar_in_loop=2,
                bars_per_loop=4,
                beat_index=5,
                tick_in_bar=3,
                eighth_index=11,
                total_frames=120000,
                frames_per_beat=24000,
                path=path,
            )
            snap = read_timing_state(path=path, now=now)
            self.assertTrue(snap["active"])
            self.assertEqual(snap["bar_in_loop"], 2)
            self.assertEqual(snap["bars_per_loop"], 4)
            self.assertEqual(snap["tick_in_bar"], 3)
            self.assertEqual(snap["total_frames"], 120000)
            self.assertEqual(snap["frames_per_beat"], 24000)

    def test_clear_resets_bar_counter_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "timing.json"
            now = time.monotonic()
            write_timing_state(
                active=True,
                bpm=120.0,
                bar_in_loop=3,
                bars_per_loop=4,
                tick_in_bar=7,
                total_frames=999999,
                frames_per_beat=24000,
                path=path,
            )
            clear_timing_state(path=path)
            snap = read_timing_state(path=path, stale_after_s=999.0, now=now)
            self.assertFalse(snap["active"])
            self.assertIsNone(snap["bar_in_loop"])
            self.assertEqual(snap["tick_in_bar"], 0)
            self.assertEqual(snap["total_frames"], 0)
            self.assertIsNone(snap["frames_per_beat"])

    def test_stale_file_reads_inactive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "timing.json"
            write_timing_state(
                active=True,
                bar_in_loop=2,
                bars_per_loop=4,
                path=path,
            )
            snap = read_timing_state(path=path, stale_after_s=0.001, now=time.monotonic() + 1.0)
            self.assertFalse(snap["active"])
            self.assertIsNone(snap["bar_in_loop"])


if __name__ == "__main__":
    unittest.main()
