"""Tests for dual-anchor normalization (#31 Stage 2) and full norm gain (#31 Stage 1)."""

from __future__ import annotations

import unittest

from patch_browser.patch_normalization import (
    compute_gain_db,
    compute_gain_db_dual_anchor,
    db_to_linear,
    volume_fader_to_amp_linear,
)
from patch_browser.touch_ui_constants import VOLUME_MAX, VOLUME_MIN


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

    def test_norm_active_applies_full_calibrated_linear_gain(self) -> None:
        gain_linear = db_to_linear(18.0)
        capped = volume_fader_to_amp_linear(
            1.0,
            patch_gain_linear=gain_linear,
            cap=1.5,
            fader_min=VOLUME_MIN,
            fader_max=VOLUME_MAX,
            norm_active=False,
        )
        full = volume_fader_to_amp_linear(
            1.0,
            patch_gain_linear=gain_linear,
            cap=1.5,
            fader_min=VOLUME_MIN,
            fader_max=VOLUME_MAX,
            norm_active=True,
        )
        self.assertLess(capped, full)
        self.assertAlmostEqual(full, gain_linear)


if __name__ == "__main__":
    unittest.main()
