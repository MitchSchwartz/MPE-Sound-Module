"""Memoised heart state on the per-frame draw path.

``is_patch_in_favorites`` is called once per visible row per frame. Uncached it walks
the filesystem — O(favourites) ``Path.resolve()`` plus O(patch_dirs) ``exists()`` —
which measured ~19,000 newfstatat/s on the appliance (2026-08-17), a full core in the
process that also hosts a JACK client.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch as mock_patch

from patch_browser.patch_scanner import PatchScanner


def _scanner() -> PatchScanner:
    return PatchScanner(patch_dirs=[], last_patch_file=None)


PATCH = {"name": "Sub", "path": "/lib/Patches/Bass/Sub.fxp", "stable_key": "factory:Bass/Sub"}


class FavoriteCacheTests(unittest.TestCase):
    def test_repeated_calls_compute_once(self) -> None:
        scanner = _scanner()
        with mock_patch.object(
            scanner, "_compute_patch_in_favorites", return_value=True
        ) as compute:
            for _ in range(60):  # one second of frames for one row
                self.assertTrue(scanner.is_patch_in_favorites(PATCH))
        compute.assert_called_once()

    def test_false_is_cached_too(self) -> None:
        """A non-favourite row must not re-walk the tree every frame either."""
        scanner = _scanner()
        with mock_patch.object(
            scanner, "_compute_patch_in_favorites", return_value=False
        ) as compute:
            for _ in range(30):
                self.assertFalse(scanner.is_patch_in_favorites(PATCH))
        compute.assert_called_once()

    def test_distinct_patches_are_cached_separately(self) -> None:
        scanner = _scanner()
        other = {"name": "Lead", "path": "/lib/Patches/Lead.fxp", "stable_key": "factory:Lead"}
        with mock_patch.object(
            scanner, "_compute_patch_in_favorites", side_effect=[True, False]
        ):
            self.assertTrue(scanner.is_patch_in_favorites(PATCH))
            self.assertFalse(scanner.is_patch_in_favorites(other))
        self.assertTrue(scanner.is_patch_in_favorites(PATCH))
        self.assertFalse(scanner.is_patch_in_favorites(other))

    def test_invalidation_makes_a_toggle_visible(self) -> None:
        """The hazard this cache introduces: a stale heart after favouriting."""
        scanner = _scanner()
        with mock_patch.object(
            scanner, "_compute_patch_in_favorites", side_effect=[False, True]
        ):
            self.assertFalse(scanner.is_patch_in_favorites(PATCH))
            scanner.invalidate_favorite_cache()
            self.assertTrue(scanner.is_patch_in_favorites(PATCH))

    def test_empty_patch_is_not_favourited(self) -> None:
        scanner = _scanner()
        self.assertFalse(scanner.is_patch_in_favorites(None))
        self.assertFalse(scanner.is_patch_in_favorites({}))

    def test_patch_without_stable_key_still_caches_by_path(self) -> None:
        scanner = _scanner()
        no_key = {"name": "Sub", "path": "/lib/Patches/Bass/Sub.fxp"}
        with mock_patch.object(
            scanner, "_compute_patch_in_favorites", return_value=True
        ) as compute:
            scanner.is_patch_in_favorites(no_key)
            scanner.is_patch_in_favorites(no_key)
        compute.assert_called_once()


class FavoriteCacheInvalidationWiringTests(unittest.TestCase):
    """Every favourites mutation must invalidate, or the heart goes stale."""

    def test_every_index_save_is_paired_with_an_invalidation(self) -> None:
        from pathlib import Path

        source = (
            Path(__file__).resolve().parents[1] / "patch_browser" / "patch_scanner.py"
        ).read_text(encoding="utf-8")
        lines = source.split("\n")
        saves = [i for i, ln in enumerate(lines) if "self.favorites_index.save()" in ln]
        self.assertTrue(saves, "no favorites_index.save() sites found — test is stale")
        for idx in saves:
            following = "\n".join(lines[idx + 1 : idx + 3])
            self.assertIn(
                "invalidate_favorite_cache",
                following,
                f"favorites_index.save() at line {idx + 1} does not invalidate the cache",
            )

    def test_rescan_invalidates(self) -> None:
        from pathlib import Path

        source = (
            Path(__file__).resolve().parents[1] / "patch_browser" / "patch_scanner.py"
        ).read_text(encoding="utf-8")
        body = source.split("def rescan_favorites_category")[1].split("def ")[0]
        self.assertIn("invalidate_favorite_cache", body)


if __name__ == "__main__":
    unittest.main()
