"""Tests for favorites v2 index and scanner integration."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from patch_browser.favorites_index import (
    DEFAULT_FAVORITES_FOLDER,
    FavoritesIndex,
    LEGACY_LIKED_FOLDER,
)
from patch_browser.patch_scanner import PatchScanner, favorites_display_name


class FavoritesIndexTests(unittest.TestCase):
    def test_add_remove_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fav.json"
            index = FavoritesIndex(path)
            index.add(
                "factory:Bass/Lead",
                folder=DEFAULT_FAVORITES_FOLDER,
                dest_path=Path(tmp) / "Lead.fxp",
            )
            index.save()
            reloaded = FavoritesIndex(path)
            self.assertTrue(reloaded.is_favorited("factory:Bass/Lead"))
            entry = reloaded.get_entry("factory:Bass/Lead")
            assert entry is not None
            self.assertEqual(entry["folder"], DEFAULT_FAVORITES_FOLDER)

            removed = reloaded.remove("factory:Bass/Lead")
            assert removed is not None
            self.assertFalse(reloaded.is_favorited("factory:Bass/Lead"))

    def test_create_and_delete_empty_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            qa = Path(tmp) / "Quick Access"
            qa.mkdir()
            index = FavoritesIndex(Path(tmp) / "fav.json")
            index.create_folder("Gig A", qa_root=qa)
            self.assertTrue((qa / "Gig A").is_dir())
            index.delete_folder("Gig A", qa_root=qa)
            self.assertFalse((qa / "Gig A").exists())

    def test_liked_folder_is_protected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            qa = Path(tmp) / "Quick Access"
            liked = qa / LEGACY_LIKED_FOLDER
            liked.mkdir(parents=True)
            index = FavoritesIndex(Path(tmp) / "fav.json")
            with self.assertRaises(ValueError):
                index.delete_folder(LEGACY_LIKED_FOLDER, qa_root=qa)

    def test_migrate_liked_to_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            qa = Path(tmp) / "Quick Access"
            liked = qa / LEGACY_LIKED_FOLDER
            liked.mkdir(parents=True)
            (liked / "Acid.fxp").write_bytes(b"x")
            index = FavoritesIndex(Path(tmp) / "fav.json")
            index.add(
                "factory:Bass/Acid",
                folder=LEGACY_LIKED_FOLDER,
                dest_path=liked / "Acid.fxp",
            )
            index.ensure_folder(LEGACY_LIKED_FOLDER)
            moved = index.migrate_legacy_liked_to_root(qa)
            self.assertEqual(moved, 1)
            self.assertTrue((qa / "Acid.fxp").exists())
            self.assertFalse(liked.exists())
            entry = index.get_entry("factory:Bass/Acid")
            assert entry is not None
            self.assertEqual(entry["folder"], DEFAULT_FAVORITES_FOLDER)
            self.assertNotIn(LEGACY_LIKED_FOLDER, index.folders)

    def test_migration_plan_requires_unambiguous_stable_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            qa = Path(tmp) / "Quick Access"
            qa.mkdir()
            (qa / "Lead.fxp").write_bytes(b"x")
            index = FavoritesIndex(Path(tmp) / "fav.json")
            plan = index.plan_flat_root_migration(
                qa,
                {"Lead": ["factory:A/Lead", "factory:B/Lead"]},
            )
            self.assertFalse(plan.ok)
            self.assertEqual(plan.move_count, 0)

    def test_migration_plan_single_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            qa = Path(tmp) / "Quick Access"
            qa.mkdir()
            (qa / "Acid.fxp").write_bytes(b"x")
            index = FavoritesIndex(Path(tmp) / "fav.json")
            plan = index.plan_flat_root_migration(
                qa,
                {"Acid": ["factory:Lead/Acid"]},
                target_folder="Gig",
            )
            self.assertTrue(plan.ok)
            self.assertEqual(plan.move_count, 1)
            self.assertEqual(plan.items[0].dest_path, qa / "Gig" / "Acid.fxp")


class PatchScannerFavoritesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.factory = self.root / "patches_factory"
        self.patches = self.root / "Patches"
        self.patches.mkdir(parents=True)
        self.factory.mkdir()
        self.qa = self.patches / "Quick Access"
        self.qa.mkdir()
        self._env_patch = mock.patch.dict(
            "os.environ",
            {"MPE_SURGE_DOCS": str(self.root), "MPE_FAVORITES_NAME": "Quick Access"},
            clear=False,
        )
        self._env_patch.start()
        self.scanner = PatchScanner([self.factory, self.patches])
        self.scanner.favorites_index = FavoritesIndex(self.root / "fav.json")

    def tearDown(self) -> None:
        self._env_patch.stop()
        self._tmp.cleanup()

    def _touch(self, rel: str, *, root: Path | None = None) -> Path:
        base = root or self.factory
        path = base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fxp")
        return path

    def test_add_patch_to_favorites_uses_qa_root(self) -> None:
        source = self._touch("Bass/Acid.fxp")
        self.scanner.scan_patches()
        entry = self.scanner.get_patch_by_path(source)
        assert entry is not None
        self.assertTrue(self.scanner.add_patch_to_favorites(entry))
        dest = self.qa / "Acid.fxp"
        self.assertTrue(dest.exists())
        self.assertTrue(
            self.scanner.favorites_index.is_favorited(entry["stable_key"])
        )
        self.assertEqual(
            self.scanner.favorites_index.get_entry(entry["stable_key"])["folder"],
            "",
        )

    def test_rescan_favorites_only_updates_qa_category(self) -> None:
        self._touch("Piano/Grand.fxp")
        self._touch("Acid.fxp", root=self.qa)
        self.scanner.scan_patches()
        label = favorites_display_name()
        before_count = len(self.scanner.get_patches_in_category("Piano"))

        self.scanner.add_patch_to_favorites(
            self.scanner.get_patch_by_path(self.factory / "Piano/Grand.fxp")
        )
        self.scanner.rescan_favorites_category()

        qa_patches = self.scanner.get_patches_in_category(label)
        self.assertTrue(any(p["name"] == "Grand" for p in qa_patches))
        self.assertEqual(
            len(self.scanner.get_patches_in_category("Piano")),
            before_count,
        )

    def test_add_folder_mirror_creates_subfolder(self) -> None:
        self._touch("Bass/Acid.fxp")
        self._touch("Bass/Sub/Wub.fxp")
        self.scanner.scan_patches()
        patches = self.scanner.get_patches_in_folder("Bass", ("Sub",))
        added, skipped = self.scanner.add_patches_to_favorites(patches, folder="Bass/Sub")
        self.assertEqual(added, 1)
        self.assertEqual(skipped, 0)
        self.assertTrue((self.qa / "Bass" / "Sub" / "Wub.fxp").exists())

    def test_remove_uses_index_not_full_rescan(self) -> None:
        source = self._touch("Lead/Solo.fxp")
        self.scanner.scan_patches()
        entry = self.scanner.get_patch_by_path(source)
        assert entry is not None
        self.scanner.add_patch_to_favorites(entry)
        with mock.patch.object(self.scanner, "scan_patches") as full_scan:
            self.assertTrue(self.scanner.remove_patch_from_favorites(entry))
            full_scan.assert_not_called()
        self.assertFalse(self.scanner.favorites_index.is_favorited(entry["stable_key"]))

    def test_legacy_liked_hidden_from_subfolders(self) -> None:
        liked = self.qa / "Liked"
        liked.mkdir()
        (liked / "Solo.fxp").write_bytes(b"x")
        self._touch("Lead/Other.fxp")
        self.scanner.scan_patches()
        label = favorites_display_name()
        subs = self.scanner.get_subfolders(label, ())
        self.assertNotIn("Liked", subs)


if __name__ == "__main__":
    unittest.main()
