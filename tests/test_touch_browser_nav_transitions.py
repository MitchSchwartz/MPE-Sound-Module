"""Nav transition tests for #24 — _enter_nav_mode and browse flows."""

from __future__ import annotations

import sys
import types
import unittest
from unittest import mock


def _install_fake_pygame() -> None:
    existing = sys.modules.get("pygame")
    if isinstance(existing, types.ModuleType) and hasattr(existing, "display"):
        return

    fake = types.ModuleType("pygame")
    fake.QUIT = 12
    fake.FULLSCREEN = 1
    fake.SRCALPHA = 65536

    class _FakeRect:
        def __init__(self, *args, **kwargs):
            if len(args) == 4:
                self.x, self.y, self.w, self.h = args
            else:
                self.x = self.y = self.w = self.h = 0
            self.centery = self.y + self.h // 2

        @property
        def right(self) -> int:
            return self.x + self.w

        @property
        def bottom(self) -> int:
            return self.y + self.h

        def pygame_rect(self):
            return self

    class _FakeSurface:
        def __init__(self, size, flags=0):
            self.size = size

        def fill(self, *_a, **_k) -> None:
            return None

        def blit(self, *_a, **_k) -> None:
            return None

        def get_size(self):
            return self.size

        def get_clip(self):
            return _FakeRect(0, 0, 800, 480)

        def set_clip(self, _rect) -> None:
            return None

        def get_width(self) -> int:
            return self.size[0]

        def get_height(self) -> int:
            return self.size[1]

    class _FakeFont:
        def __init__(self, *_args, **_kwargs):
            pass

        def size(self, text):
            return (max(8, len(text) * 8), 16)

        def render(self, text, _a, _c):
            surf = _FakeSurface((max(8, len(text) * 8), 16))
            surf.get_width = lambda: max(8, len(text) * 8)
            surf.get_height = lambda: 16
            return surf

        def get_linesize(self) -> int:
            return 18

    class _FakeEvent:
        def __init__(self, kind, attrs=None):
            self.type = kind
            for key, value in (attrs or {}).items():
                setattr(self, key, value)

    fake.Rect = _FakeRect
    fake.Surface = _FakeSurface
    fake.event = types.SimpleNamespace(Event=_FakeEvent)
    fake.QUIT = 12
    fake.MOUSEBUTTONDOWN = 1025
    fake.MOUSEBUTTONUP = 1026
    fake.MOUSEMOTION = 1024
    fake.FINGERDOWN = 1792
    fake.FINGERUP = 1793
    fake.FINGERMOTION = 1794
    fake.init = mock.Mock()
    fake.quit = mock.Mock()
    fake.display = types.SimpleNamespace(
        set_caption=mock.Mock(),
        set_mode=mock.Mock(return_value=_FakeSurface((800, 480))),
        flip=mock.Mock(),
    )
    fake.mouse = types.SimpleNamespace(set_visible=mock.Mock())
    fake.font = types.SimpleNamespace(
        Font=_FakeFont,
        match_font=mock.Mock(return_value="/tmp/fake-font.ttf"),
    )
    fake.draw = types.SimpleNamespace(rect=mock.Mock(), line=mock.Mock(), lines=mock.Mock())
    sys.modules["pygame"] = fake


_install_fake_pygame()

from patch_browser.touch_browser_app import TouchPatchBrowser  # noqa: E402
from patch_browser.touch_ui_enums import LeftNavMode  # noqa: E402


def _default_surge_status() -> dict:
    return {"status": "Running", "details": "ok", "can_restart": False}


