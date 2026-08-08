"""Nested folder browse tests — Phase 2 drill-down and back stack."""

from __future__ import annotations

import sys
import types
import unittest
from unittest import mock


def _install_fake_pygame() -> None:
    if "pygame" in sys.modules:
        return
    fake = types.ModuleType("pygame")
    fake.init = mock.Mock()
    fake.quit = mock.Mock()
    sys.modules["pygame"] = fake


_install_fake_pygame()

from patch_browser.touch_browser_patches import TouchBrowserPatchesMixin
from patch_browser.touch_ui_enums import LeftNavMode


class _NestedBrowseHost(TouchBrowserPatchesMixin):
    def __init__(self) -> None:
        self.categories = ["!Quick Access", "Bass"]
        self.browse_folder_index = 0
        self.browse_inner_segments: tuple[str, ...] = ()
        self.loaded_folder_index = 0
        self.loaded_inner_segments: tuple[str, ...] = ()
        self.left_nav_mode = LeftNavMode.FOLDERS
        self.left_nav_collapsed = False
        self.detail_patch = None
        self.loaded_patch_info = None
        self.all_patches_flat: list[dict] = []
        self._browse_nav_entries: list[dict] = []
        self._all_patches_saved_scroll = 0.0
        self._scan_lock = mock.Mock()
        self._scan_lock.__enter__ = mock.Mock(return_value=None)
        self._scan_lock.__exit__ = mock.Mock(return_value=False)
        self.nav_list = mock.Mock()
        self.nav_list._scroll_pixels = 0.0
        self.nav_list._velocity = 0.0
        self.nav_list._momentum_active = False
        self.nav_list.row_height = 50
        self.nav_list._max_scroll_pixels = mock.Mock(return_value=0.0)
        self.nav_list.set_items = mock.Mock()
        self.loader = mock.Mock()
        self.loader.osc_enabled = True
        self.loader.load_patch.return_value = True
        self.volume_level = 1.0
        self.scanner = mock.Mock()
        self._layout_calls = 0
        self._relayout_calls = 0
        self._enter_nav_calls: list[dict] = []

        self._patches = {
            "!Quick Access": {
                "root": [{"name": "Solo", "path": "/qa/Solo.fxp", "category": "!Quick Access", "inner_segments": ()}],
                "Gig A": [{"name": "Pad", "path": "/qa/Gig A/Pad.fxp", "category": "!Quick Access", "inner_segments": ("Gig A",)}],
            },
            "Bass": {
                "root": [{"name": "Root", "path": "/factory/Bass/Root.fxp", "category": "Bass", "inner_segments": ()}],
                "Sub": [{"name": "Deep", "path": "/factory/Bass/Sub/Deep.fxp", "category": "Bass", "inner_segments": ("Sub",)}],
            },
        }

        def _subfolders(category: str, inner: tuple[str, ...] = ()) -> list[str]:
            tree = self._patches.get(category, {})
            if inner:
                return []
            return sorted(k for k in tree if k != "root")

        def _patches_in_folder(category: str, inner: tuple[str, ...] = ()) -> list[dict]:
            tree = self._patches.get(category, {})
            if not inner:
                out = list(tree.get("root", []))
                return out
            key = inner[0]
            return list(tree.get(key, []))

        self.scanner.get_subfolders.side_effect = _subfolders
        self.scanner.get_patches_in_folder.side_effect = _patches_in_folder
        self.scanner.get_patches_in_category.side_effect = lambda cat: (
            self._patches.get(cat, {}).get("root", [])
            + sum(
                (v for k, v in self._patches.get(cat, {}).items() if k != "root"),
                [],
            )
        )
        self.scanner.save_last_patch = mock.Mock()
        self.instrument_filter = None

    def _patch_passes_instrument_filter(self, patch: dict) -> bool:
        return True

    def _refresh_instrument_chips(self) -> None:
        pass

    def _layout(self) -> None:
        self._layout_calls += 1

    def _relayout(self) -> None:
        self._relayout_calls += 1

    def _update_nav_list_geometry(self) -> None:
        pass

    def _enter_nav_mode(self, mode, **kwargs) -> None:
        self._enter_nav_calls.append({"mode": mode, **kwargs})
        self.left_nav_mode = mode
        if "browse_folder_index" in kwargs and kwargs["browse_folder_index"] is not None:
            self.browse_folder_index = kwargs["browse_folder_index"]
        if "browse_inner_segments" in kwargs and kwargs["browse_inner_segments"] is not None:
            self.browse_inner_segments = tuple(kwargs["browse_inner_segments"])
        self._refresh_lists(scroll_to_selection=kwargs.get("scroll_to_selection", False))

    def _apply_volume(self, *_a, **_k) -> None:
        pass

    def _sync_pressure_live(self) -> None:
        pass

    def _note_surge_patch_load_success(self) -> None:
        pass

    def _toast(self, *_a, **_k) -> None:
        pass


