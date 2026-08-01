"""Tests for flat All patches index."""

from __future__ import annotations

import threading
import unittest
from unittest.mock import MagicMock

from patch_browser.all_patches_index import build_flat_patch_list, first_sort_letter


class TestAllPatchesIndex(unittest.TestCase):
    def test_first_sort_letter(self) -> None:
        self.assertEqual(first_sort_letter("Acid"), "A")
        self.assertEqual(first_sort_letter("123"), "#")
        self.assertEqual(first_sort_letter(""), "#")

    def test_build_flat_patch_list_sorts_and_indexes(self) -> None:
        scanner = MagicMock()
        scanner.scan_lock = threading.Lock()
        scanner.patches = {
            "Bass": [
                {"name": "Zebra", "path": "/z.fxp", "category": "Bass"},
                {"name": "Acid", "path": "/a.fxp", "category": "Bass"},
            ],
            "Keys": [
                {"name": "Glass", "path": "/g.fxp", "category": "Keys"},
            ],
        }

        patches, letter_index = build_flat_patch_list(scanner)

        self.assertEqual([p["name"] for p in patches], ["Acid", "Glass", "Zebra"])
        self.assertEqual(letter_index["A"], 0)
        self.assertEqual(letter_index["G"], 1)
        self.assertEqual(letter_index["Z"], 2)


if __name__ == "__main__":
    unittest.main()
