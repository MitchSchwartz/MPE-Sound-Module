"""Tests for touch header looper HUD merge + visibility."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from patch_browser.looper_hud import (
    MAX_EXTRAPOLATION_S,
    looper_hud_bar_fraction,
    looper_hud_bar_in_loop,
    looper_hud_eighth_index,
    looper_hud_interpolated_frames,
    looper_hud_is_visible,
    looper_hud_min_width_px,
    looper_hud_segment_fills,
    looper_hud_tick_from_internal,
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

    def _fills(self, total_frames: int, *, beats: int = 4) -> list[float]:
        return looper_hud_segment_fills(
            total_frames=total_frames,
            frames_per_beat=self.FPB,
            beats_per_bar=beats,
        )

    def test_eighth_tick_at_bar_start(self) -> None:
        self.assertEqual(
            looper_hud_tick_in_bar(
                total_frames=0, frames_per_beat=self.FPB, beats_per_bar=4
            ),
            0,
        )

    def test_segment_fills_are_continuous_within_a_beat(self) -> None:
        fpb = self.FPB
        self.assertEqual(self._fills(0), [0.0, 0.0, 0.0, 0.0])
        self.assertEqual(self._fills(fpb // 4), [0.25, 0.0, 0.0, 0.0])
        self.assertEqual(self._fills(fpb // 2), [0.5, 0.0, 0.0, 0.0])
        self.assertEqual(self._fills(fpb), [1.0, 0.0, 0.0, 0.0])
        self.assertEqual(self._fills(fpb + fpb // 2), [1.0, 0.5, 0.0, 0.0])

    def test_segment_fills_advance_smoothly_not_in_half_steps(self) -> None:
        """Sub-tick frame deltas must move the fill — the HUD wobble regression."""
        fpb = self.FPB
        step = fpb // 100
        seen = [self._fills(step * i)[0] for i in range(1, 20)]
        self.assertEqual(seen, sorted(seen))
        self.assertEqual(len(set(seen)), len(seen))

    def test_segment_fills_reset_to_zero_at_each_bar_line(self) -> None:
        fpb = self.FPB
        fpbar = fpb * 4
        last = self._fills(fpbar - 1)
        self.assertEqual(last[:3], [1.0, 1.0, 1.0])
        self.assertGreater(last[3], 0.99)
        self.assertEqual(self._fills(fpbar), [0.0, 0.0, 0.0, 0.0])
        self.assertEqual(self._fills(fpbar * 3), [0.0, 0.0, 0.0, 0.0])

    def test_segment_fills_clamp_negative_and_odd_meter(self) -> None:
        self.assertEqual(self._fills(-500), [0.0, 0.0, 0.0, 0.0])
        self.assertEqual(len(self._fills(0, beats=3)), 3)
        self.assertEqual(
            looper_hud_segment_fills(
                total_frames=10, frames_per_beat=0, beats_per_bar=0
            ),
            [0.0],
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


class LooperHudInterpolationTests(unittest.TestCase):
    """Draw path estimates forward from the last publish (publishes are ~25 Hz)."""

    FPB = 24000  # 48 kHz @ 120 bpm
    SR = 48000

    def _internal(self, **over) -> dict:
        internal = {
            "active": True,
            "total_frames": 0,
            "frames_per_beat": self.FPB,
            "beats_per_bar": 4,
            "bars_per_loop": 4,
            "bar_in_loop": 1,
            "sample_rate": self.SR,
            "updated_at": 1000.0,
        }
        internal.update(over)
        return internal

    def test_interpolates_elapsed_seconds_into_frames(self) -> None:
        internal = self._internal(total_frames=10_000)
        # 1/16 s is exactly representable, so the expected frame count is exact.
        self.assertEqual(
            looper_hud_interpolated_frames(internal, now=1000.0625),
            10_000 + 3_000,
        )

    def test_extrapolation_is_clamped(self) -> None:
        internal = self._internal(total_frames=10_000)
        clamped = 10_000 + int(MAX_EXTRAPOLATION_S * self.SR)
        self.assertEqual(looper_hud_interpolated_frames(internal, now=1000.5), clamped)
        self.assertEqual(looper_hud_interpolated_frames(internal, now=1005.0), clamped)

    def test_negative_elapsed_returns_raw(self) -> None:
        internal = self._internal(total_frames=10_000)
        self.assertEqual(looper_hud_interpolated_frames(internal, now=999.9), 10_000)
        self.assertEqual(looper_hud_interpolated_frames(internal, now=1000.0), 10_000)

    def test_missing_fields_return_raw(self) -> None:
        raw = 10_000
        for missing in ("sample_rate", "updated_at"):
            internal = self._internal(total_frames=raw, **{missing: None})
            self.assertEqual(
                looper_hud_interpolated_frames(internal, now=1000.02), raw, missing
            )
        self.assertEqual(
            looper_hud_interpolated_frames(
                self._internal(total_frames=raw, active=False), now=1000.02
            ),
            raw,
        )
        self.assertEqual(
            looper_hud_interpolated_frames(
                self._internal(total_frames=None), now=1000.02
            ),
            0,
        )

    def test_tick_advances_between_publishes(self) -> None:
        # Published exactly on an eighth boundary; 40 ms later the next eighth is due
        # at 120 bpm (an eighth is 250 ms), so the tick must not have moved yet …
        internal = self._internal(total_frames=self.FPB // 2)
        self.assertEqual(looper_hud_tick_from_internal(internal, now=1000.04), 1)
        # … but crossing the boundary inside the publish gap must show through.
        internal = self._internal(total_frames=self.FPB - 480)  # 10 ms short of beat 2
        self.assertEqual(looper_hud_tick_from_internal(internal, now=1000.0), 1)
        self.assertEqual(looper_hud_tick_from_internal(internal, now=1000.02), 2)

    def test_bar_in_loop_helper_wraps(self) -> None:
        fpbar = self.FPB * 4
        for frames, expected in ((0, 1), (fpbar, 2), (fpbar * 3, 4), (fpbar * 4, 1)):
            self.assertEqual(
                looper_hud_bar_in_loop(
                    total_frames=frames,
                    frames_per_beat=self.FPB,
                    beats_per_bar=4,
                    bars_per_loop=4,
                ),
                expected,
                frames,
            )

    def test_bar_fraction_advances_when_interpolation_crosses_a_bar(self) -> None:
        fpbar = self.FPB * 4
        internal = self._internal(
            total_frames=fpbar - 480,  # 10 ms short of bar 2
            bar_in_loop=1,  # stale published value
        )
        snap = {"internal_timing": internal}
        self.assertEqual(looper_hud_bar_fraction(snap, now=1000.0), "1/4")
        self.assertEqual(looper_hud_bar_fraction(snap, now=1000.02), "2/4")

    def test_bar_fraction_falls_back_to_published_bar(self) -> None:
        snap = {
            "internal_timing": {
                "active": True,
                "bar_in_loop": 3,
                "bars_per_loop": 4,
            }
        }
        self.assertEqual(looper_hud_bar_fraction(snap), "3/4")


if __name__ == "__main__":
    unittest.main()
