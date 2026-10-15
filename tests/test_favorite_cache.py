"""The heart is drawn per visible row per frame, so it must never touch the disk.

Before pointers the answer walked the filesystem (O(favourites) ``Path.resolve()``),
which measured ~19,000 newfstatat/s on the appliance (2026-08-17): a full core in the
process that also hosts a JACK client, and a memo hid it. The index is a dict now; there
is nothing to memoise, and nothing may go back to the filesystem.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from patch_browser.favorites_index import FavoritesIndex
from patch_browser.patch_scanner import PatchScanner

PATCH = {"name": "Sub", "path": "/lib/Patches/Bass/Sub.fxp", "stable_key": "factory:Bass/Sub"}


def _scanner(case: unittest.TestCase, *keys: str) -> PatchScanner:
    tmp = tempfile.TemporaryDirectory()
    case.addCleanup(tmp.cleanup)
    scanner = PatchScanner(patch_dirs=[], last_patch_file=None)
    scanner.favorites_index = FavoritesIndex(Path(tmp.name) / "fav.json")
    for key in keys:
        scanner.favorites_index.add(key)
    return scanner


class HeartIsALookup(unittest.TestCase):
    def test_no_filesystem_call_per_row_per_frame(self) -> None:
        scanner = _scanner(self, "factory:Bass/Sub")
        boom = mock.Mock(side_effect=AssertionError("filesystem touched on the draw path"))
        with mock.patch.object(Path, "resolve", boom), mock.patch.object(Path, "exists", boom), \
                mock.patch.object(Path, "is_dir", boom), mock.patch.object(Path, "stat", boom), \
                mock.patch.object(os, "stat", boom), mock.patch.object(os, "walk", boom):
            for _ in range(600):  # ten seconds of frames
                self.assertTrue(scanner.is_patch_in_favorites(PATCH))
                self.assertFalse(scanner.is_patch_in_favorites({**PATCH, "stable_key": "factory:Other"}))

    def test_the_heart_follows_the_index_at_once(self) -> None:
        scanner = _scanner(self)
        self.assertFalse(scanner.is_patch_in_favorites(PATCH))
        scanner.favorites_index.add("factory:Bass/Sub")
        self.assertTrue(scanner.is_patch_in_favorites(PATCH))
        scanner.favorites_index.remove("factory:Bass/Sub")
        self.assertFalse(scanner.is_patch_in_favorites(PATCH))

    def test_empty_patch_is_not_favourited(self) -> None:
        scanner = _scanner(self)
        self.assertFalse(scanner.is_patch_in_favorites(None))
        self.assertFalse(scanner.is_patch_in_favorites({}))

    def test_a_view_row_and_its_source_share_the_heart(self) -> None:
        scanner = _scanner(self, "factory:Bass/Sub")
        view = {**PATCH, "category": "!Quick Access", "favorite_view": True}
        self.assertTrue(scanner.is_patch_in_favorites(view))
        self.assertTrue(scanner.is_patch_in_favorites(PATCH))

    def test_no_cache_left_to_go_stale(self) -> None:
        self.assertFalse(hasattr(_scanner(self), "_favorite_cache"))
        self.assertFalse(hasattr(PatchScanner, "_compute_patch_in_favorites"))


class EveryMutationRebuildsTheViews(unittest.TestCase):
    """The list is built from the index: every index save must be followed by a rebuild."""

    def test_every_index_save_is_paired_with_a_rebuild(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "patch_browser" / "patch_scanner.py").read_text(encoding="utf-8")
        lines = source.split("\n")
        saves = [i for i, ln in enumerate(lines) if "self.favorites_index.save()" in ln]
        self.assertTrue(saves, "no favorites_index.save() sites found: test is stale")
        for idx in saves:
            following = "\n".join(lines[idx + 1 : idx + 3])
            self.assertIn("invalidate_favorite_cache", following,
                          f"favorites_index.save() at line {idx + 1} does not rebuild the views")

    def test_invalidate_rebuilds(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "patch_browser" / "patch_scanner.py").read_text(encoding="utf-8")
        body = source.split("def invalidate_favorite_cache")[1].split("    def ")[0]
        self.assertIn("rescan_favorites_category", body)


if __name__ == "__main__":
    unittest.main()
