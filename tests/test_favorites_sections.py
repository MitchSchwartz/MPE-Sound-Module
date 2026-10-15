"""Favorites grouped by instrument: the group authority, header rows, the index
to patch mapping, the restored selection, and the jump strip (2026-10-13)."""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.fake_pygame import install_fake_pygame

install_fake_pygame()

from patch_browser.favorites_index import FavoritesIndex  # noqa: E402
from patch_browser.favorites_sections import (  # noqa: E402
    HEADER_KIND,
    OTHER_GROUP,
    favorite_group,
    section_label,
    section_starts,
    section_tag,
    sectioned_entries,
)
from patch_browser.geometry import Rect  # noqa: E402
from patch_browser.jump_strip import (  # noqa: E402
    scrub_fraction,
    section_cell_at,
    section_cell_bounds,
    thumb_height,
    thumb_top,
)
from patch_browser.patch_metadata import INSTRUMENT_DISPLAY_ORDER, PatchMetadataIndex  # noqa: E402
from patch_browser.patch_scanner import PatchScanner, favorites_display_name  # noqa: E402
from patch_browser.scroll_widgets import ScrollList  # noqa: E402
from patch_browser.touch_browser_app import TouchPatchBrowser  # noqa: E402
from patch_browser.touch_ui_constants import (  # noqa: E402
    JUMP_STRIP_W,
    NAV_SECTION_HEADER_H,
    PATCHES_ROW_HEIGHT,
)
from patch_browser.touch_ui_enums import LeftNavMode  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
QA = "Quick Access"


# --- the group authority ------------------------------------------------------


class FavoriteGroupAuthority(unittest.TestCase):
    def test_the_users_choice_wins_even_other(self) -> None:
        self.assertEqual(favorite_group(user_instrument="pad", primary="bass"), "pad")
        self.assertEqual(favorite_group(user_instrument="other", primary="bass"), "other")

    def test_the_patchs_own_instrument_answers_otherwise(self) -> None:
        self.assertEqual(favorite_group(user_instrument=None, primary="bass"), "bass")

    def test_no_signal_is_other(self) -> None:
        for own in (None, "", "other", "kazoo"):
            with self.subTest(own=own):
                self.assertEqual(favorite_group(user_instrument=None, primary=own), OTHER_GROUP)

    def test_an_unknown_user_value_cannot_make_a_section(self) -> None:
        self.assertEqual(favorite_group(user_instrument="kazoo", primary="bass"), OTHER_GROUP)

    def test_labels_and_strip_tags_are_distinct(self) -> None:
        tags = [section_tag(g) for g in INSTRUMENT_DISPLAY_ORDER]
        self.assertEqual(len(set(tags)), len(tags), tags)
        self.assertTrue(all(len(t) <= 3 for t in tags), tags)
        self.assertEqual(section_label("fx"), "FX")
        self.assertEqual(section_label("bass"), "Bass")

    def test_one_decision_site(self) -> None:
        """Only favorites_sections decides a group; only the scanner asks it.
        (The order's guard is in test_instrument_order.)"""
        definers, callers = [], []
        for path in sorted((REPO / "patch_browser").rglob("*.py")):
            text = path.read_text(errors="replace")
            rel = path.relative_to(REPO).as_posix()
            if re.search(r"^def favorite_group\(", text, re.M):
                definers.append(rel)
            if re.search(r"(?<!def )\bfavorite_group\(", text):
                callers.append(rel)
        self.assertEqual(definers, ["patch_browser/favorites_sections.py"])
        self.assertEqual(callers, ["patch_browser/patch_scanner.py"])


def _entry(name: str) -> dict:
    return {"kind": "patch", "patch": {"name": name, "path": f"/qa/{name}.fxp"}, "label": name}


class SectionedEntries(unittest.TestCase):
    GROUPS = {"Zed": "bass", "Alpha": "pad", "Mid": "bass", "Odd": "kazoo", "Beta": "keys", "Low": "other"}

    def _build(self) -> list[dict]:
        # Not alphabetical, so "kept the input order" and "sorted" differ.
        names = ["Beta", "Zed", "Odd", "Mid", "Alpha", "Low"]
        return sectioned_entries([_entry(n) for n in names], lambda p: self.GROUPS[p["name"]])

    def test_sections_a_to_z_other_last_and_order_kept_inside(self) -> None:
        rows = [(e["kind"], e.get("group") or e["patch"]["name"]) for e in self._build()]
        self.assertEqual(
            rows,
            [
                (HEADER_KIND, "bass"), ("patch", "Zed"), ("patch", "Mid"),
                (HEADER_KIND, "keys"), ("patch", "Beta"),
                (HEADER_KIND, "pad"), ("patch", "Alpha"),
                (HEADER_KIND, "other"), ("patch", "Odd"), ("patch", "Low"),
            ],
        )

    def test_header_counts_and_no_empty_sections(self) -> None:
        entries = self._build()
        counts = {e["group"]: e["count"] for e in entries if e["kind"] == HEADER_KIND}
        self.assertEqual(counts, {"keys": 1, "bass": 2, "pad": 1, "other": 2})
        self.assertEqual(section_starts(entries), [("bass", 0), ("keys", 3), ("pad", 5), ("other", 7)])

    def test_every_patch_appears_once(self) -> None:
        entries = self._build()
        names = [e["patch"]["name"] for e in entries if e["kind"] == "patch"]
        self.assertEqual(sorted(names), sorted(self.GROUPS))


