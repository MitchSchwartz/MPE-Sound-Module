"""Favorites index (v2, pointers) and the scanner's favorites category."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from patch_browser.favorites_index import (
    DEFAULT_FAVORITES_FOLDER,
    FavoritesIndex,
    LEGACY_LIKED_FOLDER,
    normalize_key,
)
from patch_browser.patch_scanner import PatchScanner, favorites_display_name, favorites_folder_matches


def tree_digest(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


class FavoritesIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "fav.json"

    def test_add_remove_roundtrip_holds_no_path(self) -> None:
        index = FavoritesIndex(self.path)
        index.add("factory:Bass/Lead", folder=DEFAULT_FAVORITES_FOLDER)
        index.save()
        raw = json.loads(self.path.read_text())
        self.assertEqual(raw["version"], 2)
        self.assertEqual(set(raw["entries"]["factory:Bass/Lead"]), {"folder", "added_at"})
        reloaded = FavoritesIndex(self.path)
        self.assertTrue(reloaded.is_favorited("factory:Bass/Lead"))
        self.assertEqual(reloaded.get_entry("factory:Bass/Lead")["folder"], DEFAULT_FAVORITES_FOLDER)
        self.assertIsNotNone(reloaded.remove("factory:Bass/Lead"))
        self.assertFalse(reloaded.is_favorited("factory:Bass/Lead"))

    def test_order_and_derived_from_are_kept(self) -> None:
        index = FavoritesIndex(self.path)
        index.add("user:Mine/X", order=3, derived_from="factory:Bass/X")
        index.save()
        entry = FavoritesIndex(self.path).get_entry("user:Mine/X")
        self.assertEqual((entry["order"], entry["derived_from"]), (3, "factory:Bass/X"))

    def test_folders_are_names_only(self) -> None:
        index = FavoritesIndex(self.path)
        index.create_folder("Gig A")
        self.assertEqual(index.folders, ["Gig A"])
        index.delete_folder("Gig A")
        self.assertEqual(index.folders, [])

    def test_a_folder_with_favorites_cannot_be_deleted(self) -> None:
        index = FavoritesIndex(self.path)
        index.add("factory:A/B", folder="Gig")
        with self.assertRaises(ValueError):
            index.delete_folder("Gig")
        index.add("factory:A/C", folder="Gig/Deep")
        with self.assertRaises(ValueError):
            index.delete_folder("Gig")

    def test_move_edits_only_the_entry(self) -> None:
        index = FavoritesIndex(self.path)
        index.add("factory:A/B")
        self.assertTrue(index.move("factory:A/B", "Gig"))
        self.assertEqual(index.folder_for("factory:A/B"), "Gig")
        self.assertIn("Gig", index.folders)
        self.assertFalse(index.move("factory:No/Such", "Gig"))

    def test_rename_folder_renames_entries_and_nested_names(self) -> None:
        index = FavoritesIndex(self.path)
        index.add("factory:A/B", folder="Gig")
        index.add("factory:A/C", folder="Gig/Deep")
        index.rename_folder("Gig", "Show")
        self.assertEqual(index.folder_for("factory:A/B"), "Show")
        self.assertEqual(index.folder_for("factory:A/C"), "Show/Deep")
        self.assertIn("Show/Deep", index.folders)
        self.assertNotIn("Gig", index.folders)
        index.create_folder("Other")
        with self.assertRaises(ValueError):
            index.rename_folder("Show", "Other")
        with self.assertRaises(ValueError):
            index.rename_folder("Missing", "X")

    def test_liked_folder_is_protected(self) -> None:
        index = FavoritesIndex(self.path)
        with self.assertRaises(ValueError):
            index.delete_folder(LEGACY_LIKED_FOLDER)
        with self.assertRaises(ValueError):
            index.rename_folder(LEGACY_LIKED_FOLDER, "X")

    def test_liked_entries_load_at_the_root(self) -> None:
        self.path.write_text(json.dumps({
            "version": 2, "folders": ["Liked", "Liked/Sub"],
            "entries": {"factory:A/B": {"folder": "Liked"}, "factory:A/C": {"folder": "Liked/Sub"}},
        }))
        index = FavoritesIndex(self.path)
        self.assertEqual(index.folder_for("factory:A/B"), None)  # root
        self.assertEqual(index.get_entry("factory:A/B")["folder"], "")
        self.assertEqual(index.get_entry("factory:A/C")["folder"], "Sub")
        self.assertNotIn("Liked", index.folders)
        self.assertNotIn("Liked/Sub", index.folders)

    def test_legacy_key_shapes_are_normalized_on_load(self) -> None:
        self.assertEqual(normalize_key("thirdparty:patches_3rdparty/Dan/Chime"), "thirdparty:Dan/Chime")
        self.assertEqual(normalize_key("thirdparty:patches_3rdparty/"), "thirdparty:patches_3rdparty/")
        self.assertEqual(normalize_key("factory:Bass/Sub"), "factory:Bass/Sub")
        self.path.write_text(json.dumps({"version": 2, "folders": [], "entries": {
            "thirdparty:patches_3rdparty/Dan/Chime": {"folder": ""},
        }}))
        index = FavoritesIndex(self.path)
        self.assertTrue(index.is_favorited("thirdparty:Dan/Chime"))
        self.assertFalse(index.is_favorited("thirdparty:patches_3rdparty/Dan/Chime"))

    def test_version_1_loads_as_pointers_and_stays_version_1(self) -> None:
        self.path.write_text(json.dumps({"version": 1, "folders": ["Gig"], "entries": {
            "factory:Bass/Acid": {"folder": "Gig", "dest_path": "/x/Quick Access/Gig/Acid.fxp", "added_at": "t"},
        }}))
        index = FavoritesIndex(self.path)
        self.assertTrue(index.legacy)
        self.assertTrue(index.is_favorited("factory:Bass/Acid"))
        index.add("factory:Bass/New")
        index.save()
        raw = json.loads(self.path.read_text())
        self.assertEqual(raw["version"], 1)
        self.assertIn("dest_path", raw["entries"]["factory:Bass/Acid"])  # untouched until migrated
        index.upgrade()
        index.save()
        raw = json.loads(self.path.read_text())
        self.assertEqual(raw["version"], 2)
        self.assertNotIn("dest_path", raw["entries"]["factory:Bass/Acid"])

    def test_a_version_2_file_drops_a_stray_dest_path(self) -> None:
        self.path.write_text(json.dumps({"version": 2, "folders": [], "entries": {
            "factory:A/B": {"folder": "", "dest_path": "/x"}}}))
        self.assertNotIn("dest_path", FavoritesIndex(self.path).get_entry("factory:A/B"))

    def test_imported_survives_a_save(self) -> None:
        index = FavoritesIndex(self.path)
        index.mark_imported("user:Quick Access/Pc")
        index.save()
        self.assertTrue(FavoritesIndex(self.path).was_imported("user:Quick Access/Pc"))
        self.assertFalse(FavoritesIndex(self.path).was_imported("user:Quick Access/Other"))

    def test_is_favorited_handles_none(self) -> None:
        index = FavoritesIndex(self.path)
        self.assertFalse(index.is_favorited(None))
        self.assertFalse(index.is_favorited(""))


class ScannerCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.factory = self.root / "patches_factory"
        self.patches = self.root / "Patches"
        self.patches.mkdir(parents=True)
        self.factory.mkdir()
        self.qa = self.patches / "Quick Access"
        self._env_patch = mock.patch.dict(
            "os.environ",
            {"MPE_SURGE_DOCS": str(self.root), "MPE_FAVORITES_NAME": "Quick Access"},
            clear=False,
        )
        self._env_patch.start()
        self.index_path = self.root / "fav.json"
        self.scanner = PatchScanner([self.factory, self.patches], last_patch_file=self.root / "last.json")
        self.scanner.favorites_index = FavoritesIndex(self.index_path)

    def tearDown(self) -> None:
        self._env_patch.stop()
        self._tmp.cleanup()

    def _touch(self, rel: str, *, root: Path | None = None, data: bytes = b"fxp") -> Path:
        path = (root or self.factory) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def entry(self, source: Path) -> dict:
        entry = self.scanner.get_patch_by_path(source)
        assert entry is not None
        return entry

    def fav_rows(self) -> list[dict]:
        return self.scanner.get_patches_in_category(favorites_display_name())


class PatchScannerFavoritesTests(ScannerCase):
    def test_add_writes_an_index_entry_and_copies_nothing(self) -> None:
        source = self._touch("Bass/Acid.fxp")
        self.scanner.scan_patches()
        before = tree_digest(self.root)
        entry = self.entry(source)
        self.assertTrue(self.scanner.add_patch_to_favorites(entry))
        after = tree_digest(self.root)
        self.assertEqual(
            {k: v for k, v in after.items() if k != "fav.json"},
            {k: v for k, v in before.items() if k != "fav.json"},
        )
        self.assertFalse(self.qa.exists())
        self.assertTrue(self.scanner.favorites_index.is_favorited(entry["stable_key"]))
        self.assertEqual(self.scanner.favorites_index.get_entry(entry["stable_key"])["folder"], "")
        self.assertTrue(self.index_path.exists())

    def test_a_row_is_a_view_of_the_source(self) -> None:
        source = self._touch("Bass/Acid.fxp")
        self.scanner.scan_patches()
        entry = self.entry(source)
        self.scanner.add_patch_to_favorites(entry, folder="Gig")
        (row,) = self.scanner.get_patches_in_folder(favorites_display_name(), ("Gig",))
        self.assertEqual(row["stable_key"], entry["stable_key"])
        self.assertEqual(row["path"], entry["path"])
        self.assertEqual(row["category"], favorites_display_name())
        self.assertEqual(row["inner_segments"], ("Gig",))
        self.assertFalse(row["missing"])
        self.assertIsNot(row, entry)
        # views never become the canonical row
        self.assertIs(self.scanner.get_patch_by_stable_key(entry["stable_key"]), entry)
        self.assertIs(self.scanner.get_patch_by_path(source), entry)
        self.assertEqual(entry["category"], "Bass")

    def test_rescan_rebuilds_only_the_favorites_category(self) -> None:
        self._touch("Piano/Grand.fxp")
        self.scanner.scan_patches()
        before_count = len(self.scanner.get_patches_in_category("Piano"))
        with mock.patch.object(self.scanner, "scan_patches") as full_scan:
            self.scanner.add_patch_to_favorites(self.entry(self.factory / "Piano/Grand.fxp"))
            full_scan.assert_not_called()
        self.assertTrue(any(p["name"] == "Grand" for p in self.fav_rows()))
        self.assertEqual(len(self.scanner.get_patches_in_category("Piano")), before_count)

    def test_the_favorites_category_is_listed_first(self) -> None:
        self._touch("Aaa/One.fxp")
        self.scanner.scan_patches()
        self.assertEqual(self.scanner.get_categories()[0], favorites_display_name())
        self.scanner.add_patch_to_favorites(self.entry(self.factory / "Aaa/One.fxp"))
        self.assertEqual(self.scanner.get_categories()[0], favorites_display_name())

    def test_add_folder_adds_entries_for_the_subtree(self) -> None:
        self._touch("Bass/Acid.fxp")
        self._touch("Bass/Sub/Wub.fxp")
        self.scanner.scan_patches()
        patches = self.scanner.get_patches_in_folder("Bass", ("Sub",))
        added, skipped = self.scanner.add_patches_to_favorites(patches, folder="Bass/Sub")
        self.assertEqual((added, skipped), (1, 0))
        self.assertFalse(self.qa.exists())
        self.assertEqual(
            [p["name"] for p in self.scanner.get_patches_in_folder(favorites_display_name(), ("Bass", "Sub"))],
            ["Wub"],
        )
        again = self.scanner.add_patches_to_favorites(patches, folder="Bass/Sub")
        self.assertEqual(again, (0, 1))

    def test_bulk_add_backs_up_only_the_index(self) -> None:
        self._touch("Bass/Acid.fxp")
        self.scanner.scan_patches()
        self.scanner.favorites_index.add("factory:Old/One")
        self.scanner.favorites_index.save()
        self.scanner.add_patches_to_favorites([self.entry(self.factory / "Bass/Acid.fxp")])
        backups = list((self.root / ".patch_browser_favorites_backups").glob("*pre-bulk-add"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(sorted(p.name for p in backups[0].iterdir()), ["README.txt", "fav.json"])
        self.assertNotIn("factory:Bass/Acid", json.loads((backups[0] / "fav.json").read_text())["entries"])

    def test_remove_edits_only_the_index_and_never_scans(self) -> None:
        source = self._touch("Lead/Solo.fxp")
        self.scanner.scan_patches()
        entry = self.entry(source)
        self.scanner.add_patch_to_favorites(entry)
        before = tree_digest(self.root)
        with mock.patch.object(self.scanner, "scan_patches") as full_scan:
            self.assertTrue(self.scanner.remove_patch_from_favorites(entry))
            full_scan.assert_not_called()
        self.assertFalse(self.scanner.favorites_index.is_favorited(entry["stable_key"]))
        self.assertEqual(self.fav_rows(), [])
        self.assertTrue(source.exists())
        self.assertEqual({k: v for k, v in tree_digest(self.root).items() if k != "fav.json"},
                         {k: v for k, v in before.items() if k != "fav.json"})
        self.assertFalse(self.scanner.remove_patch_from_favorites(entry))  # already gone

    def test_remove_through_the_view_row(self) -> None:
        source = self._touch("Lead/Solo.fxp")
        self.scanner.scan_patches()
        self.scanner.add_patch_to_favorites(self.entry(source))
        (row,) = self.fav_rows()
        self.assertTrue(self.scanner.remove_patch_from_favorites(row))
        self.assertEqual(self.fav_rows(), [])

    def test_bulk_remove(self) -> None:
        a = self._touch("Lead/A.fxp")
        b = self._touch("Lead/B.fxp")
        self.scanner.scan_patches()
        self.scanner.add_patches_to_favorites([self.entry(a), self.entry(b)])
        self.assertEqual(self.scanner.remove_patches_from_favorites_bulk(self.fav_rows()), 2)
        self.assertEqual(self.fav_rows(), [])
        self.assertEqual(self.scanner.remove_patches_from_favorites_bulk(self.fav_rows()), 0)

    def test_move_changes_the_folder_only(self) -> None:
        source = self._touch("Lead/Solo.fxp")
        self.scanner.scan_patches()
        entry = self.entry(source)
        self.scanner.add_patch_to_favorites(entry)
        self.assertTrue(self.scanner.move_patch_to_favorites_folder(entry, "Gig"))
        self.assertEqual(self.scanner.favorites_index.folder_for(entry["stable_key"]), "Gig")
        self.assertEqual(self.scanner.get_subfolders(favorites_display_name()), ["Gig"])
        self.assertEqual(
            [p["name"] for p in self.scanner.get_patches_in_folder(favorites_display_name(), ("Gig",))],
            ["Solo"],
        )
        self.assertTrue(source.exists())
        self.assertFalse(self.scanner.move_patch_to_favorites_folder({"stable_key": "factory:No/Such"}, "Gig"))

    def test_an_empty_index_folder_shows_up(self) -> None:
        self._touch("Lead/Solo.fxp")
        self.scanner.favorites_index.create_folder("Gig")
        self.scanner.favorites_index.create_folder("Gig/Deep")
        self.scanner.scan_patches()
        label = favorites_display_name()
        self.assertEqual(self.scanner.get_subfolders(label), ["Gig"])
        self.assertEqual(self.scanner.get_subfolders(label, ("Gig",)), ["Deep"])

    def test_rows_are_a_to_z_unless_ordered(self) -> None:
        for name in ("Zed", "Alpha", "Mid", "Beta"):
            self._touch(f"Lead/{name}.fxp")
        self.scanner.scan_patches()
        index = self.scanner.favorites_index
        for name in ("Zed", "Alpha", "Mid", "Beta"):
            index.add(f"factory:Lead/{name}")
        self.scanner.rescan_favorites_category()
        self.assertEqual([p["name"] for p in self.fav_rows()], ["Alpha", "Beta", "Mid", "Zed"])
        index.set_order("factory:Lead/Zed", 1)
        index.set_order("factory:Lead/Mid", 2)
        self.scanner.rescan_favorites_category()
        self.assertEqual([p["name"] for p in self.fav_rows()], ["Zed", "Mid", "Alpha", "Beta"])

    def test_a_missing_source_is_a_greyed_row_that_stays(self) -> None:
        self._touch("Lead/Solo.fxp")
        self.scanner.favorites_index.add("factory:Gone/Ghost", folder="Gig")
        self.scanner.favorites_index.save()
        self.scanner.scan_patches()
        (row,) = self.scanner.get_patches_in_folder(favorites_display_name(), ("Gig",))
        self.assertTrue(row["missing"])
        self.assertEqual(row["name"], "Ghost")
        self.assertEqual(row["path"], "")
        self.assertEqual(row["stable_key"], "factory:Gone/Ghost")
        self.assertTrue(self.scanner.favorites_index.is_favorited("factory:Gone/Ghost"))
        self.assertEqual(self.scanner.favorite_sections([row]), {"factory:Gone/Ghost": "other"})
        # the only action is remove, and it works
        self.assertFalse(self.scanner.add_patch_to_favorites(row))
        self.assertTrue(self.scanner.remove_patch_from_favorites(row))
        self.assertEqual(self.scanner.get_patches_in_folder(favorites_display_name(), ("Gig",)), [])

    def test_a_returning_source_stops_being_missing(self) -> None:
        self.scanner.favorites_index.add("factory:Lead/Back")
        self.scanner.scan_patches()
        self.assertTrue(self.fav_rows()[0]["missing"])
        self._touch("Lead/Back.fxp")
        self.scanner.scan_patches()
        self.assertFalse(self.fav_rows()[0]["missing"])

    def test_the_heart_is_a_lookup(self) -> None:
        source = self._touch("Lead/Solo.fxp")
        self.scanner.scan_patches()
        entry = self.entry(source)
        self.assertFalse(self.scanner.is_patch_in_favorites(entry))
        self.scanner.add_patch_to_favorites(entry)
        self.assertTrue(self.scanner.is_patch_in_favorites(entry))
        self.assertTrue(self.scanner.is_patch_in_favorites(self.fav_rows()[0]))
        self.assertFalse(self.scanner.is_patch_in_favorites(None))
        self.assertFalse(self.scanner.is_patch_in_favorites({}))

    def test_favorites_follow_the_library_file(self) -> None:
        """A pointer plays whatever the library holds now."""
        source = self._touch("Lead/Solo.fxp", data=b"v1")
        self.scanner.scan_patches()
        self.scanner.add_patch_to_favorites(self.entry(source))
        source.write_bytes(b"v2")
        self.scanner.scan_patches()
        self.assertEqual(Path(self.fav_rows()[0]["path"]).read_bytes(), b"v2")


class PcWorkflow(ScannerCase):
    def test_a_patch_saved_into_the_favorites_folder_is_favorited_on_scan(self) -> None:
        self._touch("Pc Saved.fxp", root=self.qa)
        self._touch("Gig/Deep Pad.fxp", root=self.qa)
        self.scanner.scan_patches()
        index = self.scanner.favorites_index
        self.assertTrue(index.is_favorited("user:Quick Access/Pc Saved"))
        self.assertEqual(index.folder_for("user:Quick Access/Gig/Deep Pad"), "Gig")
        self.assertTrue(json.loads(self.index_path.read_text())["entries"])  # saved
        # a user patch like any other: a real file, its own key, not the favorites category
        row = self.scanner.get_patch_by_stable_key("user:Quick Access/Pc Saved")
        self.assertEqual(row["category"], "Quick Access")
        self.assertFalse(favorites_folder_matches(row["category"]))
        names = sorted(p["name"] for p in self.fav_rows())
        self.assertEqual(names, ["Deep Pad", "Pc Saved"])
        self.assertEqual(self.scanner.get_subfolders(favorites_display_name()), ["Gig"])

    def test_removing_it_is_not_undone_by_the_next_scan(self) -> None:
        self._touch("Pc Saved.fxp", root=self.qa)
        self.scanner.scan_patches()
        self.scanner.remove_patch_from_favorites({"stable_key": "user:Quick Access/Pc Saved"})
        self.scanner.scan_patches()
        self.assertFalse(self.scanner.favorites_index.is_favorited("user:Quick Access/Pc Saved"))
        self.assertTrue((self.qa / "Pc Saved.fxp").exists())

    def test_a_second_scan_adds_nothing(self) -> None:
        self._touch("Pc Saved.fxp", root=self.qa)
        self.scanner.scan_patches()
        before = self.index_path.read_text()
        self.scanner.scan_patches()
        self.assertEqual(self.index_path.read_text(), before)

    def test_a_patch_added_to_the_folder_later_is_picked_up(self) -> None:
        self._touch("First.fxp", root=self.qa)
        self.scanner.scan_patches()
        self._touch("Second.fxp", root=self.qa)
        self.scanner.scan_patches()
        self.assertTrue(self.scanner.favorites_index.is_favorited("user:Quick Access/Second"))

    def test_other_user_folders_are_not_favorited(self) -> None:
        self._touch("Mine/Thing.fxp", root=self.patches)
        self.scanner.scan_patches()
        self.assertEqual(self.fav_rows(), [])

    def test_a_legacy_index_does_not_import_the_old_copies(self) -> None:
        self.index_path.write_text(json.dumps({"version": 1, "folders": [], "entries": {
            "factory:Bass/Acid": {"folder": "", "dest_path": "/x/Quick Access/Acid.fxp"}}}))
        self.scanner.favorites_index = FavoritesIndex(self.index_path)
        self._touch("Bass/Acid.fxp")
        self._touch("Acid.fxp", root=self.qa)  # the old copy
        self.scanner.scan_patches()
        # one favorite, shown through its copy (see LegacyIndexWindowTests); the copy is not
        # imported as a second one and the index stays version 1 with its one entry
        self.assertEqual([p["stable_key"] for p in self.fav_rows()], ["user:Quick Access/Acid"])
        self.assertEqual(list(self.scanner.favorites_index.entries), ["factory:Bass/Acid"])
        self.assertEqual(json.loads(self.index_path.read_text())["version"], 1)

    def test_import_skips_a_folder_only_matching_by_the_bang(self) -> None:
        """The virtual label has a leading !; the folder on disk does not."""
        self.assertTrue(favorites_folder_matches("!Quick Access"))
        self.assertFalse(favorites_folder_matches("Quick Access"))
        self.assertFalse(favorites_folder_matches("Quick Access (old)"))


class Persistence(ScannerCase):
    def test_a_fresh_scanner_reads_what_was_saved(self) -> None:
        source = self._touch("Lead/Solo.fxp")
        self.scanner.scan_patches()
        self.scanner.add_patch_to_favorites(self.entry(source), folder="Gig")
        other = PatchScanner([self.factory, self.patches], last_patch_file=self.root / "last2.json")
        other.favorites_index = FavoritesIndex(self.index_path)
        other.scan_patches()
        self.assertEqual(
            [p["name"] for p in other.get_patches_in_folder(favorites_display_name(), ("Gig",))], ["Solo"]
        )


class LegacyIndexWindowTests(ScannerCase):
    """New code on a version-1 index, before the migration has run (item 6)."""

    def setUp(self) -> None:
        super().setUp()
        self._touch("Bass/Acid.fxp")
        self._touch("Bass/Other.fxp", data=b"other")
        self.copy = self._touch("Quick Access/Acid.fxp", root=self.patches)  # tuned copy, v1 style
        self._touch("Quick Access/Kick.fxp", root=self.patches, data=b"kick")  # a copy no entry names
        self.index_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "folders": [],
                    "entries": {
                        "factory:Bass/Acid": {
                            "folder": "",
                            "dest_path": f"/anywhere/Quick Access/Acid.fxp",
                            "added_at": "t",
                        }
                    },
                }
            )
        )
        self.scanner.favorites_index = FavoritesIndex(self.index_path)
        self.assertTrue(self.scanner.favorites_index.legacy)
        self.scanner.scan_patches()

    def test_the_row_plays_the_copy_so_calibration_and_the_tuned_sound_stay(self) -> None:
        rows = self.fav_rows()
        self.assertEqual([r["stable_key"] for r in rows], ["user:Quick Access/Acid"])
        self.assertEqual(Path(rows[0]["path"]), self.copy)
        self.assertFalse(rows[0]["missing"])

    def test_heart_state_and_removal_resolve_through_the_alias(self) -> None:
        row = self.fav_rows()[0]
        self.assertTrue(self.scanner.is_patch_in_favorites(row))
        self.assertTrue(self.scanner.is_patch_in_favorites(self.entry(self.copy)))
        self.assertFalse(self.scanner.add_patch_to_favorites(row))  # already there
        self.assertTrue(self.scanner.remove_patch_from_favorites(row))
        self.assertEqual(list(self.scanner.favorites_index.entries), [])
        self.assertEqual(self.fav_rows(), [])

    def test_hearting_an_unindexed_copy_files_it_under_the_copys_key_for_the_migration(self) -> None:
        kick = self.entry(self.patches / "Quick Access" / "Kick.fxp")
        self.assertTrue(self.scanner.add_patch_to_favorites(kick))
        self.assertIn("user:Quick Access/Kick", self.scanner.favorites_index.entries)
        self.assertTrue(self.scanner.favorites_index.legacy)  # still saved as version 1
        self.assertEqual(json.loads(self.index_path.read_text())["version"], 1)

    def test_a_version_2_index_does_not_alias(self) -> None:
        self.scanner.favorites_index.upgrade()
        self.scanner.rescan_favorites_category()
        self.assertEqual([r["stable_key"] for r in self.fav_rows()], ["factory:Bass/Acid"])


if __name__ == "__main__":
    unittest.main()