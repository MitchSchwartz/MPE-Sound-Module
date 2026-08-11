"""Tests for touch header looper HUD merge + visibility."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from patch_browser.looper_hud import (
    looper_hud_bar_fraction,
    looper_hud_eighth_index,
    looper_hud_is_visible,
    looper_hud_min_width_px,
    looper_hud_segment_halves,
    looper_hud_tick_in_bar,
    merge_looper_hud_snapshot,
)
from patch_browser.looper_timing_state import write_timing_state


class LooperHudTests(unittest.TestCase):
    FPB = 24000

    def test_merge_internal_timing_sets_looper_active(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "timing.json"
            write_timing_state(
                active=True,
                bpm=120.0,
                beat_in_bar=2,
                bar_in_loop=1,
                bars_per_loop=4,
                beat_index=1,
                tick_in_bar=2,
                eighth_index=2,
                path=path,
            )
            with patch("patch_browser.looper_hud.read_timing_state") as read_mock:
                read_mock.return_value = {
                    "active": True,
                    "online": True,
                    "bpm": 120.0,
                    "beat_in_bar": 2,
                    "beats_per_bar": 4,
                    "bar_in_loop": 1,
                    "bars_per_loop": 4,
                }
                merged = merge_looper_hud_snapshot({"connected": False, "bpm": None})
            self.assertTrue(merged["looper_active"])

    def test_bar_fraction_from_internal_timing(self) -> None:
        snap = {
            "internal_timing": {
                "active": True,
                "bar_in_loop": 2,
                "bars_per_loop": 4,
            }
        }
        self.assertEqual(looper_hud_bar_fraction(snap), "2/4")

    def test_eighth_ticks_and_halves(self) -> None:
        fpb = self.FPB
        fpbar = fpb * 4
        self.assertEqual(
            looper_hud_tick_in_bar(total_frames=0, frames_per_beat=fpb, beats_per_bar=4),
            0,
        )
        self.assertEqual(looper_hud_segment_halves(tick_in_bar=0), [0, 0, 0, 0])
        self.assertEqual(looper_hud_segment_halves(tick_in_bar=1), [1, 0, 0, 0])
        self.assertEqual(looper_hud_segment_halves(tick_in_bar=2), [2, 0, 0, 0])
        self.assertEqual(looper_hud_segment_halves(tick_in_bar=3), [2, 1, 0, 0])
        self.assertEqual(
            looper_hud_segment_halves(
                tick_in_bar=looper_hud_tick_in_bar(
                    total_frames=fpbar - 1,
                    frames_per_beat=fpb,
                    beats_per_bar=4,
                )
            ),
            [2, 2, 2, 1],
        )

    def test_eighth_index_increases_on_loop_wrap(self) -> None:
        fpb = self.FPB
        fpbar = fpb * 4
        first = looper_hud_eighth_index(
            total_frames=0, frames_per_beat=fpb, beats_per_bar=4
        )
        wrapped = looper_hud_eighth_index(
            total_frames=fpbar, frames_per_beat=fpb, beats_per_bar=4
        )
        self.assertGreater(wrapped, first)
        self.assertEqual(
            looper_hud_tick_in_bar(
                total_frames=fpbar, frames_per_beat=fpb, beats_per_bar=4
            ),
            0,
        )

    def test_tick_from_internal_prefers_total_frames(self) -> None:
        from patch_browser.looper_hud import looper_hud_tick_from_internal

        fpb = self.FPB
        self.assertEqual(
            looper_hud_tick_from_internal(
                {
                    "total_frames": fpb // 2,
                    "frames_per_beat": fpb,
                    "beats_per_bar": 4,
                    "tick_in_bar": 99,
                }
            ),
            1,
        )

    def test_bar_fraction_empty_when_inactive(self) -> None:
        snap = {
            "internal_timing": {"active": False},
            "connected": False,
            "bpm": None,
        }
        self.assertEqual(looper_hud_bar_fraction(snap), "")

    def test_is_visible_false_when_timing_cleared(self) -> None:
        self.assertFalse(
            looper_hud_is_visible(
                {"connected": False, "internal_timing": {"active": False}}
            )
        )

    def test_bar_counter_and_tick_at_loop_wrap(self) -> None:
        from patch_browser.looper_hud import looper_hud_tick_from_internal

        fpb = self.FPB
        fpbar = fpb * 4
        internal = {
            "active": True,
            "bar_in_loop": 1,
            "bars_per_loop": 4,
            "total_frames": fpbar * 4,
            "frames_per_beat": fpb,
            "beats_per_bar": 4,
        }
        snap = {"internal_timing": internal}
        self.assertEqual(looper_hud_bar_fraction(snap), "1/4")
        self.assertEqual(looper_hud_tick_from_internal(internal), 0)


if __name__ == "__main__":
    unittest.main()