class TouchBrowserNavTransitionTests(unittest.TestCase):
    def _make_browser(self, *, categories: list[str] | None = None) -> TouchPatchBrowser:
        categories = categories or ["!Quick Access", "Bass", "Piano"]
        patches = {
            cat: [{"name": f"{cat} Patch", "path": f"/tmp/{cat}/a.fxp", "category": cat}]
            for cat in categories
        }

        scanner = mock.Mock()
        scanner.load_last_patch.return_value = None
        scanner.get_categories.return_value = categories
        scanner.get_patches_in_category.side_effect = lambda cat: patches.get(cat, [])
        scanner.get_subfolders.return_value = []
        scanner.get_patches_in_folder.side_effect = lambda cat, inner=(): patches.get(cat, [])
        scanner.scan_complete.is_set.return_value = True
        scanner.wait_for_scan.return_value = True
        scanner.patches = patches
        scanner.scan_lock = __import__("threading").Lock()

        loader = mock.Mock()
        loader.osc_enabled = True
        loader.load_patch.return_value = True
        loader.normalization.is_globally_enabled.return_value = True
        loader.normalization.is_enabled.return_value = True
        loader.normalization.get_entry.return_value = None
        loader.normalization.get_raw_gain_db.return_value = None
        loader.normalization.list_missing.return_value = []

        surge_monitor = mock.Mock()
        surge_monitor.get_status_summary.return_value = _default_surge_status()
        surge_monitor.check_health.return_value = (True, "ok")
        surge_monitor.osc_port_in_use.return_value = True
        surge_monitor.surge_pid = 1234

        cpu_monitor = mock.Mock()
        cpu_monitor.snapshot.return_value = {"online": True, "percent": 5.0}

        backlight = mock.Mock()
        backlight.get_percent.return_value = 100
        backlight.restore_saved.return_value = None

        fake_screen = mock.Mock()
        fake_screen.get_size.return_value = (800, 480)
        patchers = [
            mock.patch("patch_browser.touch_browser_app.PatchScanner", return_value=scanner),
            mock.patch("patch_browser.touch_browser_app.PatchLoader", return_value=loader),
            mock.patch("patch_browser.touch_browser_app.SurgeMonitor", return_value=surge_monitor),
            mock.patch("patch_browser.touch_browser_app.SurgeCpuMonitor", return_value=cpu_monitor),
            mock.patch("patch_browser.touch_browser_app.BacklightController", return_value=backlight),
            mock.patch("patch_browser.touch_browser_app.evdev_bridge_enabled", return_value=False),
            mock.patch(
                "patch_browser.touch_browser_app.acquire_browser_display",
                return_value=fake_screen,
            ),
            mock.patch.object(TouchPatchBrowser, "_start_background_scan"),
            mock.patch.object(TouchPatchBrowser, "_wait_for_initial_scan"),
            mock.patch.object(TouchPatchBrowser, "_complete_boot_splash"),
            mock.patch.object(TouchPatchBrowser, "_paint_boot_splash_frame"),
            mock.patch.object(TouchPatchBrowser, "_start_evdev_touch_bridge"),
            mock.patch.object(TouchPatchBrowser, "_bootstrap_patches"),
            mock.patch(
                "patch_browser.wifi_manager.wifi_settings_row_label",
                return_value="Wi‑Fi — Not connected",
            ),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

        browser = TouchPatchBrowser()
        browser.categories = categories
        browser.scanner = scanner
        browser._layout()
        return browser

    def test_enter_all_patches_expands_main_rect_after_leave(self) -> None:
        browser = self._make_browser()
        browser.left_nav_mode = LeftNavMode.FOLDERS
        browser._layout()
        browser._enter_all_patches()
        self.assertEqual(browser.left_nav_mode, LeftNavMode.ALL_PATCHES)
        self.assertEqual(browser.main_rect.w, 0)

        browser._go_back_from_all_patches()
        self.assertEqual(browser.left_nav_mode, LeftNavMode.FOLDERS)
        self.assertGreater(browser.main_rect.w, 100)

    def test_all_patches_scroll_restored_on_reenter(self) -> None:
        browser = self._make_browser()
        browser._enter_all_patches()
        browser.nav_list._scroll_pixels = 120.0
        browser.nav_list._max_scroll_pixels = mock.Mock(return_value=500.0)
        browser._snapshot_all_patches_scroll()
        browser._go_back_from_all_patches()

        browser._enter_all_patches()
        self.assertAlmostEqual(browser.nav_list._scroll_pixels, 120.0)

    def test_leave_all_clears_az_rail_capture(self) -> None:
        browser = self._make_browser()
        browser._enter_all_patches()
        browser._az_rail_capture = True
        browser._az_rail_scrub_letter = "B"
        browser._go_back_from_all_patches()
        self.assertFalse(browser._az_rail_capture)
        self.assertIsNone(browser._az_rail_scrub_letter)

    def test_pick_patch_from_all_loads_and_shows_detail_pane(self) -> None:
        browser = self._make_browser()
        browser._rebuild_all_patches_index()
        patch = browser.all_patches_flat[0]
        browser._select_patch(patch)
        self.assertEqual(browser.left_nav_mode, LeftNavMode.PATCHES)
        self.assertIsNotNone(browser.detail_patch)
        self.assertGreater(browser.main_rect.w, 100)
        loader = browser.loader
        loader.load_patch.assert_called()

    def test_current_chip_from_all_jumps_to_loaded_folder(self) -> None:
        browser = self._make_browser()
        browser.loaded_patch_info = {
            "name": "Bass Patch",
            "category": "Bass",
            "path": "/tmp/Bass/a.fxp",
        }
        browser.loaded_folder_index = 1
        browser._enter_all_patches()
        browser._go_to_loaded_folder()
        self.assertEqual(browser.left_nav_mode, LeftNavMode.PATCHES)
        self.assertEqual(browser.browse_folder_index, 1)
        self.assertGreater(browser.main_rect.w, 100)

    def test_back_from_patch_list_goes_to_folders(self) -> None:
        browser = self._make_browser()
        browser._enter_folder(2)
        self.assertEqual(browser.left_nav_mode, LeftNavMode.PATCHES)
        browser._go_up_to_folders()
        self.assertEqual(browser.left_nav_mode, LeftNavMode.FOLDERS)

    def test_all_patches_list_has_no_row_highlight_index(self) -> None:
        browser = self._make_browser()
        browser.detail_patch = {"name": "x", "category": "Bass", "path": "/tmp/x.fxp"}
        browser._enter_all_patches()
        self.assertIsNone(browser.nav_list.highlight_index)


class NavModeGeometryTests(unittest.TestCase):
    def test_geometry_change_only_for_all_transitions(self) -> None:
        from patch_browser.touch_browser_nav import nav_mode_changes_geometry

        self.assertTrue(
            nav_mode_changes_geometry(LeftNavMode.FOLDERS, LeftNavMode.ALL_PATCHES)
        )
        self.assertTrue(
            nav_mode_changes_geometry(LeftNavMode.ALL_PATCHES, LeftNavMode.FOLDERS)
        )
        self.assertFalse(
            nav_mode_changes_geometry(LeftNavMode.FOLDERS, LeftNavMode.PATCHES)
        )
        self.assertFalse(
            nav_mode_changes_geometry(LeftNavMode.PATCHES, LeftNavMode.FOLDERS)
        )


if __name__ == "__main__":
    unittest.main()
