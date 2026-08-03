"""Tests for Surge OSC normalized value helpers."""

from __future__ import annotations

import unittest

from patch_browser.surge_osc_params import (
    bipolar_to_normalized,
    db_attenuation_to_normalized,
    db_extra_narrow_to_normalized,
    freq_audible_hp_to_normalized,
    normalized_to_db_attenuation,
)


class SurgeOscParamsTests(unittest.TestCase):
    def test_db_attenuation_unity_and_ceiling(self) -> None:
        self.assertAlmostEqual(db_attenuation_to_normalized(0.0), 1.0)
        self.assertAlmostEqual(db_attenuation_to_normalized(-1.0), 47.0 / 48.0)
        self.assertAlmostEqual(db_attenuation_to_normalized(-48.0), 0.0)
        self.assertAlmostEqual(normalized_to_db_attenuation(47.0 / 48.0), -1.0)

    def test_bipolar_fast_is_zero_norm(self) -> None:
        self.assertAlmostEqual(bipolar_to_normalized(-1.0), 0.0)
        self.assertAlmostEqual(bipolar_to_normalized(0.0), 0.5)
        self.assertAlmostEqual(bipolar_to_normalized(1.0), 1.0)

    def test_extra_narrow_zero_is_mid(self) -> None:
        self.assertAlmostEqual(db_extra_narrow_to_normalized(0.0), 0.5)

    def test_hp_default_is_zero_norm(self) -> None:
        self.assertAlmostEqual(freq_audible_hp_to_normalized(-60.0), 0.0)


if __name__ == "__main__":
    unittest.main()
