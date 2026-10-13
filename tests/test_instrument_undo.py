"""Set instrument offers Undo on its toast for 10 s (2026-10-13).

Driven end to end with a real scanner over a temporary library (the favorites
sections rig), through both input paths, as a finger would."""

from __future__ import annotations

import json
import time
import unittest
from unittest import mock

from tests.fake_pygame import install_fake_pygame

install_fake_pygame()

import pygame  # noqa: E402

from patch_browser.context_menu import ContextTarget  # noqa: E402
from patch_browser.favorites_sections import HEADER_KIND, section_starts  # noqa: E402
from patch_browser.touch_ui_constants import TOAST_ACTION_S  # noqa: E402
from patch_browser.touch_ui_enums import Screen  # noqa: E402
from tests.test_favorites_sections import _BrowserCase  # noqa: E402


class _UndoCase(_BrowserCase):
    def setUp(self) -> None:
        super().setUp()
        self.open_favorites()

    def fav(self, name: str) -> dict:
        return next(e["patch"] for e in self.entries() if e["kind"] == "patch" and e["patch"]["name"] == name)

    def section_of(self, name: str) -> str:
        group = None
        for entry in self.entries():
            if entry["kind"] == HEADER_KIND:
                group = entry["group"]
            elif entry["patch"]["name"] == name:
                return group
        raise AssertionError(name)

    def set_instrument(self, name: str, instrument: str) -> None:
        b = self.browser
        b._context_target = ContextTarget(kind="patch", patch=self.fav(name))
        b.screen_state = Screen.CONTEXT_MENU
        b._execute_context_action(f"pick_instrument:{instrument}")
        self.assertEqual(b.screen_state, Screen.BROWSER)

    def user_rows(self) -> dict:
        path = self.library.user_meta
        return json.loads(path.read_text())["patches"] if path.exists() else {}

    def key(self, name: str) -> str:
        return self.fav(name)["stable_key"]

    def undo_pos(self) -> tuple[int, int]:
        rect = self.browser._toast_action_rect()
        self.assertIsNotNone(rect, "no Undo button on the toast")
        return rect.centerx, rect.centery

    def tap_evdev(self, pos, end=None) -> None:
        b = self.browser
        b._handle_evdev_browser_touch("down", pos)
        if end is not None:
            b._handle_evdev_browser_touch("motion", end)
        b._handle_evdev_browser_touch("up", end or pos)

    def tap_pygame(self, pos, end=None) -> None:
        b = self.browser
        b._handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"pos": pos, "button": 1}))
        if end is not None:
            b._handle_event(pygame.event.Event(pygame.MOUSEMOTION, {"pos": end, "rel": (0, 0), "buttons": (1, 0, 0)}))
        b._handle_event(pygame.event.Event(pygame.MOUSEBUTTONUP, {"pos": end or pos, "button": 1}))


