"""Tests for touch header looper HUD merge + visibility."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from patch_browser.looper_hud import (
    looper_hud_bar_fraction,
    looper_hud_is_visible,
    looper_hud_min_width_px,
    merge_looper_hud_snapshot,
)
from patch_browser.looper_timing_state import write_timing_state


class LooperHudTests(unittest.TestCase):
    def test_merge_internal_timing_sets_looper_active(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "timing.json"
            write_timing_state(
                active=True,
                bpm=120.0,
                beat_in_bar=2,
                bar_in_loop=1,
                bars_per_loop=4,
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
            self.assertTrue(merged["running"])
            self.assertEqual(merged["bpm"], 120.0)
            self.assertEqual(merged["internal_timing"]["beat_in_bar"], 2)

    def test_bar_fraction_from_internal_timing(self) -> None:
        snap = {
            "internal_timing": {
                "active": True,
                "bar_in_loop": 2,
                "bars_per_loop": 4,
            }
        }
        self.assertEqual(looper_hud_bar_fraction(snap), "2/4")

    def test_visible_when_internal_active_without_pedal(self) -> None:
        snap = {
            "connected": False,
            "looper_active": True,
            "internal_timing": {"active": True, "bpm": 110},
        }
        self.assertTrue(looper_hud_is_visible(snap))

    def test_min_width_includes_bar_counter(self) -> None:
        narrow = looper_hud_min_width_px(frac_label="1/4")
        wide = looper_hud_min_width_px(frac_label="16/16")
        self.assertLess(narrow, wide)


if __name__ == "__main__":
    unittest.main()
