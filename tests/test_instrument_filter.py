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

    def test_instruments_with_patches_is_alphabetical(self) -> None:
        """bass/lead/pad alone cannot prove this — they are alphabetical AND in
        vocab order. Use instruments whose two orderings disagree."""
        patches = [
            {"instrument_primary": "piano"},   # vocab index 0
            {"instrument_primary": "bass"},    # vocab index 3
            {"instrument_primary": "organ"},   # vocab index 2
        ]
        self.assertEqual(
            instruments_with_patches(patches), ["bass", "organ", "piano"]
        )

    def test_instruments_with_patches_is_not_in_vocab_order(self) -> None:
        """Anti-vacuity: the assertion above must be able to fail."""
        from patch_browser.patch_metadata import INSTRUMENT_VOCAB

        patches = [{"instrument_primary": n} for n in ("piano", "bass", "organ")]
        vocab_order = [n for n in INSTRUMENT_VOCAB if n in {"piano", "bass", "organ"}]
        self.assertEqual(vocab_order, ["piano", "organ", "bass"])
        self.assertNotEqual(instruments_with_patches(patches), vocab_order)

    def test_only_vocab_instruments_become_chips(self) -> None:
        patches = [
            {"instrument_primary": "bass"},
            {"instrument_primary": "not-a-real-instrument"},
        ]
        self.assertEqual(instruments_with_patches(patches), ["bass"])

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
