"""Favorites index backup and restore (the index is the whole favorites state)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from patch_browser.favorites_backup import default_backup_root, restore_index, snapshot_index


class FavoritesBackupTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.index = self.root / "patch_browser_favorites.json"
        self.index.write_text('{"version":2,"folders":[],"entries":{"a:b":{"folder":""}}}', encoding="utf-8")

    def test_snapshot_copies_the_index_byte_for_byte(self) -> None:
        out = snapshot_index(self.index, dest_dir=self.root / "snap")
        self.assertEqual((out / self.index.name).read_bytes(), self.index.read_bytes())

    def test_default_location_is_beside_the_index(self) -> None:
        out = snapshot_index(self.index, label="x")
        self.assertEqual(out.parent, default_backup_root(self.index))
        self.assertEqual(out.parent.parent, self.root)
        self.assertTrue(out.name.endswith("-x"))

    def test_two_snapshots_in_one_second_do_not_collide(self) -> None:
        a = snapshot_index(self.index, label="x")
        b = snapshot_index(self.index, label="x")
        self.assertNotEqual(a, b)

    def test_a_missing_index_still_makes_a_snapshot_directory(self) -> None:
        self.index.unlink()
        out = snapshot_index(self.index, dest_dir=self.root / "snap")
        self.assertFalse((out / self.index.name).exists())

    def test_restore_puts_it_back_and_keeps_a_pre_restore_copy(self) -> None:
        snap = snapshot_index(self.index, dest_dir=self.root / "snap")
        original = self.index.read_bytes()
        self.index.write_text('{"version":2,"folders":[],"entries":{}}', encoding="utf-8")
        changed = self.index.read_bytes()
        pre = restore_index(self.index, snap)
        self.assertEqual(self.index.read_bytes(), original)
        assert pre is not None
        self.assertEqual((pre / self.index.name).read_bytes(), changed)

    def test_restore_without_a_backup(self) -> None:
        snap = snapshot_index(self.index, dest_dir=self.root / "snap")
        self.assertIsNone(restore_index(self.index, snap, backup_before=False))

    def test_restore_from_an_empty_snapshot_fails_and_changes_nothing(self) -> None:
        empty = self.root / "empty"
        empty.mkdir()
        before = self.index.read_bytes()
        with self.assertRaises(FileNotFoundError):
            restore_index(self.index, empty)
        self.assertEqual(self.index.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
