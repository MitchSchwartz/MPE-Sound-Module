"""Tests for dual-anchor normalization (#31 Stage 2) and full norm gain (#31 Stage 1)."""

from __future__ import annotations

import unittest

from patch_browser.patch_normalization import (
    compute_gain_db,
    compute_gain_db_dual_anchor,
    stage_db,
)


class DualAnchorGainTests(unittest.TestCase):
    def test_dual_anchor_uses_min_of_strike_and_sustain(self) -> None:
        strike_gain = compute_gain_db(-30.0, -10.0)
        sustain_gain = compute_gain_db(-22.0, -6.0)
        dual = compute_gain_db_dual_anchor(-30.0, -10.0, -22.0, -6.0)
        self.assertAlmostEqual(dual, min(strike_gain, sustain_gain))

    def test_dual_anchor_attenuates_when_strike_already_hot(self) -> None:
        # A Robotic Mind shape: loud strike, quiet sustain — must not boost for sustain.
        dual = compute_gain_db_dual_anchor(-14.85, -7.95, -24.03, -7.95)
        strike_gain = compute_gain_db(-14.85, -7.95)
        sustain_gain = compute_gain_db(-24.03, -7.95)
        self.assertLess(strike_gain, 0.0)
        self.assertGreater(sustain_gain, 0.0)
        self.assertAlmostEqual(dual, strike_gain)

    def test_norm_active_applies_the_full_calibrated_gain(self) -> None:
        """No cap between the calibration and the stage: +18 dB is +18 dB."""
        self.assertEqual(
            stage_db(norm_db=18.0, ceiling_db=None, headroom_db=0.0, vol_db=0.0), 18.0
        )

if __name__ == "__main__":
    unittest.main()
