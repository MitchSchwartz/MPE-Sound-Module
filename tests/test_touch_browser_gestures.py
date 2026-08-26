"""Touch browser gesture, navigation, and patch-load tests (consolidated)."""

from __future__ import annotations

import time
import unittest
from unittest import mock

from tests.fake_pygame import install_fake_pygame

install_fake_pygame()

from patch_browser.context_menu import ContextTarget
from patch_browser.geometry import Rect
from patch_browser.touch_browser_app import TouchPatchBrowser
from patch_browser.touch_browser_browse import TouchBrowserBrowseMixin
from patch_browser.touch_browser_context import TouchBrowserContextMixin
from patch_browser.touch_browser_instruments import TouchBrowserInstrumentsMixin
from patch_browser.touch_browser_normalization import TouchBrowserNormalizationMixin
from patch_browser.touch_browser_patches import TouchBrowserPatchesMixin
from patch_browser.touch_ui_constants import (
    BROWSE_EDGE_GRAB_W,
    BROWSE_FILTER_W,
    BROWSE_OFFSET_FILTER,
    BROWSE_OFFSET_HOME,
    BROWSE_PATCH_W,
    LEFT_NAV_WIDTH,
)
from patch_browser.touch_ui_enums import LeftNavMode, Screen


def _default_surge_status() -> dict:
    return {"status": "Running", "details": "ok", "can_restart": False}


def _goto_filter_stop(host: "_BrowseHost", tag_rects: list[tuple[str, Rect]]) -> None:
    host._browse_carousel.state.stop = "filter"
    host._browse_carousel.state.offset_px = BROWSE_OFFSET_FILTER
    host.browse_filter_rect = Rect(0, 40, 532, 400)
    host.browse_filter_tag_rects = tag_rects


class _BrowseHost(TouchBrowserBrowseMixin, TouchBrowserInstrumentsMixin):
    def __init__(self) -> None:
        self.left_nav_mode = LeftNavMode.PATCHES
        self.left_nav_collapsed = False
        self.left_panel_rect = Rect(BROWSE_OFFSET_FILTER + 532, 40, 268, 400)
        self.all_patches_flat: list[dict] = []
        self.categories: list[str] = []
        self.nav_list = mock.Mock()
        self.font_sm = mock.Mock()
        self.font_sm.size = lambda text: (len(text) * 8, 16)
        self.font_md = mock.Mock()
        self.font_md.size = lambda text: (len(text) * 9, 20)
        self._context_patches_calls = 0
        self._init_browse_carousel_state()
        self._init_instrument_filter_state()
        self._layout_calls = 0
        self._draw_calls = 0

    def _layout(self) -> None:
        self._layout_calls += 1

    def _draw(self) -> None:
        self._draw_calls += 1

    def _set_instrument_filter(self, instrument: str | None) -> None:
        self.instrument_filter = instrument

    def _patches_for_chip_context(self) -> list[dict]:
        self._context_patches_calls += 1
        return super()._patches_for_chip_context()