class NestedFolderBrowseTests(unittest.TestCase):
    def test_browse_list_shows_subfolders_then_patches(self) -> None:
        host = _NestedBrowseHost()
        host.browse_folder_index = 1  # Bass
        host.left_nav_mode = LeftNavMode.PATCHES
        host._refresh_lists()

        labels = host.nav_list.set_items.call_args[0][0]
        self.assertEqual(labels[0], "Sub  >")
        self.assertIn("Root", labels)

    def test_quick_access_shows_gig_subfolder(self) -> None:
        host = _NestedBrowseHost()
        host.browse_folder_index = 0
        host.left_nav_mode = LeftNavMode.PATCHES
        host._refresh_lists()
        labels = host.nav_list.set_items.call_args[0][0]
        self.assertIn("Gig A  >", labels)
        self.assertIn("Solo", labels)

    def test_enter_subfolder_appends_segment(self) -> None:
        host = _NestedBrowseHost()
        host.browse_folder_index = 1
        host.left_nav_mode = LeftNavMode.PATCHES
        host._enter_subfolder("Sub")
        self.assertEqual(host.browse_inner_segments, ("Sub",))

    def test_back_from_subfolder_pops_one_level(self) -> None:
        host = _NestedBrowseHost()
        host.browse_folder_index = 1
        host.browse_inner_segments = ("Sub",)
        host.left_nav_mode = LeftNavMode.PATCHES
        host._go_up_to_folders()
        self.assertEqual(host.browse_inner_segments, ())
        self.assertEqual(host.left_nav_mode, LeftNavMode.PATCHES)

    def test_back_at_category_root_goes_to_folders(self) -> None:
        host = _NestedBrowseHost()
        host.browse_folder_index = 1
        host.left_nav_mode = LeftNavMode.PATCHES
        host._go_up_to_folders()
        self.assertEqual(host.left_nav_mode, LeftNavMode.FOLDERS)

    def test_select_subfolder_row_drills_down(self) -> None:
        host = _NestedBrowseHost()
        host.browse_folder_index = 1
        host.left_nav_mode = LeftNavMode.PATCHES
        host._refresh_lists()
        host._select_nav_index(0)  # Sub  >
        self.assertEqual(host.browse_inner_segments, ("Sub",))

    def test_select_patch_row_loads_patch(self) -> None:
        host = _NestedBrowseHost()
        host.browse_folder_index = 1
        host.left_nav_mode = LeftNavMode.PATCHES
        host._refresh_lists()
        # index 0 = Sub folder, index 1 = Root patch
        host._select_nav_index(1)
        self.assertIsNotNone(host.detail_patch)
        self.assertEqual(host.detail_patch["name"], "Root")
        host.loader.load_patch.assert_called()

    def test_go_to_loaded_folder_restores_inner_segments(self) -> None:
        host = _NestedBrowseHost()
        host.loaded_patch_info = {
            "name": "Deep",
            "category": "Bass",
            "path": "/factory/Bass/Sub/Deep.fxp",
            "inner_segments": ["Sub"],
        }
        host.loaded_folder_index = 1
        host.left_nav_mode = LeftNavMode.ALL_PATCHES
        host._go_to_loaded_folder()
        self.assertEqual(host.browse_inner_segments, ("Sub",))
        self.assertEqual(host.browse_folder_index, 1)

    def test_browse_folder_title_shows_breadcrumb(self) -> None:
        host = _NestedBrowseHost()
        host.browse_folder_index = 1
        host.browse_inner_segments = ("Sub",)
        self.assertEqual(host._browse_folder_title(), "Bass / Sub")


if __name__ == "__main__":
    unittest.main()
