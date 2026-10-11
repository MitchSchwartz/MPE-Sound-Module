"""Play-screen FX controls and the Settings default, at the 800x480 DSI size."""

from __future__ import annotations

import itertools
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.fake_pygame import install_fake_pygame

install_fake_pygame()

from patch_browser.fx_policy import (  # noqa: E402
    MOD_WHEEL_EFFECT,
    MOD_WHEEL_PATCH_FIRST,
    FxPolicy,
)
from patch_browser.geometry import Rect  # noqa: E402
from patch_browser.touch_browser_app import TouchPatchBrowser  # noqa: E402
from patch_browser.touch_ui_constants import (  # noqa: E402
    BROWSE_FILTER_W,
    BROWSE_OFFSET_HOME,
    BROWSE_PATCH_W,
    BROWSE_TRACK_W,
    LEFT_NAV_WIDTH,
)
from patch_browser.touch_ui_enums import Screen  # noqa: E402

SCREEN_W, SCREEN_H = 800, 480


def _overlaps(a: Rect, b: Rect) -> bool:
    return a.x < b.right and b.x < a.right and a.y < b.bottom and b.y < a.bottom


def _center(rect: Rect) -> tuple[int, int]:
    return rect.centerx, rect.centery


def _inside(inner: Rect, outer: Rect) -> bool:
    return (
        inner.x >= outer.x
        and inner.y >= outer.y
        and inner.right <= outer.right
        and inner.bottom <= outer.bottom
    )