class UndoToast(_UndoCase):
    def test_set_shows_a_10_second_undo(self) -> None:
        before = time.time()
        self.set_instrument("Attacky", "pad")
        b = self.browser
        self.assertEqual(b.toast_message, "Instrument → Pad")
        self.assertEqual(b.toast_action[0], "Undo")
        self.assertEqual(TOAST_ACTION_S, 10.0)
        self.assertAlmostEqual(b.toast_until - before, 10.0, delta=0.5)
        self.assertEqual(self.section_of("Attacky"), "pad")

    def test_undo_unsets_an_instrument_the_patch_never_had(self) -> None:
        for path in ("evdev", "pygame"):
            with self.subTest(path=path):
                self.assertEqual(self.section_of("Attacky"), "bass")
                self.assertNotIn(self.key("Attacky"), self.user_rows())
                self.set_instrument("Attacky", "pad")
                self.assertEqual(self.section_of("Attacky"), "pad")
                self.assertEqual(self.user_rows()[self.key("Attacky")]["instrument_user"], "pad")
                (self.tap_evdev if path == "evdev" else self.tap_pygame)(self.undo_pos())
                self.assertEqual(self.section_of("Attacky"), "bass")
                # Unset, not set to its old value: the row is gone.
                self.assertNotIn(self.key("Attacky"), self.user_rows())
                self.assertIsNone(self.library.scanner.metadata_index.user_instrument(self.key("Attacky")))
                self.assertEqual(self.browser.toast_message, "Instrument change undone")
                self.assertIsNone(self.browser.toast_action)
                self.assertEqual(self.selected, [], "the undo tap selected nothing under it")

    def test_undo_restores_an_earlier_choice(self) -> None:
        self.set_instrument("Photon", "keys")
        self.set_instrument("Photon", "pad")
        self.tap_evdev(self.undo_pos())
        self.assertEqual(self.section_of("Photon"), "keys")
        self.assertEqual(self.user_rows()[self.key("Photon")]["instrument_user"], "keys")

    def test_undo_runs_once(self) -> None:
        self.set_instrument("Attacky", "pad")
        pos = self.undo_pos()
        self.tap_evdev(pos)
        self.assertIsNone(self.browser._toast_action_rect())
        self.set_instrument("Photon", "keys")  # something else to undo...
        self.browser._clear_toast()  # ...but no button now
        self.tap_evdev(pos)
        self.assertEqual(self.section_of("Photon"), "keys")
        self.assertEqual(self.section_of("Attacky"), "bass")

    def test_after_10_seconds_the_button_is_gone(self) -> None:
        self.set_instrument("Attacky", "pad")
        pos = self.undo_pos()
        later = self.browser.toast_until + 0.01
        with mock.patch("time.time", return_value=later):
            self.assertIsNone(self.browser._toast_action_rect())
            self.tap_evdev(pos)
        self.assertEqual(self.section_of("Attacky"), "pad")

    def test_sliding_off_the_button_cancels(self) -> None:
        self.set_instrument("Attacky", "pad")
        x, y = self.undo_pos()
        self.tap_evdev((x, y), end=(x, y - 150))
        self.assertEqual(self.section_of("Attacky"), "pad")
        self.assertIsNotNone(self.browser.toast_action, "still offered")

    def test_button_only_on_the_browser_screen(self) -> None:
        self.set_instrument("Attacky", "pad")
        pos = self.undo_pos()
        self.browser.screen_state = Screen.SETTINGS
        self.assertIsNone(self.browser._toast_action_rect())
        self.assertFalse(self.browser._toast_action_touch("down", pos))

    def test_another_toast_replaces_the_offer(self) -> None:
        self.set_instrument("Attacky", "pad")
        self.browser._toast("Patch mod reset", 1.2)
        self.assertIsNone(self.browser.toast_action)
        self.assertIsNone(self.browser._toast_action_rect())

    def test_the_toast_button_fires_its_action_once(self) -> None:
        """The mechanism, apart from Set instrument: an action that shows no
        toast of its own still cannot run twice."""
        calls = []
        self.browser._toast("Something happened", TOAST_ACTION_S, action=("Undo", lambda: calls.append(1)))
        pos = self.undo_pos()
        self.tap_evdev(pos)
        self.tap_evdev(pos)
        self.tap_pygame(pos)
        self.assertEqual(calls, [1])
        self.assertEqual(self.browser.toast_message, "")


class UserRowSnapshot(unittest.TestCase):
    def test_a_snapshot_is_a_copy_both_ways(self) -> None:
        import tempfile
        from pathlib import Path

        from patch_browser.patch_metadata import PatchMetadataIndex

        with tempfile.TemporaryDirectory() as tmp:
            index = PatchMetadataIndex(baseline_path=Path(tmp) / "none.json", user_path=Path(tmp) / "user.json")
            index.set_user_instrument("k", ["pad", "keys"])
            snap = index.user_row("k")
            snap["instrument_user"].append("lead")
            snap["instrument_user"] = "bass"
            self.assertEqual(index.user_instrument("k"), "pad")
            self.assertEqual(index.user_row("k")["instrument_user"], ["pad", "keys"])
            row = {"instrument_user": "organ"}
            index.restore_user_row("k", row)
            row["instrument_user"] = "fx"
            self.assertEqual(index.user_instrument("k"), "organ")
            self.assertIsNone(index.user_row("missing"))


