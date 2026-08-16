"""Tests for Quick Select backup / index rebuild."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from patch_browser.favorites_backup import rebuild_index_from_qa_tree, snapshot_favorites
from patch_browser.favorites_index import FavoritesIndex


class FavoritesBackupTests(unittest.TestCase):
    def test_snapshot_writes_tree_and_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            qa = root / "Quick Select"
            qa.mkdir()
            (qa / "Acid.fxp").write_bytes(b"x")
            index = root / "patch_browser_favorites.json"
            index.write_text('{"version":1,"folders":[],"entries":{}}', encoding="utf-8")

            out = snapshot_favorites(qa, index, dest_dir=root / "snap")
            self.assertTrue((out / "Quick Select" / "Acid.fxp").is_file())
            self.assertTrue((out / "patch_browser_favorites.json").is_file())

    def test_rebuild_index_from_flat_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            qa = root / "Quick Select"
            qa.mkdir()
            (qa / "Acid.fxp").write_bytes(b"x")
            index = FavoritesIndex(root / "fav.json")
            stem_map = {"Acid": ["factory:Lead/Acid"]}
            count, errors = rebuild_index_from_qa_tree(index, qa, stem_map)
            self.assertEqual(errors, [])
            self.assertEqual(count, 1)
            self.assertTrue(index.is_favorited("factory:Lead/Acid"))
            entry = index.get_entry("factory:Lead/Acid")
            assert entry is not None
            self.assertEqual(entry.get("folder"), "")


if __name__ == "__main__":
    unittest.main()