# --- a real scanner over a temporary library ---------------------------------


LIBRARY = {
    "Basses": ["Attacky", "Deep Sub", "Reese Low"],
    "Keys": ["Rhodes Mk1", "Wurli Soft"],
    "Pads": ["Glass Choir", "Warm Cloud"],
    "Leads": ["Photon"],
    "Pack": [f"Pack Patch {i:02d}" for i in range(1, 31)],
}
FAVORITES = [("Basses", "Attacky"), ("Basses", "Deep Sub"), ("Keys", "Rhodes Mk1"),
             ("Pads", "Glass Choir"), ("Pads", "Warm Cloud"), ("Leads", "Photon"),
             ("Basses", "Reese Low"), ("Keys", "Wurli Soft")]
UNTRACED = ["Mystery Patch"]  # a favorite whose patch is not in the library


class _Library:
    def __init__(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.lib = root / "lib"
        for folder, names in LIBRARY.items():
            (self.lib / folder).mkdir(parents=True)
            for name in names:
                (self.lib / folder / f"{name}.fxp").write_bytes(b"")
        entries = {f"user:{folder}/{name}": {"folder": ""} for folder, name in FAVORITES}
        for name in UNTRACED:
            entries[f"user:Gone/{name}"] = {"folder": ""}
        index_path = root / "favorites.json"
        index_path.write_text(json.dumps({"version": 2, "folders": [], "entries": entries}))
        self.user_meta = root / "user-metadata.json"
        scanner = PatchScanner([self.lib], last_patch_file=root / "last.json")
        scanner.metadata_index = PatchMetadataIndex(
            baseline_path=root / "no-baseline.json", user_path=self.user_meta
        )
        scanner.favorites_index = FavoritesIndex(index_path)
        scanner.scan_patches()
        scanner.scan_complete.set()
        self.scanner = scanner

    def qa_patches(self) -> list[dict]:
        return self.scanner.get_patches_in_folder(favorites_display_name(), ())

    def close(self) -> None:
        self.tmp.cleanup()


class ScannerFavoriteSections(unittest.TestCase):
    def setUp(self) -> None:
        self.library = _Library()
        self.addCleanup(self.library.close)
        self.scanner = self.library.scanner

    def _groups(self) -> dict[str, str]:
        info = self.scanner.favorite_sections(self.library.qa_patches())
        return {key.rpartition("/")[2]: g for key, g in info.items()}

    def test_rows_take_the_patchs_instrument(self) -> None:
        groups = self._groups()
        self.assertEqual(groups["Attacky"], "bass")
        self.assertEqual(groups["Rhodes Mk1"], "keys")
        self.assertEqual(groups["Glass Choir"], "pad")
        self.assertEqual(groups["Photon"], "lead")

    def test_a_missing_patch_is_other(self) -> None:
        self.assertEqual(self._groups()["Mystery Patch"], OTHER_GROUP)

    def test_a_user_instrument_moves_it(self) -> None:
        row = next(p for p in self.library.qa_patches() if p["name"] == "Attacky")
        self.scanner.metadata_index.set_user_instrument(row["stable_key"], "pad", patch=row)
        self.assertEqual(self._groups()["Attacky"], "pad")

    def test_the_row_and_the_library_entry_agree_after_an_instrument_change(self) -> None:
        row = next(p for p in self.library.qa_patches() if p["name"] == "Attacky")
        index = self.scanner.metadata_index
        index.set_user_instrument(row["stable_key"], "pad", patch=row)
        canonical = self.scanner.get_patch_by_stable_key(row["stable_key"])
        index.enrich_patch(canonical)
        self.scanner.invalidate_favorite_cache()
        fresh = next(p for p in self.library.qa_patches() if p["name"] == "Attacky")
        self.assertEqual(fresh["instrument_primary"], "pad")
        self.assertEqual(fresh["instrument_primary"], canonical["instrument_primary"])


# --- the list widget with header rows ----------------------------------------


def _sectioned_list(kinds: list[str]) -> ScrollList:
    lst = ScrollList(Rect(0, 100, 200, 316), row_height=PATCHES_ROW_HEIGHT)
    lst.set_items(
        [f"row-{i}" for i in range(len(kinds))],
        preserve_scroll=False,
        row_heights=[NAV_SECTION_HEADER_H if k == "h" else PATCHES_ROW_HEIGHT for k in kinds],
        selectable=[k != "h" for k in kinds],
    )
    return lst


KINDS = ["h", "p", "p", "h", "p", "h", "p", "p", "p", "h"] + ["p"] * 12


class HeaderRows(unittest.TestCase):
    def test_header_rows_are_shorter(self) -> None:
        self.assertLess(NAV_SECTION_HEADER_H, PATCHES_ROW_HEIGHT)
        lst = _sectioned_list(KINDS)
        self.assertEqual(lst.row_extent(0), NAV_SECTION_HEADER_H)
        self.assertEqual(lst.row_extent(1), PATCHES_ROW_HEIGHT)

    def test_index_to_row_mapping_with_headers(self) -> None:
        lst = _sectioned_list(KINDS)
        expected_tops, top = [], 0
        for kind in KINDS:
            expected_tops.append(top)
            top += NAV_SECTION_HEADER_H if kind == "h" else PATCHES_ROW_HEIGHT
        for scroll in (0.0, 37.0, 200.0, lst.max_scroll_pixels()):
            lst.set_scroll_pixels(scroll)
            for index, kind in enumerate(KINDS):
                y = lst.rect.y + lst.padding + expected_tops[index] - int(lst.scroll_pixels)
                for dy in (1, (NAV_SECTION_HEADER_H if kind == "h" else PATCHES_ROW_HEIGHT) - 1):
                    py = y + dy
                    if not (lst.rect.y <= py < lst.rect.bottom):
                        continue
                    with self.subTest(scroll=scroll, index=index, dy=dy):
                        self.assertEqual(lst.item_at(50, py), None if kind == "h" else index)

    def test_header_tap_fires_nothing(self) -> None:
        lst = _sectioned_list(KINDS)
        y = lst.rect.y + lst.padding + 5
        lst.pointer_down((50, y))
        self.assertIsNone(lst.pressed_index)
        self.assertIsNone(lst.pointer_up((50, y)))
        self.assertIsNone(lst.take_tap_index())

    def test_patch_row_tap_still_fires(self) -> None:
        lst = _sectioned_list(KINDS)
        y = lst.rect.y + lst.padding + NAV_SECTION_HEADER_H + 10
        lst.pointer_down((50, y))
        self.assertEqual(lst.pointer_up((50, y)), 1)

    def test_max_scroll_shows_the_last_row_whole(self) -> None:
        lst = _sectioned_list(KINDS)
        inner = lst.rect.h - 2 * lst.padding
        self.assertEqual(lst.max_scroll_pixels(), lst.content_height() - inner)

    def test_scroll_to_index_brings_the_row_into_view(self) -> None:
        lst = _sectioned_list(KINDS)
        inner = lst.rect.h - 2 * lst.padding
        for index in (len(KINDS) - 1, 4, 0, 15):
            lst.scroll_to_index(index)
            with self.subTest(index=index):
                self.assertGreaterEqual(lst.row_top(index), lst.scroll_pixels)
                self.assertLessEqual(
                    lst.row_top(index) + lst.row_extent(index), lst.scroll_pixels + inner
                )

    def test_uniform_list_is_unchanged(self) -> None:
        lst = ScrollList(Rect(0, 0, 200, 316), row_height=58)
        lst.set_items([str(i) for i in range(20)], preserve_scroll=False)
        self.assertEqual(lst.max_scroll_pixels(), (20 - lst.visible_count()) * 58)
        self.assertEqual(lst.item_at(10, lst.padding + 58 * 3 + 2), 3)


# --- the browser, end to end with a real scanner ------------------------------


class _BrowserCase(unittest.TestCase):
    def setUp(self) -> None:
        self.library = _Library()
        self.addCleanup(self.library.close)
        loader = mock.Mock()
        loader.osc_enabled = False
        loader.normalization.is_globally_enabled.return_value = True
        loader.normalization.is_enabled.return_value = True
        loader.normalization.get_entry.return_value = None
        loader.normalization.get_raw_gain_db.return_value = None
        loader.normalization.list_missing.return_value = []
        surge = mock.Mock()
        surge.get_status_summary.return_value = {"status": "Running", "details": "ok", "can_restart": False}
        cpu = mock.Mock()
        cpu.snapshot.return_value = {"online": True, "percent": 12.5}
        backlight = mock.Mock()
        backlight.get_percent.return_value = 100
        screen = mock.Mock()
        screen.get_size.return_value = (800, 480)
        for patcher in (
            mock.patch("patch_browser.touch_browser_app.PatchScanner", return_value=self.library.scanner),
            mock.patch("patch_browser.touch_browser_app.PatchLoader", return_value=loader),
            mock.patch("patch_browser.touch_browser_app.SurgeMonitor", return_value=surge),
            mock.patch("patch_browser.touch_browser_app.SurgeCpuMonitor", return_value=cpu),
            mock.patch("patch_browser.touch_browser_app.BacklightController", return_value=backlight),
            mock.patch("patch_browser.touch_browser_app.evdev_bridge_enabled", return_value=False),
            mock.patch("patch_browser.touch_browser_app.acquire_browser_display", return_value=screen),
            mock.patch.object(TouchPatchBrowser, "_start_background_scan"),
            mock.patch.object(TouchPatchBrowser, "_wait_for_initial_scan"),
            mock.patch.object(TouchPatchBrowser, "_complete_boot_splash"),
            mock.patch.object(TouchPatchBrowser, "_paint_boot_splash_frame"),
            mock.patch.object(TouchPatchBrowser, "_start_evdev_touch_bridge"),
            mock.patch(
                "patch_browser.touch_browser_wifi_modal.wifi_settings_row_label",
                return_value="Wi-Fi",
            ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.browser = TouchPatchBrowser()
        self.browser._apply_scan_results()
        self.selected: list[dict] = []
        self.browser._select_patch = self.selected.append  # record, do not load

    def open_folder(self, name: str) -> None:
        self.browser._enter_folder(self.browser.categories.index(name))

    def open_favorites(self) -> None:
        self.open_folder(favorites_display_name())

    def entries(self) -> list[dict]:
        return self.browser._browse_nav_entries

    def row_y(self, index: int) -> int:
        nav = self.browser.nav_list
        nav.scroll_to_index(index)
        return nav.rect.y + nav.padding + nav.row_top(index) - int(nav.scroll_pixels) + 5

    def tap(self, pos: tuple[int, int]) -> None:
        b = self.browser
        b._handle_evdev_browser_touch("down", pos)
        b._handle_evdev_browser_touch("up", pos)

    def _drawn_rows(self) -> dict[str, list[tuple[str, int]]]:
        """Draw the list once; return each row's texts by font, with blit y.

        Keys: "md" (patch names, font_md) and "sm" (subtitles and header text,
        font_sm), each a list of (text, y).
        """
        b = self.browser
        drawn: dict[str, list[tuple[str, int]]] = {"md": [], "sm": []}
        surfaces: dict[int, tuple[str, str]] = {}

        def recorder(key, real):
            def render(text, aa, color):
                surf = real(text, aa, color)
                surfaces[id(surf)] = (key, text)
                return surf
            return render

        screen = mock.Mock()
        screen.get_clip.return_value = None

        def blit(surf, pos, *args, **kwargs):
            if id(surf) in surfaces:
                key, text = surfaces[id(surf)]
                drawn[key].append((text, pos[1]))

        screen.blit.side_effect = blit
        with mock.patch.object(b, "screen", screen), \
                mock.patch.object(b.font_md, "render", side_effect=recorder("md", b.font_md.render)), \
                mock.patch.object(b.font_sm, "render", side_effect=recorder("sm", b.font_sm.render)), \
                mock.patch("patch_browser.touch_browser_draw.pygame.draw"):
            b._draw_browse_patches_list()
        return drawn

    def patch_index(self, name: str) -> int:
        return next(
            i for i, e in enumerate(self.entries())
            if e["kind"] == "patch" and e["patch"]["name"] == name
        )


class FavoritesListInTheBrowser(_BrowserCase):
    def test_only_favorites_get_sections(self) -> None:
        self.open_favorites()
        kinds = [e["kind"] for e in self.entries()]
        self.assertEqual(kinds[0], HEADER_KIND)
        self.assertEqual(
            [g for g, _i in section_starts(self.entries())], ["bass", "keys", "lead", "pad", "other"]
        )
        # The jump strip lists the same sections in the same order.
        self.assertEqual(
            [g for g, _i in self.browser._jump_strip_sections()], ["bass", "keys", "lead", "pad", "other"]
        )
        self.open_folder("Basses")
        self.assertNotIn(HEADER_KIND, [e["kind"] for e in self.entries()])

    def test_headers_in_the_nav_list_are_short_and_not_selectable(self) -> None:
        self.open_favorites()
        nav = self.browser.nav_list
        for index, entry in enumerate(self.entries()):
            with self.subTest(index=index):
                if entry["kind"] == HEADER_KIND:
                    self.assertEqual(nav.row_extent(index), NAV_SECTION_HEADER_H)
                    self.assertFalse(nav.is_selectable(index))
                    y = self.row_y(index)
                    self.assertIsNone(nav.item_at(40, y))
                    nav.pointer_down((40, y))
                    self.assertIsNone(nav.pressed_index, "no pressed chrome on a header")
                    nav.cancel_active_pointer()
                else:
                    self.assertEqual(nav.row_extent(index), PATCHES_ROW_HEIGHT)
                    self.assertTrue(nav.is_selectable(index))

    def test_tapping_a_row_selects_that_patch_and_a_header_selects_nothing(self) -> None:
        self.open_favorites()
        for index, entry in enumerate(self.entries()):
            self.selected.clear()
            self.tap((40, self.row_y(index)))
            with self.subTest(index=index, kind=entry["kind"]):
                if entry["kind"] == HEADER_KIND:
                    self.assertEqual(self.selected, [])
                else:
                    self.assertEqual([p["name"] for p in self.selected], [entry["patch"]["name"]])

    def test_header_has_no_long_press_and_select_ignores_it(self) -> None:
        self.open_favorites()
        self.assertIsNone(self.browser._context_target_for_nav_index(0))
        self.browser._select_nav_index(0)
        self.assertEqual(self.selected, [])
        target = self.browser._context_target_for_nav_index(1)
        self.assertEqual(target.patch["name"], self.entries()[1]["patch"]["name"])

    def test_highlight_and_loaded_marker_point_at_the_right_row(self) -> None:
        self.open_favorites()
        qa = {p["name"]: p for p in self.library.qa_patches()}
        b = self.browser
        b.detail_patch = dict(qa["Warm Cloud"])
        b.loaded_patch_info = dict(qa["Photon"])
        b._refresh_lists()
        self.assertEqual(b.nav_list.highlight_index, self.patch_index("Warm Cloud"))
        self.assertEqual(b.nav_list.loaded_marker_index, self.patch_index("Photon"))
        self.assertEqual(self.entries()[b.nav_list.highlight_index]["patch"]["name"], "Warm Cloud")

    def test_restored_selection_is_scrolled_into_view(self) -> None:
        """Boot restores the last patch: _refresh_lists(scroll_to_selection=True)."""
        self.open_favorites()
        b = self.browser
        target = next(p for p in self.library.qa_patches() if p["name"] == "Mystery Patch")
        b.detail_patch = dict(target)
        b.loaded_patch_info = dict(target)
        b.nav_list.set_scroll_pixels(0)
        b._refresh_lists(scroll_to_selection=True)
        index = self.patch_index("Mystery Patch")
        nav = b.nav_list
        inner = nav.rect.h - 2 * nav.padding
        self.assertEqual(nav.highlight_index, index)
        self.assertGreaterEqual(nav.row_top(index), nav.scroll_pixels)
        self.assertLessEqual(nav.row_top(index) + nav.row_extent(index), nav.scroll_pixels + inner)

    def test_scroll_survives_a_refresh(self) -> None:
        self.open_favorites()
        nav = self.browser.nav_list
        nav.set_scroll_pixels(123)
        self.browser._refresh_lists()
        self.assertEqual(nav.scroll_pixels, 123)

    def test_sectioned_rows_draw_no_subtitle_and_centre_the_name(self) -> None:
        """Approved 2026-10-13: the header names the instrument, the row does not."""
        self.open_favorites()
        b = self.browser
        nav = b.nav_list
        header_texts = set()
        for entry in self.entries():
            if entry["kind"] == HEADER_KIND:
                header_texts |= {entry["label"].upper(), str(entry["count"])}
        drawn = self._drawn_rows()
        self.assertTrue(drawn["md"], "patch names were drawn")
        self.assertTrue(set(t for t, _y in drawn["sm"]) <= header_texts, drawn["sm"])
        # Row height unchanged; the name sits in the middle of it.
        for index, y, h in nav.visible_rows():
            entry = self.entries()[index]
            if entry["kind"] != "patch":
                continue
            self.assertEqual(h, PATCHES_ROW_HEIGHT)
            name_y = [ty for text, ty in drawn["md"] if text == entry["patch"]["name"]]
            text_h = b.font_md.render(entry["patch"]["name"], True, (0, 0, 0)).get_height()
            self.assertEqual(name_y, [y + (h - 2 - text_h) // 2], entry["patch"]["name"])

    def test_unsectioned_folders_keep_their_subtitle(self) -> None:
        self.open_folder("Basses")
        drawn = self._drawn_rows()
        subtitles = [t for t, _y in drawn["sm"]]
        self.assertEqual(subtitles.count("Bass"), len(LIBRARY["Basses"]), subtitles)

    def test_draw_smoke(self) -> None:
        """The list and strip draw at the top, the bottom, and in a plain folder.

        (The full _draw needs a real pygame Rect for the nav header icons; the
        rendered screenshots in the PR cover that.)"""
        b = self.browser
        self.open_favorites()
        for scroll in (0.0, b.nav_list.max_scroll_pixels()):
            b.nav_list.set_scroll_pixels(scroll)
            b._draw_browse_patches_list()
            b._draw_jump_strip()
        self.open_folder("Pack")
        b._draw_browse_patches_list()
        b._draw_jump_strip()


class PointerFavoritesInTheBrowser(_BrowserCase):
    """The pointer index through every UI path (2026-10-15)."""

    def setUp(self) -> None:
        super().setUp()
        self.browser._toast = mock.Mock()
        self.browser._select_patch = lambda patch: TouchPatchBrowser._select_patch(self.browser, patch)

    def row(self, name: str) -> dict:
        return next(p for p in self.library.qa_patches() if p["name"] == name)

    def test_the_missing_row_is_listed_and_loads_nothing(self) -> None:
        self.open_favorites()
        row = self.row("Mystery Patch")
        self.assertTrue(row["missing"])
        self.assertIn("Mystery Patch", [e["patch"]["name"] for e in self.entries() if e["kind"] == "patch"])
        self.browser._select_patch(row)
        self.browser.loader.load_patch.assert_not_called()
        self.browser._toast.assert_called()
        self.assertIsNone(self.browser.loaded_patch_info)

    def test_a_present_row_loads_the_source_file_under_the_source_key(self) -> None:
        self.browser.loader.osc_enabled = True
        self.browser.loader.load_patch.return_value = True
        self.open_favorites()
        row = self.row("Attacky")
        source = self.library.scanner.get_patch_by_stable_key(row["stable_key"])
        self.browser._select_patch(row)
        self.browser.loader.load_patch.assert_called_once_with(source["path"], stable_key=source["stable_key"])
        self.assertEqual(self.browser.loaded_patch_info["stable_key"], source["stable_key"])

    def test_a_missing_row_offers_only_remove_and_remove_works(self) -> None:
        self.open_favorites()
        index = self.patch_index("Mystery Patch")
        target = self.browser._context_target_for_nav_index(index)
        self.browser._open_context_menu(target)
        self.assertEqual([a[0] for a in self.browser._context_menu_actions], ["unfavorite"])
        self.browser._execute_context_action("unfavorite")
        self.browser._execute_context_action("unfavorite_confirm")
        self.assertFalse(self.library.scanner.favorites_index.is_favorited("user:Gone/Mystery Patch"))
        self.open_favorites()
        self.assertNotIn("Mystery Patch", [e["patch"]["name"] for e in self.entries() if e["kind"] == "patch"])

    def test_the_missing_row_is_drawn_greyed_with_a_note(self) -> None:
        self.open_favorites()
        b = self.browser
        b.nav_list.scroll_to_index(self.patch_index("Mystery Patch"))
        drawn = self._drawn_rows()
        self.assertIn("Mystery Patch", [t for t, _y in drawn["md"]])
        self.assertTrue(any("Missing" in t for t, _y in drawn["sm"]), drawn["sm"])

    def test_heart_toggle_adds_and_removes_an_entry_without_a_file(self) -> None:
        b = self.browser
        scanner = self.library.scanner
        patch = scanner.get_patch_by_stable_key("user:Pack/Pack Patch 01")
        b.detail_patch = dict(patch)
        before = {p.name for p in self.library.lib.rglob("*") if p.is_file()}
        b._toggle_favorites()
        self.assertTrue(scanner.favorites_index.is_favorited(patch["stable_key"]))
        self.assertEqual({p.name for p in self.library.lib.rglob("*") if p.is_file()}, before)
        self.assertIn("Pack Patch 01", [p["name"] for p in self.library.qa_patches()])
        b._toggle_favorites()
        self.assertFalse(scanner.favorites_index.is_favorited(patch["stable_key"]))
        self.assertNotIn("Pack Patch 01", [p["name"] for p in self.library.qa_patches()])

    def test_add_folder_move_and_remove_all_through_the_menu(self) -> None:
        b = self.browser
        scanner = self.library.scanner
        self.open_folder("Pack")
        from patch_browser.context_menu import ContextTarget

        folder = ContextTarget(kind="library_folder", category="Pack", inner_segments=())
        b._open_context_menu(folder)
        b._execute_context_action("add_all_qa")
        self.assertEqual(len(scanner.favorites_index.entries), len(FAVORITES) + len(UNTRACED) + 30)
        self.assertEqual(scanner.favorites_index.folder_for("user:Pack/Pack Patch 01"), "Pack")
        # move one through the folder picker
        scanner.favorites_index.create_folder("Other")
        patch = scanner.get_patch_by_stable_key("user:Pack/Pack Patch 01")
        b._open_context_menu(ContextTarget(kind="patch", category="!Quick Access", patch=dict(patch)))
        b._execute_context_action("move_pick_folder")
        self.assertIn("pick_folder:Other", [a[0] for a in b._context_menu_actions])
        b._execute_context_action("pick_folder:Other")
        self.assertEqual(scanner.favorites_index.folder_for("user:Pack/Pack Patch 01"), "Other")
        # remove the whole folder
        b._context_target = ContextTarget(
            kind="qa_folder", category="!Quick Access", inner_segments=("Pack",), folder_name="Pack"
        )
        b._execute_context_action("qa_remove_all_confirm")
        # the 29 still in Pack go; the one moved to Other stays
        self.assertEqual(len(scanner.favorites_index.entries), len(FAVORITES) + len(UNTRACED) + 1)
        self.assertTrue(scanner.favorites_index.is_favorited("user:Pack/Pack Patch 01"))

    def test_new_rename_and_delete_folder_edit_only_the_index(self) -> None:
        b = self.browser
        index = self.library.scanner.favorites_index
        before = {p.name for p in self.library.lib.rglob("*")}
        from patch_browser.context_menu import ContextTarget

        root = ContextTarget(kind="qa_folder", category="!Quick Access", inner_segments=())
        b._context_target = root
        b._name_prompt_kind = "qa_new"
        b._name_prompt_text = "Gig"
        b._commit_name_prompt()
        self.assertEqual(index.folders, ["Gig"])
        b._context_target = ContextTarget(
            kind="qa_folder", category="!Quick Access", inner_segments=("Gig",), folder_name="Gig"
        )
        b._name_prompt_kind = "qa_new"
        b._name_prompt_text = "Deep"
        b._commit_name_prompt()
        self.assertEqual(index.folders, ["Gig", "Gig/Deep"])
        b._name_prompt_kind = "qa_rename"
        b._name_prompt_text = "Show"
        b._context_target = ContextTarget(
            kind="qa_folder", category="!Quick Access", inner_segments=("Gig",), folder_name="Gig"
        )
        b._commit_name_prompt()
        self.assertEqual(index.folders, ["Show", "Show/Deep"])
        b._context_target = ContextTarget(
            kind="qa_folder", category="!Quick Access", inner_segments=("Show",), folder_name="Show"
        )
        b._delete_qa_folder(b._context_target)
        self.assertEqual(index.folders, [])
        self.assertEqual({p.name for p in self.library.lib.rglob("*")}, before)  # no directories made

    def test_a_duplicate_folder_name_is_refused(self) -> None:
        b = self.browser
        from patch_browser.context_menu import ContextTarget

        b._context_target = ContextTarget(kind="qa_folder", category="!Quick Access", inner_segments=())
        for _ in range(2):
            b._name_prompt_kind = "qa_new"
            b._name_prompt_text = "Gig"
            b._commit_name_prompt()
        self.assertEqual(self.library.scanner.favorites_index.folders, ["Gig"])

    def test_sections_include_every_row_and_the_jump_strip_follows(self) -> None:
        self.open_favorites()
        groups = [e["group"] for e in self.entries() if e["kind"] == HEADER_KIND]
        self.assertEqual(groups, ["bass", "keys", "lead", "pad", "other"][: len(groups)])
        self.assertEqual(sum(e["count"] for e in self.entries() if e["kind"] == HEADER_KIND),
                         len(FAVORITES) + len(UNTRACED))
        self.assertEqual([g for g, _i in self.browser._jump_strip_sections()], groups)


# --- the jump strip -----------------------------------------------------------


class JumpStripMapping(unittest.TestCase):
    def test_cells_tile_the_strip_and_invert(self) -> None:
        for count in (1, 2, 5, 12, 15):
            for height in (300, 328, 331):
                top = 144
                covered = []
                for i in range(count):
                    y0, y1 = section_cell_bounds(i, top, height, count)
                    covered.extend(range(y0, y1))
                    for y in range(y0, y1):
                        self.assertEqual(section_cell_at(y, top, height, count), i)
                self.assertEqual(covered, list(range(top, top + height)), (count, height))

    def test_cells_clamp_past_the_ends(self) -> None:
        self.assertEqual(section_cell_at(-50, 100, 300, 5), 0)
        self.assertEqual(section_cell_at(900, 100, 300, 5), 4)

    def test_scrub_fraction_puts_the_thumb_under_the_finger(self) -> None:
        top, height, thumb = 144, 328, 40
        self.assertEqual(scrub_fraction(top, top, height, thumb), 0.0)
        self.assertEqual(scrub_fraction(top + height, top, height, thumb), 1.0)
        for y in range(top + thumb // 2, top + height - thumb // 2):
            f = scrub_fraction(y, top, height, thumb)
            self.assertAlmostEqual(thumb_top(f, top, height, thumb) + thumb / 2, y, delta=1)

    def test_thumb_is_proportional_with_a_floor(self) -> None:
        self.assertEqual(thumb_height(300, 300, 3000, 28), 30)
        self.assertEqual(thumb_height(300, 300, 30000, 28), 28)
        self.assertEqual(thumb_height(300, 300, 200, 28), 300)


class JumpStripInTheBrowser(_BrowserCase):
    def settle(self) -> None:
        for _ in range(60):
            self.browser.nav_list.tick(1 / 60)

    def strip_cell_y(self, i: int) -> int:
        rect = self.browser.jump_strip_rect
        count = len(self.browser._jump_strip_sections())
        y0, y1 = section_cell_bounds(i, rect.y, rect.h, count)
        return (y0 + y1) // 2

    def test_finger_wide_and_clear_of_the_rows(self) -> None:
        self.open_favorites()
        b = self.browser
        strip, nav = b.jump_strip_rect, b.nav_list.rect
        self.assertGreaterEqual(JUMP_STRIP_W, 40)
        self.assertEqual(strip.w, JUMP_STRIP_W)
        self.assertLessEqual(nav.right, strip.x, "rows end before the strip")
        self.assertLessEqual(strip.right, b.left_panel_rect.right, "inside the 250 px nav")
        self.assertEqual((strip.y, strip.h), (nav.y, nav.h))
        self.assertLessEqual(strip.right, b.main_rect.x)

    def test_tap_on_a_section_jumps_to_its_header(self) -> None:
        self.open_favorites()
        b = self.browser
        sections = b._jump_strip_sections()
        for i, (group, header) in enumerate(sections):
            pos = (b.jump_strip_rect.centerx, self.strip_cell_y(i))
            self.tap(pos)
            self.settle()
            nav = b.nav_list
            with self.subTest(group=group):
                self.assertEqual(nav.scroll_pixels, min(nav.row_top(header), nav.max_scroll_pixels()))
                self.assertEqual(self.selected, [], "a strip tap never selects a patch")

    def test_drag_along_the_strip_scrubs_sections(self) -> None:
        self.open_favorites()
        b = self.browser
        x = b.jump_strip_rect.centerx
        b._handle_evdev_browser_touch("down", (x, self.strip_cell_y(0)))
        b._handle_evdev_browser_touch("motion", (x, self.strip_cell_y(2)))
        self.settle()
        header = b._jump_strip_sections()[2][1]
        self.assertEqual(b.nav_list.scroll_pixels, min(b.nav_list.row_top(header), b.nav_list.max_scroll_pixels()))
        b._handle_evdev_browser_touch("up", (x, self.strip_cell_y(2)))
        self.assertFalse(b._jump_strip_capture)
        self.assertEqual(self.selected, [])

    def test_without_sections_it_scrubs_position(self) -> None:
        self.open_folder("Pack")
        b = self.browser
        rect = b.jump_strip_rect
        self.assertEqual(b._jump_strip_sections(), [])
        self.assertGreater(rect.w, 0)
        b._handle_evdev_browser_touch("down", (rect.centerx, rect.bottom - 1))
        self.assertEqual(b.nav_list.scroll_pixels, b.nav_list.max_scroll_pixels())
        b._handle_evdev_browser_touch("motion", (rect.centerx, rect.y))
        self.assertEqual(b.nav_list.scroll_pixels, 0)
        b._handle_evdev_browser_touch("motion", (rect.centerx, rect.y + rect.h // 2))
        self.assertAlmostEqual(
            b.nav_list.scroll_pixels / b.nav_list.max_scroll_pixels(), 0.5, delta=0.02
        )
        b._handle_evdev_browser_touch("up", (rect.centerx, rect.y + rect.h // 2))
        self.assertEqual(self.selected, [])

    def test_sdl_path_routes_the_strip_too(self) -> None:
        import pygame

        self.open_folder("Pack")
        b = self.browser
        rect = b.jump_strip_rect
        down = pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"pos": (rect.centerx, rect.bottom - 1), "button": 1})
        up = pygame.event.Event(pygame.MOUSEBUTTONUP, {"pos": (rect.centerx, rect.bottom - 1), "button": 1})
        b._handle_event(down)
        b._handle_event(up)
        self.assertEqual(b.nav_list.scroll_pixels, b.nav_list.max_scroll_pixels())
        self.assertEqual(self.selected, [])

    def test_no_strip_when_the_list_fits(self) -> None:
        self.open_folder("Leads")
        self.assertEqual(self.browser.jump_strip_rect.w, 0)
        self.assertEqual(self.browser.nav_list.rect.w, self.browser._nav_list_full_rect.w)

    def test_no_strip_outside_patch_lists(self) -> None:
        self.open_favorites()
        self.browser._go_up_to_folders()
        self.assertEqual(self.browser.left_nav_mode, LeftNavMode.FOLDERS)
        self.assertEqual(self.browser.jump_strip_rect.w, 0)


if __name__ == "__main__":
    unittest.main()