class _BrowserCase(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)
        self.prefs = self.root / "ui.json"
        for target in ("patch_browser.ui_prefs.UI_STATE_FILE",):
            patcher = mock.patch(target, self.prefs)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _make_browser(self, *, fx_enabled: bool) -> TouchPatchBrowser:
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
        # Real numbers for the existing faders so the whole strip can draw.
        loader.normalization.get_user_trim_db.return_value = 0.0
        loader.normalization.get_slider_default_gain_db.return_value = 0.0
        loader.hold.get_effective_hold_mult.return_value = 1.0
        loader.pressure.get_slider_default_floor.return_value = 0.0
        loader.pressure.get_user_touch_offset.return_value = 0.0

        surge_monitor = mock.Mock()
        surge_monitor.get_status_summary.return_value = {"status": "Running", "details": "ok", "can_restart": False}
        cpu_monitor = mock.Mock()
        cpu_monitor.snapshot.return_value = {"online": True, "percent": 12.5}
        backlight = mock.Mock()
        backlight.get_percent.return_value = 100
        fake_screen = mock.Mock()
        fake_screen.get_size.return_value = (SCREEN_W, SCREEN_H)
        self.policy = FxPolicy(
            enabled=fx_enabled,
            route_path=self.root / "fx-route.json",
            sender=mock.Mock(),
        )
        patches = [
            mock.patch("patch_browser.touch_browser_app.PatchScanner", return_value=scanner),
            mock.patch("patch_browser.touch_browser_app.PatchLoader", return_value=loader),
            mock.patch("patch_browser.touch_browser_app.FxPolicy", return_value=self.policy),
            mock.patch("patch_browser.touch_browser_app.SurgeMonitor", return_value=surge_monitor),
            mock.patch("patch_browser.touch_browser_app.SurgeCpuMonitor", return_value=cpu_monitor),
            mock.patch("patch_browser.touch_browser_app.BacklightController", return_value=backlight),
            mock.patch("patch_browser.touch_browser_app.evdev_bridge_enabled", return_value=False),
            mock.patch("patch_browser.touch_browser_app.acquire_browser_display", return_value=fake_screen),
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
        browser = TouchPatchBrowser()
        browser.detail_patch = {
            "name": "A Patch With A Fairly Long Name",
            "category": "Keys",
            "path": str(self.root / "p.fxp"),
        }
        browser._layout()
        return browser


class PlayScreenLayoutTests(_BrowserCase):
    def test_carousel_widths_follow_the_narrower_nav(self) -> None:
        self.assertLess(LEFT_NAV_WIDTH, 268)
        self.assertEqual(BROWSE_FILTER_W + LEFT_NAV_WIDTH, SCREEN_W)
        self.assertEqual(BROWSE_PATCH_W + LEFT_NAV_WIDTH, SCREEN_W)
        self.assertEqual(BROWSE_TRACK_W, BROWSE_FILTER_W + LEFT_NAV_WIDTH + BROWSE_PATCH_W)
        self.assertEqual(BROWSE_OFFSET_HOME, -BROWSE_FILTER_W)

    def test_six_faders_and_the_switch_fit_without_overlap(self) -> None:
        browser = self._make_browser(fx_enabled=True)
        ids = [c.channel_id for c in browser.mixer_channels]
        self.assertEqual(ids, ["volume", "tail", "touch", "norm", "patch_mod", "effect"])
        screen = Rect(0, 0, SCREEN_W, SCREEN_H)
        main = browser.main_rect
        self.assertTrue(_inside(main, screen), main)
        columns = [c.column_rect for c in browser.mixer_channels]
        for col in columns:
            self.assertTrue(_inside(col, main), col)
        for a, b in itertools.combinations(columns, 2):
            self.assertFalse(_overlaps(a, b), (a, b))
        row = [
            browser.mod_wheel_info_rect,
            browser.mod_wheel_switch_rect,
            browser.normalize_btn,
            browser.favorites_btn,
        ]
        for rect in row:
            self.assertGreater(rect.w, 0)
            self.assertTrue(_inside(rect, main), rect)
            for col in columns:
                self.assertFalse(_overlaps(rect, col), (rect, col))
        for a, b in itertools.combinations(row, 2):
            self.assertFalse(_overlaps(a, b), (a, b))
        self.assertFalse(_overlaps(browser.left_panel_rect, main))

    def test_fader_labels_do_not_collide(self) -> None:
        """'Patch mod' is wider than its column; it must still clear its neighbours."""
        browser = self._make_browser(fx_enabled=True)
        channels = browser.mixer_channels
        width = browser.font_sm.size
        for left, right in zip(channels, channels[1:]):
            lw = width(left.label)[0]
            rw = width(right.label)[0]
            left_edge = left.column_rect.x + (left.column_rect.w + lw) // 2
            right_edge = right.column_rect.x + (right.column_rect.w - rw) // 2
            self.assertLess(left_edge, right_edge, (left.label, right.label))

    def test_nav_header_buttons_do_not_overlap(self) -> None:
        browser = self._make_browser(fx_enabled=True)
        buttons = [
            browser.nav_back_btn,
            browser.nav_current_btn,
            browser.nav_all_btn,
            browser.browse_looper_open_btn,
            browser.browse_filter_open_btn,
            browser.nav_collapse_btn,
        ]
        buttons = [b for b in buttons if b.w > 0]
        self.assertGreaterEqual(len(buttons), 5)
        for a, b in itertools.combinations(buttons, 2):
            self.assertFalse(_overlaps(a, b), (a, b))
        for b in buttons:
            self.assertTrue(_inside(b, browser.nav_header_rect), b)

    def test_fx_off_hides_switch_and_effect_but_keeps_patch_mod_and_readout(self) -> None:
        browser = self._make_browser(fx_enabled=False)
        ids = [c.channel_id for c in browser.mixer_channels]
        self.assertIn("patch_mod", ids)
        self.assertNotIn("effect", ids)
        self.assertEqual(browser.mod_wheel_switch_rect.w, 0)
        self.assertGreater(browser.mod_wheel_info_rect.w, 0)

    def test_draw_does_not_raise(self) -> None:
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                browser = self._make_browser(fx_enabled=enabled)
                browser.screen_state = Screen.BROWSER
                # The fake pygame cannot draw the expanded nav's icons; the
                # collapsed nav still draws the detail pane and the FX row.
                browser.left_nav_collapsed = True
                browser._layout()
                browser._draw()


class PlayScreenSwitchTests(_BrowserCase):
    def test_tap_flips_the_current_target_not_the_default(self) -> None:
        browser = self._make_browser(fx_enabled=True)
        self.assertEqual(self.policy.target, MOD_WHEEL_PATCH_FIRST)
        browser._handle_browser_tap(_center(browser.mod_wheel_switch_rect))
        self.assertEqual(self.policy.target, MOD_WHEEL_EFFECT)
        self.assertEqual(self.policy.global_target, MOD_WHEEL_PATCH_FIRST)
        self.assertFalse(self.prefs.exists() and "mod_wheel_target" in json.loads(self.prefs.read_text()))
        browser._handle_browser_tap(_center(browser.mod_wheel_switch_rect))
        self.assertEqual(self.policy.target, MOD_WHEEL_PATCH_FIRST)

    def test_evdev_tap_flips_it_too(self) -> None:
        browser = self._make_browser(fx_enabled=True)
        pos = _center(browser.mod_wheel_switch_rect)
        browser._handle_evdev_browser_touch("down", pos)
        browser._handle_evdev_browser_touch("up", pos)
        self.assertEqual(self.policy.target, MOD_WHEEL_EFFECT)


class SettingsDefaultTests(_BrowserCase):
    def test_row_cycles_and_persists_the_default(self) -> None:
        browser = self._make_browser(fx_enabled=True)
        browser._open_settings_panel()
        browser._settings_slide = 1.0
        self.assertGreater(browser.mod_wheel_row_rect.h, 0)
        browser._execute_settings_hit("mod_wheel", (0, 0))
        self.assertEqual(self.policy.global_target, MOD_WHEEL_EFFECT)
        self.assertEqual(self.policy.target, MOD_WHEEL_EFFECT)
        self.assertEqual(json.loads(self.prefs.read_text())["mod_wheel_target"], MOD_WHEEL_EFFECT)
        browser._execute_settings_hit("mod_wheel", (0, 0))
        self.assertEqual(json.loads(self.prefs.read_text())["mod_wheel_target"], MOD_WHEEL_PATCH_FIRST)

    def test_row_is_hit_testable(self) -> None:
        browser = self._make_browser(fx_enabled=True)
        browser._open_settings_panel()
        browser._settings_slide = 1.0
        browser._sync_settings_scroll_viewport()
        screen_rect = browser._panel_local_to_screen(browser.mod_wheel_row_rect, scrolled=True)
        self.assertEqual(browser._settings_hit_at(_center(screen_rect)), "mod_wheel")

    def test_row_absent_when_fx_off(self) -> None:
        browser = self._make_browser(fx_enabled=False)
        browser._open_settings_panel()
        self.assertEqual(browser.mod_wheel_row_rect.h, 0)

    def test_settings_draw_does_not_raise(self) -> None:
        browser = self._make_browser(fx_enabled=True)
        browser.left_nav_collapsed = True
        browser._layout()
        browser._open_settings_panel()
        browser._settings_slide = 1.0
        browser._draw()


if __name__ == "__main__":
    unittest.main()
