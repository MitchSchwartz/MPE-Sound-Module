"""Smoke tests for TouchPatchBrowser mixin wiring (mocked pygame, no display)."""

from __future__ import annotations

import unittest
from unittest import mock

from tests.fake_pygame import install_fake_pygame

install_fake_pygame()

from patch_browser.touch_browser_app import TouchPatchBrowser  # noqa: E402
from patch_browser.touch_ui_enums import Screen  # noqa: E402


def _default_surge_status() -> dict:
    return {
        "status": "Running",
        "details": "ok",
        "can_restart": False,
    }


class TouchBrowserSmokeTests(unittest.TestCase):
    def _make_browser(self) -> TouchPatchBrowser:
        scanner = mock.Mock()
        scanner.load_last_patch.return_value = None
        scanner.get_categories.return_value = []
        scanner.get_subfolders.return_value = []
        scanner.get_patches_in_folder.return_value = []
        scanner.scan_complete.is_set.return_value = True
        scanner.wait_for_scan.return_value = True
        scanner.patches = {}

        loader = mock.Mock()
        loader.osc_enabled = False
        loader.normalization.is_globally_enabled.return_value = True
        loader.normalization.is_enabled.return_value = True
        loader.normalization.get_entry.return_value = None
        loader.normalization.get_raw_gain_db.return_value = None
        loader.normalization.list_missing.return_value = []

        surge_monitor = mock.Mock()
        surge_monitor.get_status_summary.return_value = _default_surge_status()

        cpu_monitor = mock.Mock()
        cpu_monitor.snapshot.return_value = {"online": True, "percent": 12.5}

        backlight = mock.Mock()
        backlight.get_percent.return_value = 100
        backlight.restore_saved.return_value = None

        fake_screen = mock.Mock()
        fake_screen.get_size.return_value = (800, 480)
        patches = [
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
            mock.patch(
                "patch_browser.touch_browser_wifi_modal.wifi_settings_row_label",
                return_value="Wi‑Fi — Not connected",
            ),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

        return TouchPatchBrowser()

    def test_handle_event_quit_stops_run_loop(self) -> None:
        browser = self._make_browser()
        import pygame

        browser._handle_event(pygame.event.Event(pygame.QUIT))
        self.assertFalse(browser._running)

    def test_handle_event_mousebuttonup_and_draw_do_not_raise(self) -> None:
        browser = self._make_browser()
        import pygame

        browser.screen_state = Screen.BROWSER
        browser.left_nav_collapsed = True
        browser.nav_list.items = []
        browser.nav_list.take_tap_index = mock.Mock(return_value=None)

        event = pygame.event.Event(
            pygame.MOUSEBUTTONUP,
            {"pos": (400, 240), "button": 1},
        )
        browser._handle_event(event)
        browser._draw()

    def test_theme_modal_hit_test_supports_all_views(self) -> None:
        browser = self._make_browser()
        from patch_browser.geometry import Rect
        from patch_browser.ui_theme import THEME_VIEW_COLORS, THEME_VIEW_PICKER

        browser._theme_view_state = THEME_VIEW_PICKER
        browser._picker_slider_rects = {}
        browser._picker_back_rect = None
        browser._picker_save_rect = None
        browser._picker_delete_rect = None
        browser._theme_modal_hit_at((10, 10))

        browser._theme_view_state = THEME_VIEW_COLORS
        browser._theme_color_delete_rects = []
        browser._theme_color_swatch_rects = []
        browser._theme_colors_back_rect = None
        browser._theme_modal_hit_at((10, 10))

        browser._theme_view_state = "main"
        browser._theme_base_option_rects = []
        browser._theme_style_option_rects = [Rect(0, 0, 100, 44), Rect(110, 0, 100, 44)]
        browser._theme_choose_color_btn = None
        browser._theme_done_rect = None
        browser._theme_cancel_rect = None
        hit = browser._theme_modal_hit_at((150, 22))
        self.assertEqual(hit, "style:1")

if __name__ == "__main__":
    unittest.main()
