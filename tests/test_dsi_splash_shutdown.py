"""Unit tests for shutdown splash helpers (no display required)."""

from __future__ import annotations

import unittest

from patch_browser.dsi_splash import (
    BOOT_SPINNER_PERIOD,
    SHUTDOWN_SLOW_HINT_SECONDS,
    boot_animation_phase,
    shutdown_animation_phase,
    shutdown_subtitle,
)


class ShutdownSplashHelperTests(unittest.TestCase):
    def test_animation_phase_cycles(self) -> None:
        self.assertAlmostEqual(shutdown_animation_phase(0.0), 0.0)
        self.assertAlmostEqual(shutdown_animation_phase(0.6, period=1.2), 0.5)
        self.assertAlmostEqual(shutdown_animation_phase(1.2, period=1.2), 0.0)

    def test_boot_animation_phase_cycles(self) -> None:
        self.assertAlmostEqual(boot_animation_phase(0.0), 0.0)
        self.assertAlmostEqual(boot_animation_phase(0.6, period=BOOT_SPINNER_PERIOD), 0.5)
        self.assertAlmostEqual(boot_animation_phase(BOOT_SPINNER_PERIOD, period=BOOT_SPINNER_PERIOD), 0.0)

    def test_subtitle_before_slow_threshold(self) -> None:
        self.assertEqual(shutdown_subtitle(0.0), "Shutting down…")
        self.assertEqual(
            shutdown_subtitle(SHUTDOWN_SLOW_HINT_SECONDS - 0.1),
            "Shutting down…",
        )

    def test_subtitle_after_slow_threshold(self) -> None:
        self.assertEqual(
            shutdown_subtitle(SHUTDOWN_SLOW_HINT_SECONDS),
            "Still shutting down…",
        )
        self.assertEqual(shutdown_subtitle(60.0), "Still shutting down…")


if __name__ == "__main__":
    unittest.main()
