"""Tests for patch identity helpers."""

from __future__ import annotations

import unittest
from pathlib import Path

from patch_browser.patch_identity import (
    build_folder_tree,
    category_and_inner_segments,
    make_patch_entry,
    patch_root_label,
    stable_key_for_relative_path,
)


class PatchIdentityTests(unittest.TestCase):
    def test_patch_root_label(self) -> None:
        self.assertEqual(patch_root_label(Path("/x/patches_factory")), "factory")
        self.assertEqual(patch_root_label(Path("/x/patches_3rdparty")), "thirdparty")
        self.assertEqual(patch_root_label(Path("/x/Patches")), "user")

    def test_stable_key(self) -> None:
        key = stable_key_for_relative_path("factory", Path("Bass/Sub/Lead 1.fxp"))
        self.assertEqual(key, "factory:Bass/Sub/Lead 1")

    def test_category_and_inner_segments(self) -> None:
        self.assertEqual(category_and_inner_segments(Path(".")), ("Uncategorized", ()))
        self.assertEqual(category_and_inner_segments(Path("Bass")), ("Bass", ()))
        self.assertEqual(
            category_and_inner_segments(Path("Bass/Sub")),
            ("Bass", ("Sub",)),
        )

    def test_make_patch_entry_nested(self) -> None:
        root = Path("/library/patches_factory")
        path = root / "Bass" / "Sub" / "Lead 1.fxp"
        entry = make_patch_entry(
            name="Lead 1",
            path=path,
            patch_dir=root,
            root_label="factory",
        )
        self.assertEqual(entry["category"], "Bass")
        self.assertEqual(entry["inner_segments"], ("Sub",))
        self.assertEqual(entry["stable_key"], "factory:Bass/Sub/Lead 1")

    def test_patch_browse_subtitle(self) -> None:
        from patch_browser.patch_identity import patch_browse_subtitle

        self.assertEqual(
            patch_browse_subtitle({"category": "Bass", "inner_segments": ("Sub",)}),
            "Bass/Sub",
        )
        self.assertEqual(
            patch_browse_subtitle({"category": "Piano", "inner_segments": ()}),
            "Piano",
        )

    def test_build_folder_tree_nested(self) -> None:
        patches = {
            "Bass": [
                {
                    "name": "Root Bass",
                    "inner_segments": (),
                    "path": "/a.fxp",
                },
                {
                    "name": "Sub Lead",
                    "inner_segments": ("Sub",),
                    "path": "/b.fxp",
                },
            ]
        }
        tree = build_folder_tree(patches)
        self.assertEqual(len(tree["Bass"]["patches"]), 1)
        self.assertEqual(tree["Bass"]["patches"][0]["name"], "Root Bass")
        self.assertIn("Sub", tree["Bass"]["children"])
        self.assertEqual(
            tree["Bass"]["children"]["Sub"]["patches"][0]["name"],
            "Sub Lead",
        )


if __name__ == "__main__":
    unittest.main()