class UndoWithConcurrentChanges(_UndoCase):
    def test_changing_another_patch_meanwhile_undoes_only_that_one(self) -> None:
        self.set_instrument("Attacky", "pad")
        self.set_instrument("Rhodes Mk1", "organ")  # within the 10 s
        self.tap_evdev(self.undo_pos())
        self.assertEqual(self.section_of("Rhodes Mk1"), "keys")
        self.assertNotIn(self.key("Rhodes Mk1"), self.user_rows())
        # The first change stands: its offer went with its toast.
        self.assertEqual(self.section_of("Attacky"), "pad")
        self.assertEqual(self.user_rows()[self.key("Attacky")]["instrument_user"], "pad")

    def test_a_stale_undo_never_clobbers_a_newer_choice(self) -> None:
        self.set_instrument("Attacky", "pad")
        callback = self.browser.toast_action[1]
        # The same patch changed by another route since (not through this toast).
        index = self.library.scanner.metadata_index
        index.set_user_instrument(self.key("Attacky"), "lead", patch=self.fav("Attacky"))
        callback()
        self.assertEqual(self.user_rows()[self.key("Attacky")]["instrument_user"], "lead")
        self.assertIn("not undone", self.browser.toast_message)

    def test_undo_keeps_other_rows_byte_for_byte(self) -> None:
        self.set_instrument("Glass Choir", "strings")
        self.browser._clear_toast()
        rows_before = self.user_rows()
        self.set_instrument("Attacky", "pad")
        self.tap_evdev(self.undo_pos())
        self.assertEqual(self.user_rows(), rows_before)


class UndoKeepsTheView(_UndoCase):
    def test_scroll_goes_back_and_the_selection_follows_the_patch(self) -> None:
        b = self.browser
        nav = b.nav_list
        b.detail_patch = dict(self.fav("Attacky"))
        b._refresh_lists()
        scroll = min(37.0, nav.max_scroll_pixels())
        nav.set_scroll_pixels(scroll)
        self.set_instrument("Attacky", "pad")
        nav.set_scroll_pixels(nav.max_scroll_pixels())  # wandered off to look for it
        self.tap_evdev(self.undo_pos())
        self.assertEqual(nav.scroll_pixels, scroll)
        highlighted = self.entries()[nav.highlight_index]
        self.assertEqual(highlighted["patch"]["name"], "Attacky")
        self.assertEqual(self.section_of("Attacky"), "bass")
        self.assertEqual(b.detail_patch.get("instrument_primary"), self.fav("Attacky").get("instrument_primary"))

    def test_scroll_is_left_alone_in_another_list(self) -> None:
        b = self.browser
        self.set_instrument("Attacky", "pad")
        callback = b.toast_action[1]
        self.open_folder("Pack")
        b.nav_list.set_scroll_pixels(b.nav_list.max_scroll_pixels())
        where = b.nav_list.scroll_pixels
        callback()
        self.assertEqual(b.nav_list.scroll_pixels, where)
        self.open_favorites()
        self.assertEqual(self.section_of("Attacky"), "bass")

    def test_sections_reflow_both_ways(self) -> None:
        """Moving the only Lead out empties its section; undo brings it back."""
        groups = [g for g, _ in section_starts(self.entries())]
        self.assertIn("lead", groups)
        self.set_instrument("Photon", "bass")
        self.assertNotIn("lead", [g for g, _ in section_starts(self.entries())])
        self.tap_evdev(self.undo_pos())
        self.assertEqual([g for g, _ in section_starts(self.entries())], groups)


if __name__ == "__main__":
    unittest.main()
