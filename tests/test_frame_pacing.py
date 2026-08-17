"""Draw-loop pacing — idle frame rate is an audio concern, not just a power one.

A JACK client lives in the touch UI process, so a draw loop that saturates a core
holds the GIL that the client's callback needs, and jackd's realtime cycle waits.
"""

from __future__ import annotations

import unittest

from patch_browser.frame_pacing import (
    ACTIVE_FPS,
    ACTIVE_HOLD_S,
    IDLE_FPS,
    frame_rate_for,
)


class FramePacingTests(unittest.TestCase):
    def test_busy_frame_runs_at_full_rate(self) -> None:
        fps, last = frame_rate_for(busy=True, now=100.0, last_active_at=0.0)
        self.assertEqual(fps, ACTIVE_FPS)
        self.assertEqual(last, 100.0, "a busy frame must refresh the activity stamp")

    def test_idle_frame_drops_to_idle_rate(self) -> None:
        fps, last = frame_rate_for(busy=False, now=100.0, last_active_at=0.0)
        self.assertEqual(fps, IDLE_FPS)
        self.assertEqual(last, 0.0, "an idle frame must not refresh the stamp")

    def test_hysteresis_holds_full_rate_just_after_activity(self) -> None:
        """The tail of a momentum fling must not visibly step down mid-glide."""
        fps, _ = frame_rate_for(
            busy=False, now=100.0 + ACTIVE_HOLD_S * 0.5, last_active_at=100.0
        )
        self.assertEqual(fps, ACTIVE_FPS)

    def test_falls_back_once_the_hold_expires(self) -> None:
        fps, _ = frame_rate_for(
            busy=False, now=100.0 + ACTIVE_HOLD_S + 0.01, last_active_at=100.0
        )
        self.assertEqual(fps, IDLE_FPS)

    def test_sustained_activity_never_drops(self) -> None:
        last = 0.0
        for i in range(120):
            fps, last = frame_rate_for(busy=True, now=i / 60.0, last_active_at=last)
            self.assertEqual(fps, ACTIVE_FPS)

    def test_idle_rate_still_outpaces_the_live_meters(self) -> None:
        """The only always-live UI is the 5 Hz meter pair — don't alias it."""
        from patch_browser.surge_peak_monitor import POLL_INTERVAL_S

        meter_hz = 1.0 / POLL_INTERVAL_S
        self.assertGreaterEqual(IDLE_FPS, meter_hz * 2, "idle fps would alias the meters")
        self.assertLess(IDLE_FPS, ACTIVE_FPS)

    def test_idle_is_a_real_reduction(self) -> None:
        """If IDLE_FPS creeps back toward 60 this stops buying the audio thread time."""
        self.assertLessEqual(IDLE_FPS, ACTIVE_FPS // 2)


if __name__ == "__main__":
    unittest.main()
