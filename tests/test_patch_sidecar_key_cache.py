"""Path-resolution memoisation on the sidecar lookup hot path.

These lookups run per mixer fader and per visible list row, every frame. Unmemoised,
``Path.resolve()`` stats every component of every patch root — measured at ~19,000
newfstatat/s on the appliance (2026-08-17), a full core, which starves the GIL that
the in-process JACK client's callback needs.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch as mock_patch

from patch_browser import patch_sidecar_key as psk
from patch_browser.patch_sidecar_key import (
    clear_path_caches,
    lookup_keys,
    stable_key_from_absolute_path,
)


class StableKeyCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_path_caches()
        self.addCleanup(clear_path_caches)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "Patches"
        (self.root / "Bass").mkdir(parents=True)
        self.patch_file = self.root / "Bass" / "Sub.fxp"
        self.patch_file.write_text("x", encoding="utf-8")

    def test_result_is_correct_and_stable_across_calls(self) -> None:
        first = stable_key_from_absolute_path(self.patch_file, [self.root])
        second = stable_key_from_absolute_path(self.patch_file, [self.root])
        self.assertIsNotNone(first)
        self.assertEqual(first, second)

    def test_repeated_lookups_do_not_re_resolve(self) -> None:
        """The whole point: one filesystem walk, not one per frame."""
        stable_key_from_absolute_path(self.patch_file, [self.root])
        with mock_patch.object(
            psk.Path, "resolve", side_effect=AssertionError("re-resolved a cached path")
        ):
            for _ in range(60):  # one second of frames
                stable_key_from_absolute_path(self.patch_file, [self.root])

    def test_a_miss_is_cached_too(self) -> None:
        """A patch outside every root must not re-walk the tree on every frame."""
        outside = Path(self._tmp.name) / "elsewhere.fxp"
        outside.write_text("x", encoding="utf-8")
        self.assertIsNone(stable_key_from_absolute_path(outside, [self.root]))
        with mock_patch.object(
            psk.Path, "resolve", side_effect=AssertionError("re-resolved a cached miss")
        ):
            self.assertIsNone(stable_key_from_absolute_path(outside, [self.root]))

    def test_different_roots_are_different_cache_entries(self) -> None:
        other_root = Path(self._tmp.name) / "Other"
        other_root.mkdir()
        keyed_to_root = stable_key_from_absolute_path(self.patch_file, [self.root])
        keyed_to_other = stable_key_from_absolute_path(self.patch_file, [other_root])
        self.assertIsNotNone(keyed_to_root)
        self.assertIsNone(keyed_to_other)

    def test_clear_path_caches_forces_a_fresh_walk(self) -> None:
        """Documented hazard: the memo is process-lifetime, so a rescan must clear it."""
        stable_key_from_absolute_path(self.patch_file, [self.root])
        clear_path_caches()
        with mock_patch.object(
            psk.Path, "resolve", wraps=Path.resolve, autospec=True
        ) as resolve:
            stable_key_from_absolute_path(self.patch_file, [self.root])
        self.assertGreater(resolve.call_count, 0)


class LookupKeysShortCircuitTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_path_caches()
        self.addCleanup(clear_path_caches)

    def test_no_filesystem_work_when_stable_key_is_known(self) -> None:
        """Scanned patches carry stable_key; deriving it again was pure waste."""
        with mock_patch.object(
            psk,
            "stable_key_from_absolute_path",
            side_effect=AssertionError("derived a key the caller already had"),
        ):
            keys = lookup_keys(
                "Sub.fxp",
                patch_path="/somewhere/Patches/Bass/Sub.fxp",
                stable_key="factory:Bass/Sub",
                patch_dirs=[Path("/somewhere/Patches")],
            )
        self.assertEqual(keys[0], "factory:Bass/Sub")
        self.assertIn("Sub", keys)

    def test_still_derives_when_stable_key_is_absent(self) -> None:
        with mock_patch.object(
            psk, "stable_key_from_absolute_path", return_value="factory:Bass/Sub"
        ) as derive:
            keys = lookup_keys(
                "Sub.fxp",
                patch_path="/somewhere/Patches/Bass/Sub.fxp",
                patch_dirs=[Path("/somewhere/Patches")],
            )
        derive.assert_called()
        self.assertEqual(keys[0], "factory:Bass/Sub")

    def test_stem_fallback_survives(self) -> None:
        keys = lookup_keys("Sub.fxp")
        self.assertEqual(keys, ["Sub"])


if __name__ == "__main__":
    unittest.main()
