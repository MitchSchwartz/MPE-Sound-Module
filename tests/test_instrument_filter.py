"""Tests for instrument chip filter helpers."""

import unittest

from patch_browser.instrument_filter import (
    filter_patches_by_instrument,
    instrument_counts,
    instruments_with_patches,
    primary_instrument,
)
from patch_browser.patch_identity import patch_list_subtitle


class InstrumentFilterTests(unittest.TestCase):
    def test_filter_by_primary(self) -> None:
        patches = [
            {"name": "A", "instrument_primary": "bass"},
            {"name": "B", "instrument_primary": "pad"},
            {"name": "C", "instrument_primary": "bass"},
        ]
        filtered = filter_patches_by_instrument(patches, "bass")
        self.assertEqual([p["name"] for p in filtered], ["A", "C"])

    def test_instruments_with_patches_respects_vocab_order(self) -> None:
        patches = [
            {"instrument_primary": "pad"},
            {"instrument_primary": "bass"},
            {"instrument_primary": "lead"},
        ]
        self.assertEqual(instruments_with_patches(patches), ["bass", "lead", "pad"])

    def test_primary_instrument_defaults_to_other(self) -> None:
        self.assertEqual(primary_instrument({}), "other")

    def test_instrument_counts(self) -> None:
        patches = [
            {"instrument_primary": "bass"},
            {"instrument_primary": "bass"},
            {"instrument_primary": "pad"},
        ]
        self.assertEqual(instrument_counts(patches), {"bass": 2, "pad": 1})

    def test_patch_list_subtitle(self) -> None:
        patch = {
            "category": "Bass",
            "inner_segments": ("Sub",),
            "instrument_primary": "bass",
        }
        self.assertEqual(patch_list_subtitle(patch), "Bass · Bass")


if __name__ == "__main__":
    unittest.main()