class _BrowseTrackLayoutTestCase(unittest.TestCase):
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
                "root": [{"name": "Solo", "path": "/qa/Solo.fxp", "category": "!Quick Access", "inner_segments": (), "instrument_primary": "lead"}],
                "Gig A": [{"name": "Pad", "path": "/qa/Gig A/Pad.fxp", "category": "!Quick Access", "inner_segments": ("Gig A",), "instrument_primary": "pad"}],
            },
            "Bass": {
                "root": [{"name": "Root", "path": "/factory/Bass/Root.fxp", "category": "Bass", "inner_segments": (), "instrument_primary": "bass"}],
                "Sub": [{"name": "Deep", "path": "/factory/Bass/Sub/Deep.fxp", "category": "Bass", "inner_segments": ("Sub",), "instrument_primary": "bass"}],
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
                return list(tree.get("root", []))
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
        if not self.instrument_filter:
            return True
        return patch.get("instrument_primary") == self.instrument_filter

    def _instrument_filter_active(self) -> bool:
        return self.instrument_filter is not None

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


class _LongPressHost(TouchBrowserContextMixin):
    def __init__(self) -> None:
        self.left_nav_collapsed = False
        self.screen_state = Screen.BROWSER
        self.left_nav_mode = LeftNavMode.PATCHES
        self.categories = ["Bass"]
        self._browse_nav_entries = [
            {"kind": "patch", "label": "Acid", "patch": {"name": "Acid", "category": "Bass"}},
        ]
        self.nav_list = mock.Mock()
        self.nav_list.rect.contains.return_value = True
        self.nav_list.item_at.return_value = 0
        self.nav_list._pointer_scrolled = False
        self.width = 800
        self.height = 480
        self._init_context_menu_state()
        self._opened: list[ContextTarget] = []

    def _open_context_menu(self, target: ContextTarget) -> None:
        self._opened.append(target)

    def _patch_is_favorited(self, _patch: dict) -> bool:
        return False

    def _browse_category_name(self) -> str:
        return "Bass"

    def _browse_inner_segments(self) -> tuple[str, ...]:
        return ()


class _NormHost(TouchBrowserPatchesMixin, TouchBrowserNormalizationMixin):
    def __init__(self) -> None:
        self.loader = mock.Mock()
        self.loader.osc_enabled = True
        self.loader.normalization = mock.Mock()
        self.loader.normalization.is_globally_enabled.return_value = True
        self.loader.normalization.is_enabled.return_value = False
        self.loader.normalization.refs_match.return_value = True
        self.loader.normalization.get_raw_gain_db.return_value = 6.0
        self.surge_monitor = mock.Mock()
        self.volume_level = 1.0
        self.detail_patch = {
            "name": "Acid",
            "category": "Bass",
            "path": "/patches/Bass/Acid.fxp",
        }
        self.loaded_patch_info = dict(self.detail_patch)
        self.toast_message = ""
        self.layout_calls = 0

    def _toast(self, message: str, seconds: float = 2.0) -> None:
        self.toast_message = message

    def _apply_volume(self, level: float, *, persist: bool = True) -> None:
        self.volume_level = level

    def _note_surge_patch_load_success(self) -> None:
        return None

    def _surge_ready_for_patch_load(self) -> bool:
        return True

    def _layout(self) -> None:
        self.layout_calls += 1


class _PatchHost(TouchBrowserPatchesMixin):
    def __init__(self) -> None:
        self.loader = mock.Mock()
        self.loader.osc_enabled = True
        self.surge_monitor = mock.Mock()
        self.scanner = mock.Mock()
        self.categories: list[str] = []
        self.browse_folder_index = 0
        self.loaded_folder_index = 0
        self.left_nav_mode = mock.Mock()
        self.detail_patch = None
        self.loaded_patch_info = None
        self.volume_level = 1.0
        self._pending_last_patch = None
        self._pending_load_next = 0.0
        self._last_known_surge_pid = None
        self._surge_was_healthy = False
        self._surge_liveness_initialized = False

    def _refresh_lists(self, *, scroll_to_selection: bool = False) -> None:
        return None

    def _apply_volume(self, level: float, *, persist: bool = True) -> None:
        return None

    def _toast(self, message: str, seconds: float = 2.0) -> None:
        return None

    def _layout(self) -> None:
        return None

    def _sync_pressure_live(self, floor: float | None = None) -> None:
        return None


class BrowseGestureZonesTests(unittest.TestCase):
    def test_edge_zone_matches_left_panel_column(self) -> None:
        host = _BrowseHost()
        zones = host._browse_gesture_zones()
        self.assertEqual(zones.edge.x, 0)
        self.assertEqual(zones.edge.w, BROWSE_EDGE_GRAB_W)
        self.assertEqual(zones.edge.y, host.left_panel_rect.y)
        self.assertEqual(zones.edge.h, host.left_panel_rect.h)

    def test_filter_zone_none_at_home_stop(self) -> None:
        host = _BrowseHost()
        host.browse_filter_rect = Rect(-532, 40, 532, 400)
        zones = host._browse_gesture_zones()
        self.assertIsNone(zones.filter)

    def test_filter_zone_not_routed_at_filter_stop(self) -> None:
        host = _BrowseHost()
        _goto_filter_stop(host, [])
        zones = host._browse_gesture_zones()
        self.assertIsNone(zones.filter)


class PointerDownClaimTests(unittest.TestCase):
    def test_inactive_when_nav_collapsed(self) -> None:
        host = _BrowseHost()
        host.left_nav_collapsed = True
        self.assertFalse(host._handle_browse_pointer_down((10, 100)))

    def test_edge_zone_claims_and_begins_drag_when_enabled(self) -> None:
        from patch_browser.touch_ui_constants import BROWSE_DRAG_ENABLED

        if not BROWSE_DRAG_ENABLED:
            self.skipTest("BROWSE_DRAG_ENABLED is false — buttons are primary nav")
        host = _BrowseHost()
        claimed = host._handle_browse_pointer_down((10, 100))
        self.assertTrue(claimed)
        self.assertTrue(host._browse_carousel.state.dragging)

    def test_filter_button_toggles_filter_and_home(self) -> None:
        host = _BrowseHost()
        host.browse_filter_open_btn = Rect(200, 4, 32, 28)
        host._toggle_browse_filter()
        self.assertEqual(host._browse_carousel.stop, "filter")
        self.assertEqual(host._browse_carousel.offset_px, BROWSE_OFFSET_FILTER)
        host._toggle_browse_filter()
        self.assertEqual(host._browse_carousel.stop, "home")
        self.assertEqual(host._browse_carousel.offset_px, BROWSE_OFFSET_HOME)

    def test_elsewhere_at_home_stop_not_claimed(self) -> None:
        host = _BrowseHost()
        claimed = host._handle_browse_pointer_down((150, 100))
        self.assertFalse(claimed)
        self.assertFalse(host._browse_carousel.state.dragging)

    def test_filter_tag_claims_at_filter_stop(self) -> None:
        host = _BrowseHost()
        tag_rect = Rect(100, 60, 80, 48)
        _goto_filter_stop(host, [("bass", tag_rect)])
        claimed = host._handle_browse_pointer_down((110, 80))
        self.assertTrue(claimed)
        self.assertTrue(host._browse_filter_tap_active)
        self.assertEqual(host._browse_filter_tap_tag, "bass")

    def test_filter_pane_body_not_claimed_when_drag_disabled(self) -> None:
        host = _BrowseHost()
        _goto_filter_stop(host, [])
        claimed = host._handle_browse_pointer_down((200, 200))
        self.assertFalse(claimed)
        self.assertFalse(host._browse_carousel.state.dragging)


class PointerMoveTests(unittest.TestCase):
    def test_dragging_updates_offset_when_enabled(self) -> None:
        from patch_browser.touch_ui_constants import BROWSE_DRAG_ENABLED

        if not BROWSE_DRAG_ENABLED:
            self.skipTest("BROWSE_DRAG_ENABLED is false")
        host = _BrowseHost()
        host._handle_browse_pointer_down((10, 100))
        start_offset = host._browse_carousel.offset_px
        claimed = host._handle_browse_pointer_move((60, 100))
        self.assertTrue(claimed)
        self.assertEqual(host._browse_carousel.offset_px, start_offset + 50)
        self.assertEqual(host._layout_calls, 1)

    def test_not_active_returns_false(self) -> None:
        host = _BrowseHost()
        self.assertFalse(host._handle_browse_pointer_move((400, 100)))
        self.assertEqual(host._layout_calls, 0)


class PointerUpTests(unittest.TestCase):
    def test_drag_release_when_enabled(self) -> None:
        from patch_browser.touch_ui_constants import BROWSE_DRAG_ENABLED

        if not BROWSE_DRAG_ENABLED:
            self.skipTest("BROWSE_DRAG_ENABLED is false")
        host = _BrowseHost()
        host._handle_browse_pointer_down((10, 100))
        host._handle_browse_pointer_move((70, 100))
        claimed = host._handle_browse_pointer_up((70, 100))
        self.assertTrue(claimed)
        self.assertFalse(host._browse_carousel.state.dragging)
        self.assertEqual(host._browse_carousel.stop, "filter")
        self.assertEqual(host._layout_calls, 2)

    def test_tag_release_on_same_tag_sets_filter_keeps_stop(self) -> None:
        host = _BrowseHost()
        tag_rect = Rect(100, 60, 80, 48)
        _goto_filter_stop(host, [("bass", tag_rect)])
        host._handle_browse_pointer_down((110, 80))
        claimed = host._handle_browse_pointer_up((115, 82))
        self.assertTrue(claimed)
        self.assertEqual(host.instrument_filter, "bass")
        self.assertEqual(host._browse_carousel.stop, "filter")

    def test_tag_release_off_tag_does_not_set_filter(self) -> None:
        host = _BrowseHost()
        tag_rect = Rect(100, 60, 80, 24)
        _goto_filter_stop(host, [("bass", tag_rect)])
        host._handle_browse_pointer_down((110, 65))
        claimed = host._handle_browse_pointer_up((300, 300))
        self.assertTrue(claimed)
        self.assertIsNone(host.instrument_filter)

    def test_no_stray_instrument_filter_expanded_attribute(self) -> None:
        host = _BrowseHost()
        tag_rect = Rect(100, 60, 80, 24)
        _goto_filter_stop(host, [("bass", tag_rect)])
        host._handle_browse_pointer_down((110, 65))
        host._handle_browse_pointer_up((110, 65))
        self.assertFalse(hasattr(host, "instrument_filter_expanded"))

    def test_not_active_returns_false(self) -> None:
        host = _BrowseHost()
        self.assertFalse(host._handle_browse_pointer_up((400, 100)))


class FilterPaneContentPositionTests(unittest.TestCase):
    def test_home_stop_tags_never_bleed_onscreen(self) -> None:
        host = _BrowseHost()
        host.browse_filter_rect = Rect(BROWSE_OFFSET_HOME, 40, 532, 400)
        host._refresh_instrument_chips()
        self.assertGreater(len(host._browse_filter_packed_tags), 0)
        for tag in host._browse_filter_packed_tags:
            self.assertLessEqual(tag.rect.right, 0)

    def test_filter_stop_tags_start_past_edge_grab_strip(self) -> None:
        host = _BrowseHost()
        host.browse_filter_rect = Rect(0, 40, 532, 400)
        host._refresh_instrument_chips()
        self.assertGreater(len(host._browse_filter_packed_tags), 0)
        self.assertEqual(host._browse_filter_packed_tags[0].rect.x, BROWSE_EDGE_GRAB_W)


class FilterPaneDragRepackSkipTests(unittest.TestCase):
    def test_dragging_reuses_cached_tag_ids_without_rewalking_context(self) -> None:
        host = _BrowseHost()
        host.browse_filter_rect = Rect(0, 40, 532, 400)
        host._refresh_instrument_chips()
        calls_before_drag = host._context_patches_calls
        self.assertGreater(calls_before_drag, 0)

        host._browse_carousel.state.dragging = True
        host.browse_filter_rect = Rect(-200, 40, 532, 400)
        host._refresh_instrument_chips()

        self.assertEqual(host._context_patches_calls, calls_before_drag)
        self.assertGreater(len(host._browse_filter_packed_tags), 0)
        self.assertEqual(host._browse_filter_packed_tags[0].rect.x, -200 + BROWSE_EDGE_GRAB_W)

    def test_drag_end_recomputes_from_fresh_context(self) -> None:
        host = _BrowseHost()
        host.browse_filter_rect = Rect(0, 40, 532, 400)
        host._refresh_instrument_chips()
        calls_before_drag = host._context_patches_calls

        host._browse_carousel.state.dragging = True
        host._refresh_instrument_chips()
        self.assertEqual(host._context_patches_calls, calls_before_drag)

        host._browse_carousel.state.dragging = False
        host._refresh_instrument_chips()
        self.assertGreater(host._context_patches_calls, calls_before_drag)


class HomeStopDefaultTests(_BrowseTrackLayoutTestCase):
    def test_default_stop_is_home_nav_and_patch_visible(self) -> None:
        browser = self._make_browser()
        self.assertEqual(browser.left_nav_mode, LeftNavMode.PATCHES)
        self.assertEqual(browser._browse_carousel.stop, "home")
        self.assertEqual(browser.left_panel_rect.x, 0)
        self.assertEqual(browser.left_panel_rect.w, LEFT_NAV_WIDTH)
        self.assertEqual(browser.main_rect.x, LEFT_NAV_WIDTH)
        self.assertEqual(browser.main_rect.w, BROWSE_PATCH_W)

    def test_filter_pane_offscreen_left_at_home(self) -> None:
        browser = self._make_browser()
        self.assertEqual(browser.browse_filter_rect.x, BROWSE_OFFSET_HOME)


class FilterStopTests(_BrowseTrackLayoutTestCase):
    def test_filter_stop_shows_filter_and_nav(self) -> None:
        browser = self._make_browser()
        browser._browse_carousel.state.stop = "filter"
        browser._browse_carousel.state.offset_px = BROWSE_OFFSET_FILTER
        browser._layout()

        self.assertEqual(browser.browse_filter_rect.x, 0)
        self.assertEqual(browser.browse_filter_rect.w, BROWSE_FILTER_W)
        self.assertEqual(browser.left_panel_rect.x, BROWSE_FILTER_W)
        self.assertEqual(browser.main_rect.x, BROWSE_FILTER_W + LEFT_NAV_WIDTH)


class CarouselDisabledFallbackTests(_BrowseTrackLayoutTestCase):
    def test_collapsed_nav_falls_back_to_legacy_margin_layout(self) -> None:
        browser = self._make_browser()
        browser.left_nav_collapsed = True
        browser._layout()

        self.assertFalse(browser._browse_carousel_active())
        self.assertEqual(browser.left_panel_rect.x, 16)
        self.assertEqual(browser.browse_filter_rect.w, 0)

    def test_all_patches_mode_falls_back_to_legacy_layout(self) -> None:
        browser = self._make_browser()
        browser.left_nav_mode = LeftNavMode.ALL_PATCHES
        browser._layout()

        self.assertFalse(browser._browse_carousel_active())
        self.assertEqual(browser.left_panel_rect.x, 16)
        self.assertEqual(browser.browse_filter_rect.w, 0)
        self.assertGreater(browser.az_rail_rect.w, 0)


class NavListFullHeightTests(_BrowseTrackLayoutTestCase):
    def test_nav_list_top_has_no_chip_offset(self) -> None:
        browser = self._make_browser()
        content_top = browser.status_rect.y + browser.status_rect.h + 10
        nav_header_h = 36
        folder_title_h = 34 if not browser.left_nav_collapsed else 0
        expected_top = content_top + nav_header_h + 4 + folder_title_h
        self.assertEqual(browser.nav_list.rect.y, expected_top)


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
                "patch_browser.touch_browser_wifi_modal.wifi_settings_row_label",
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
        browser.loader.load_patch.assert_called()

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


class NestedFolderBrowseTests(unittest.TestCase):
    def test_browse_list_shows_subfolders_then_patches(self) -> None:
        host = _NestedBrowseHost()
        host.browse_folder_index = 1
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
        host._select_nav_index(0)
        self.assertEqual(host.browse_inner_segments, ("Sub",))

    def test_select_patch_row_loads_patch(self) -> None:
        host = _NestedBrowseHost()
        host.browse_folder_index = 1
        host.left_nav_mode = LeftNavMode.PATCHES
        host._refresh_lists()
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

    def test_instrument_filter_hides_folders_shows_recursive_patches(self) -> None:
        host = _NestedBrowseHost()
        host.browse_folder_index = 1
        host.left_nav_mode = LeftNavMode.PATCHES
        host.instrument_filter = "bass"
        host._refresh_lists()

        labels = host.nav_list.set_items.call_args[0][0]
        self.assertNotIn("Sub  >", labels)
        self.assertEqual(labels, ["Root", "Deep"])

    def test_instrument_filter_scoped_to_current_subfolder(self) -> None:
        host = _NestedBrowseHost()
        host.browse_folder_index = 1
        host.browse_inner_segments = ("Sub",)
        host.left_nav_mode = LeftNavMode.PATCHES
        host.instrument_filter = "bass"
        host._refresh_lists()

        labels = host.nav_list.set_items.call_args[0][0]
        self.assertEqual(labels, ["Deep"])

    def test_instrument_filter_highlights_patch_in_subfolder(self) -> None:
        host = _NestedBrowseHost()
        host.browse_folder_index = 1
        host.left_nav_mode = LeftNavMode.PATCHES
        host.instrument_filter = "bass"
        host.detail_patch = {
            "name": "Deep",
            "category": "Bass",
            "path": "/factory/Bass/Sub/Deep.fxp",
            "inner_segments": ("Sub",),
            "instrument_primary": "bass",
        }
        host._refresh_lists()

        highlight = host.nav_list.set_items.call_args[1]["highlight_index"]
        self.assertEqual(highlight, 1)

    def test_clearing_instrument_filter_restores_folder_rows(self) -> None:
        host = _NestedBrowseHost()
        host.browse_folder_index = 1
        host.left_nav_mode = LeftNavMode.PATCHES
        host.instrument_filter = "bass"
        host._refresh_lists()
        host.instrument_filter = None
        host._refresh_lists()

        labels = host.nav_list.set_items.call_args[0][0]
        self.assertEqual(labels[0], "Sub  >")
        self.assertIn("Root", labels)


class LongPressTickTests(unittest.TestCase):
    def test_tick_opens_menu_after_hold_without_scroll(self) -> None:
        host = _LongPressHost()
        host._context_nav_pointer_down((100, 200))
        host._long_press_pending["started"] = time.time() - 0.7
        host._tick_long_press()
        self.assertEqual(len(host._opened), 1)
        self.assertEqual(host._opened[0].kind, "patch")

    def test_tick_cancels_when_list_scrolled(self) -> None:
        host = _LongPressHost()
        host._context_nav_pointer_down((100, 200))
        host.nav_list._pointer_scrolled = True
        host._long_press_pending["started"] = time.time() - 0.7
        host._tick_long_press()
        self.assertEqual(host._opened, [])


class NormToggleReloadTests(unittest.TestCase):
    def test_per_patch_toggle_refreshes_volume_without_full_reload(self) -> None:
        host = _NormHost()

        host._toggle_normalization()

        host.loader.normalization.set_enabled.assert_called_once_with(
            "Acid",
            True,
            patch_path="/patches/Bass/Acid.fxp",
            stable_key=None,
        )
        host.loader.load_patch.assert_not_called()
        host.loader.refresh_patch_volume.assert_called_once_with("Acid")
        self.assertEqual(host.layout_calls, 1)

    def test_per_patch_toggle_skips_refresh_when_osc_disabled(self) -> None:
        host = _NormHost()
        host.loader.osc_enabled = False

        host._toggle_normalization()

        host.loader.refresh_patch_volume.assert_not_called()


class SurgeReadyForPatchLoadTests(unittest.TestCase):
    def test_false_when_osc_disabled(self) -> None:
        host = _PatchHost()
        host.loader.osc_enabled = False
        self.assertFalse(host._surge_ready_for_patch_load())

    def test_false_when_osc_port_closed(self) -> None:
        host = _PatchHost()
        host.surge_monitor.check_health.return_value = (True, None)
        host.surge_monitor.osc_port_in_use.return_value = False
        self.assertFalse(host._surge_ready_for_patch_load())

    def test_true_when_healthy_and_port_open(self) -> None:
        host = _PatchHost()
        host.surge_monitor.check_health.return_value = (True, None)
        host.surge_monitor.osc_port_in_use.return_value = True
        self.assertTrue(host._surge_ready_for_patch_load())


class BootstrapPatchesTests(unittest.TestCase):
    def test_queues_pending_when_surge_not_ready(self) -> None:
        host = _PatchHost()
        host.scanner.patches = {}
        host.scanner.load_last_patch.return_value = {
            "category": "Bass",
            "patch_path": "/tmp/test.fxp",
        }
        host.scanner.quick_scan_category.return_value = [{"name": "test", "path": "/tmp/test.fxp"}]
        host.surge_monitor.check_health.return_value = (False, "down")
        host.surge_monitor.osc_port_in_use.return_value = False

        host._bootstrap_patches()

        self.assertIsNotNone(host._pending_last_patch)
        self.assertIsNone(host.loaded_patch_info)
        self.assertGreater(host._pending_load_next, time.time())


class SurgeRequeueTests(unittest.TestCase):
    def test_pid_change_requeues_loaded_patch(self) -> None:
        host = _PatchHost()
        host._surge_liveness_initialized = True
        host._surge_was_healthy = True
        host._last_known_surge_pid = 100
        host.loaded_patch_info = {"name": "A", "category": "B", "path": "/p/a.fxp"}
        host.surge_monitor.check_health.return_value = (True, None)
        host.surge_monitor.surge_pid = 200

        host._maybe_requeue_patch_after_surge_change()

        self.assertIsNotNone(host._pending_last_patch)
        self.assertEqual(host._pending_last_patch["name"], "A")

    def test_first_call_initializes_without_requeue(self) -> None:
        host = _PatchHost()
        host.loaded_patch_info = {"name": "A", "category": "B", "path": "/p/a.fxp"}
        host.surge_monitor.check_health.return_value = (True, None)
        host.surge_monitor.surge_pid = 42

        host._maybe_requeue_patch_after_surge_change()

        self.assertIsNone(host._pending_last_patch)
        self.assertTrue(host._surge_liveness_initialized)
        self.assertEqual(host._last_known_surge_pid, 42)


class ProfileSwitchReloadTests(unittest.TestCase):
    def test_skips_liveness_requeue_while_profile_switch_active(self) -> None:
        host = _PatchHost()
        host._surge_liveness_initialized = True
        host._last_known_surge_pid = 1
        host._surge_was_healthy = True
        host.loaded_patch_info = {"name": "A", "category": "B", "path": "/p/a.fxp"}
        host._profile_switch_reload_active = True
        host.surge_monitor.check_health.return_value = (True, None)
        host.surge_monitor.surge_pid = 99

        host._maybe_requeue_patch_after_surge_change()

        self.assertIsNone(host._pending_last_patch)

    def test_profile_switch_sends_load_twice(self) -> None:
        host = _PatchHost()
        patch = {"name": "Lead", "category": "Synth", "path": "/p/lead.fxp"}
        host._pending_last_patch = dict(patch)
        host._pending_load_next = 0.0
        host._profile_switch_reload_active = True
        host._profile_switch_sent_once = False
        host.surge_monitor.check_health.return_value = (True, None)
        host.surge_monitor.osc_port_in_use.return_value = True
        host.loader.load_patch.return_value = True

        host._retry_pending_load()
        self.assertTrue(host._profile_switch_sent_once)
        self.assertIsNotNone(host._pending_last_patch)
        self.assertGreater(host._pending_load_next, time.time())

        host._pending_load_next = 0.0
        host._retry_pending_load()
        self.assertFalse(host._profile_switch_reload_active)
        self.assertIsNone(host._pending_last_patch)
        self.assertEqual(host.loader.load_patch.call_count, 2)


if __name__ == "__main__":
    unittest.main()
