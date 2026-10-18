"""Tests for PatchScanner path identity and folder tree."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from patch_browser.favorites_index import FavoritesIndex
from patch_browser.patch_scanner import (
    PatchScanner,
    favorites_display_name,
    favorites_folder_matches,
)


class PatchScannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.factory = self.root / "patches_factory"
        self.factory.mkdir()
        self.scanner = PatchScanner([self.factory])

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _touch(self, rel: str) -> Path:
        path = self.factory / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fxp")
        return path

    def test_same_name_different_subfolders_both_indexed(self) -> None:
        self._touch("Bass/SubA/Lead 1.fxp")
        self._touch("Bass/SubB/Lead 1.fxp")
        self.scanner.scan_patches()
        bass = self.scanner.get_patches_in_category("Bass")
        self.assertEqual(len(bass), 2)
        keys = {p["stable_key"] for p in bass}
        self.assertEqual(
            keys,
            {"factory:Bass/SubA/Lead 1", "factory:Bass/SubB/Lead 1"},
        )

    def test_stable_key_lookup(self) -> None:
        path = self._touch("Piano/Grand.fxp")
        self.scanner.scan_patches()
        entry = self.scanner.get_patch_by_path(path)
        self.assertIsNotNone(entry)
        assert entry is not None
        by_key = self.scanner.get_patch_by_stable_key(entry["stable_key"])
        self.assertEqual(by_key["path"], str(path.resolve()))

    def test_folder_tree_subfolders(self) -> None:
        self._touch("Bass/Root.fxp")
        self._touch("Bass/Sub/Deep.fxp")
        self.scanner.scan_patches()
        self.assertEqual(self.scanner.get_subfolders("Bass"), ["Sub"])
        root_patches = self.scanner.get_patches_in_folder("Bass")
        self.assertEqual([p["name"] for p in root_patches], ["Root"])
        sub_patches = self.scanner.get_patches_in_folder("Bass", ("Sub",))
        self.assertEqual([p["name"] for p in sub_patches], ["Deep"])

    def test_favorites_display_name_idempotent(self) -> None:
        self.assertEqual(favorites_display_name("Quick Access"), "!Quick Access")
        self.assertEqual(favorites_display_name("!Quick Access"), "!Quick Access")

    def test_favorites_folder_matches_the_virtual_label_only(self) -> None:
        self.assertTrue(favorites_folder_matches("!quick access"))
        self.assertTrue(favorites_folder_matches("!Quick Access"))
        # the folder of that name on disk is an ordinary category
        self.assertFalse(favorites_folder_matches("Quick Access"))

    def test_quick_access_category_and_nested_scan(self) -> None:
        qa = self.root / "patches_factory"
        user = self.root / "user_patches"
        user.mkdir()
        qa_dir = user / "Quick Access"
        qa_dir.mkdir()
        nested = qa_dir / "Gig A"
        nested.mkdir()
        (nested / "Pad.fxp").write_bytes(b"x")
        (qa_dir / "Solo.fxp").write_bytes(b"x")

        scanner = PatchScanner([user])
        scanner.favorites_index = FavoritesIndex(self.root / "fav.json")
        scanner.scan_patches()
        label = favorites_display_name()
        self.assertIn(label, scanner.get_categories())
        # patches saved into that folder are user patches, favorited on scan
        patches = scanner.get_patches_in_category(label)
        self.assertEqual(len(patches), 2)
        self.assertEqual(scanner.get_subfolders(label), ["Gig A"])
        self.assertIn("Quick Access", scanner.get_categories())

    def test_quick_scan_category_recursive(self) -> None:
        sub = self.factory / "Keys" / "Electric"
        sub.mkdir(parents=True)
        (self.factory / "Keys" / "Acoustic.fxp").write_bytes(b"x")
        (sub / "Rhodes.fxp").write_bytes(b"x")
        quick = self.scanner.quick_scan_category(self.factory / "Keys")
        names = sorted(p["name"] for p in quick)
        self.assertEqual(names, ["Acoustic", "Rhodes"])
        self.assertTrue(all("stable_key" in p for p in quick))

    def test_root_level_patch_is_uncategorized(self) -> None:
        self._touch("Loose.fxp")
        self.scanner.scan_patches()
        patches = self.scanner.get_patches_in_category("Uncategorized")
        self.assertEqual(len(patches), 1)
        self.assertEqual(patches[0]["name"], "Loose")

    def test_appliance_state_backups_are_not_patches(self) -> None:
        self._touch("Keys/Real.fxp")
        self._touch("appliance-state/captures/2026-10-01/home/favs/Backup.fxp")
        self._touch("Keys/appliance-state/Nested.fxp")
        self.scanner.scan_patches()
        names = sorted(p["name"] for ps in self.scanner.patches.values() for p in ps)
        self.assertEqual(names, ["Real"])
        quick = self.scanner.quick_scan_category(self.factory / "Keys")
        self.assertEqual([p["name"] for p in quick], ["Real"])

    def test_tutorials_are_not_patches(self) -> None:
        self._touch("Keys/Real.fxp")
        self._touch("Tutorials/Formula Modulator/02 Interacting With LFO Parameters.fxp")
        self.scanner.scan_patches()
        names = sorted(p["name"] for ps in self.scanner.patches.values() for p in ps)
        self.assertEqual(names, ["Real"])

    def test_library_dirs_skip_state_captures(self) -> None:
        import os
        from patch_browser.patch_scanner import resolve_library_patch_dirs

        repo = self.root / "personal"
        for rel in (
            "assets/patches/patches_factory/A.fxp",
            "assets/cc0-pack/B.fxp",
            "assets/appliance-state/captures/x/C.fxp",
            "assets/state/D.fxp",
        ):
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (repo / rel).write_bytes(b"x")
        old = os.environ.get("MPE_PERSONAL_REPO")
        os.environ["MPE_PERSONAL_REPO"] = str(repo)
        try:
            names = sorted(p.name for p in resolve_library_patch_dirs(self.root))
        finally:
            if old is None:
                os.environ.pop("MPE_PERSONAL_REPO")
            else:
                os.environ["MPE_PERSONAL_REPO"] = old
        self.assertEqual(names, ["cc0-pack", "patches_factory"])

    def test_resolve_library_patch_dirs_finds_sibling_repo(self) -> None:
        from patch_browser.patch_scanner import resolve_library_patch_dirs

        module_repo = Path(__file__).resolve().parents[1]
        personal = module_repo.parent / "MPE-Library"
        if not personal.is_dir():
            self.skipTest("MPE-Library sibling repo not present")
        dirs = resolve_library_patch_dirs(module_repo)
        self.assertGreaterEqual(len(dirs), 3)
        self.assertTrue(any(p.name == "patches_factory" for p in dirs))


if __name__ == "__main__":
    unittest.main()
