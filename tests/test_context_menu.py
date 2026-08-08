"""Tests for long-press context menu action building."""

import unittest

from patch_browser.context_menu import (
    ContextTarget,
    build_context_actions,
    folder_picker_actions,
    instrument_picker_actions,
)
from patch_browser.favorites_index import qa_folder_key_for_library


class ContextMenuTests(unittest.TestCase):
    def test_library_folder_actions(self) -> None:
        target = ContextTarget(kind="library_folder", category="Bass", inner_segments=())
        actions = build_context_actions(target, is_favorited=False)
        ids = [a[0] for a in actions]
        self.assertEqual(ids, ["add_all_qa"])

    def test_library_nested_folder_mirror_key(self) -> None:
        key = qa_folder_key_for_library("Bass", ("Sub",))
        self.assertEqual(key, "Bass/Sub")

    def test_patch_unfavorited_actions(self) -> None:
        target = ContextTarget(kind="patch", patch={"name": "Acid"})
        actions = build_context_actions(target, is_favorited=False)
        ids = [a[0] for a in actions]
        self.assertIn("favorite_qa", ids)
        self.assertIn("add_pick_folder", ids)
        self.assertIn("set_instrument_pick", ids)

    def test_patch_favorited_actions(self) -> None:
        target = ContextTarget(kind="patch", patch={"name": "Acid"})
        actions = build_context_actions(target, is_favorited=True)
        ids = [a[0] for a in actions]
        self.assertIn("unfavorite", ids)
        self.assertIn("move_pick_folder", ids)

    def test_qa_folder_liked_only_new(self) -> None:
        target = ContextTarget(
            kind="qa_folder",
            category="!Quick Access",
            inner_segments=("Liked",),
            folder_name="Liked",
        )
        actions = build_context_actions(target, is_favorited=False, qa_patch_count=0)
        self.assertEqual([a[0] for a in actions], ["qa_new_subfolder"])

    def test_qa_folder_remove_all_when_populated(self) -> None:
        target = ContextTarget(
            kind="qa_folder",
            category="!Quick Access",
            inner_segments=("Gigs",),
            folder_name="Gigs",
        )
        actions = build_context_actions(target, is_favorited=False, qa_patch_count=3)
        self.assertIn("qa_remove_all", [a[0] for a in actions])

    def test_qa_folder_user_actions(self) -> None:
        target = ContextTarget(
            kind="qa_folder",
            category="!Quick Access",
            inner_segments=("Gigs",),
            folder_name="Gigs",
        )
        actions = build_context_actions(target, is_favorited=False)
        ids = [a[0] for a in actions]
        self.assertIn("qa_rename", ids)
        self.assertIn("qa_delete", ids)

    def test_nested_qa_folder_no_rename(self) -> None:
        target = ContextTarget(
            kind="qa_folder",
            category="!Quick Access",
            inner_segments=("Gigs", "Live"),
            folder_name="Live",
        )
        actions = build_context_actions(target, is_favorited=False)
        ids = [a[0] for a in actions]
        self.assertEqual(ids, ["qa_new_subfolder"])

    def test_picker_helpers(self) -> None:
        actions = folder_picker_actions(["Gigs", "Sunday"])
        ids = [a[0] for a in actions]
        self.assertEqual(ids, ["pick_folder:Gigs", "pick_folder:Sunday"])
        self.assertTrue(instrument_picker_actions())
        self.assertIn("percussion", [a[1].lower() for a in instrument_picker_actions()])
        self.assertIn("sequencer", [a[1].lower() for a in instrument_picker_actions()])

    def test_is_qa_browse_quick_select(self) -> None:
        from patch_browser.context_menu import is_qa_browse

        self.assertTrue(is_qa_browse("!Quick Access"))
        self.assertFalse(is_qa_browse("Bass"))


if __name__ == "__main__":
    unittest.main()
