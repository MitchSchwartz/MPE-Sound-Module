"""Phase C — pointer-down routing tests for the browse carousel mixin.

Lightweight host (no full TouchPatchBrowser/pygame display) mixing in
TouchBrowserBrowseMixin + TouchBrowserInstrumentsMixin — same style as
test_touch_browser_long_press.py's `_LongPressHost`. See
Documents/specs/touch-browser-browse-carousel-spec.md §Gesture
architecture and acceptance criteria 7/8.
"""

from __future__ import annotations

import sys
import types
import unittest
from unittest import mock


def _install_fake_pygame() -> None:
    # This file imports touch_browser_instruments.py, which does its own
    # top-level `import pygame` for `pygame.draw` (used by
    # _draw_browse_filter_pane, not exercised by these tests directly).
    # That binding is cached at first import for the whole process, so
    # an incomplete stub here would silently break *other* test files
    # that later build/draw a real browser. Rather than hand-picking
    # attributes and playing whack-a-mole, this is the exact fake from
    # test_touch_browser_browse_track.py / test_touch_browser_smoke.py
    # — proven complete enough to construct a real TouchPatchBrowser.
    if isinstance(sys.modules.get("pygame"), types.ModuleType) and hasattr(
        sys.modules["pygame"], "display"
    ):
        return

    fake = types.ModuleType("pygame")

    fake.QUIT = 12
    fake.MOUSEBUTTONDOWN = 1025
    fake.MOUSEBUTTONUP = 1026
    fake.MOUSEMOTION = 1024
    fake.FINGERDOWN = 1792
    fake.FINGERUP = 1793
    fake.FINGERMOTION = 1794
    fake.SRCALPHA = 65536
    fake.FULLSCREEN = 1

    class _FakeRect:
        def __init__(self, *args, **kwargs):
            if len(args) == 4:
                self.x, self.y, self.w, self.h = args
            elif len(args) == 1 and isinstance(args[0], tuple):
                self.x, self.y, self.w, self.h = args[0]
            else:
                self.x = self.y = self.w = self.h = 0
            self.centery = self.y + self.h // 2

        @property
        def right(self) -> int:
            return self.x + self.w

        @property
        def bottom(self) -> int:
            return self.y + self.h

    class _FakeSurface:
        def __init__(self, size, flags=0):
            self.size = size

        def fill(self, *_args, **_kwargs) -> None:
            return None

        def blit(self, *_args, **_kwargs) -> None:
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

        def render(self, text, _antialias, _color):
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
    fake.init = mock.Mock()
    fake.quit = mock.Mock()
    fake.display = types.SimpleNamespace(
        set_caption=mock.Mock(),
        set_mode=mock.Mock(return_value=_FakeSurface((800, 480))),
        flip=mock.Mock(),
    )
    fake.mouse = types.SimpleNamespace(set_visible=mock.Mock())
    fake.time = types.SimpleNamespace(Clock=mock.Mock(return_value=mock.Mock(get_time=mock.Mock(return_value=16))))
    fake.font = types.SimpleNamespace(
        Font=_FakeFont,
        match_font=mock.Mock(return_value="/tmp/fake-font.ttf"),
    )
    fake.draw = types.SimpleNamespace(
        rect=mock.Mock(),
        line=mock.Mock(),
        lines=mock.Mock(),
        polygon=mock.Mock(),
        circle=mock.Mock(),
    )

    sys.modules["pygame"] = fake


_install_fake_pygame()

from patch_browser.geometry import Rect  # noqa: E402
from patch_browser.touch_browser_browse import TouchBrowserBrowseMixin  # noqa: E402
from patch_browser.touch_browser_instruments import TouchBrowserInstrumentsMixin  # noqa: E402
from patch_browser.touch_ui_constants import BROWSE_EDGE_GRAB_W, BROWSE_OFFSET_FILTER  # noqa: E402
from patch_browser.touch_ui_enums import LeftNavMode  # noqa: E402


class _BrowseHost(TouchBrowserBrowseMixin, TouchBrowserInstrumentsMixin):
    def __init__(self) -> None:
        self.left_nav_mode = LeftNavMode.PATCHES
        self.left_nav_collapsed = False
        self.left_panel_rect = Rect(BROWSE_OFFSET_FILTER + 532, 40, 268, 400)
        self.all_patches_flat: list[dict] = []
        self.nav_list = mock.Mock()
        self._init_browse_carousel_state()
        self._init_instrument_filter_state()
        self._layout_calls = 0

    def _layout(self) -> None:  # stand-in for the real, heavy _layout()
        self._layout_calls += 1

    def _set_instrument_filter(self, instrument: str | None) -> None:
        # Skip the real masonry/list refresh plumbing — just record it.
        self.instrument_filter = instrument


def _goto_filter_stop(host: _BrowseHost, tag_rects: list[tuple[str, Rect]]) -> None:
    host._browse_carousel.state.stop = "filter"
    host._browse_carousel.state.offset_px = BROWSE_OFFSET_FILTER
    host.browse_filter_rect = Rect(0, 40, 532, 400)
    host.browse_filter_tag_rects = tag_rects


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

    def test_filter_zone_present_at_filter_stop(self) -> None:
        host = _BrowseHost()
        _goto_filter_stop(host, [])
        zones = host._browse_gesture_zones()
        self.assertIsNotNone(zones.filter)
        self.assertEqual(zones.filter.w, 532)


class PointerDownClaimTests(unittest.TestCase):
    def test_inactive_when_nav_collapsed(self) -> None:
        host = _BrowseHost()
        host.left_nav_collapsed = True
        self.assertFalse(host._handle_browse_pointer_down((10, 100)))

    def test_edge_zone_claims_and_begins_drag(self) -> None:
        host = _BrowseHost()
        claimed = host._handle_browse_pointer_down((10, 100))
        self.assertTrue(claimed)
        self.assertTrue(host._browse_carousel.state.dragging)

    def test_elsewhere_at_home_stop_not_claimed(self) -> None:
        # Acceptance criterion 5 analogue: nav-column taps fall through.
        host = _BrowseHost()
        claimed = host._handle_browse_pointer_down((150, 100))
        self.assertFalse(claimed)
        self.assertFalse(host._browse_carousel.state.dragging)

    def test_filter_tag_claims_at_filter_stop(self) -> None:
        host = _BrowseHost()
        tag_rect = Rect(100, 60, 80, 24)
        _goto_filter_stop(host, [("bass", tag_rect)])
        claimed = host._handle_browse_pointer_down((110, 65))
        self.assertTrue(claimed)
        self.assertTrue(host._browse_filter_tap_active)
        self.assertEqual(host._browse_filter_tap_tag, "bass")


class PointerMoveTests(unittest.TestCase):
    def test_dragging_updates_offset_and_relayouts(self) -> None:
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
    def test_drag_release_ends_drag_and_relayouts(self) -> None:
        host = _BrowseHost()
        host._handle_browse_pointer_down((10, 100))
        host._handle_browse_pointer_move((70, 100))  # past BROWSE_SNAP_COMMIT_PX
        claimed = host._handle_browse_pointer_up((70, 100))
        self.assertTrue(claimed)
        self.assertFalse(host._browse_carousel.state.dragging)
        self.assertEqual(host._browse_carousel.stop, "filter")
        self.assertEqual(host._layout_calls, 2)  # one on move, one on up

    def test_tag_release_on_same_tag_sets_filter_keeps_stop(self) -> None:
        # Acceptance criterion 7.
        host = _BrowseHost()
        tag_rect = Rect(100, 60, 80, 24)
        _goto_filter_stop(host, [("bass", tag_rect)])
        host._handle_browse_pointer_down((110, 65))
        claimed = host._handle_browse_pointer_up((115, 68))
        self.assertTrue(claimed)
        self.assertEqual(host.instrument_filter, "bass")
        self.assertEqual(host._browse_carousel.stop, "filter")

    def test_tag_release_off_tag_does_not_set_filter(self) -> None:
        host = _BrowseHost()
        tag_rect = Rect(100, 60, 80, 24)
        _goto_filter_stop(host, [("bass", tag_rect)])
        host._handle_browse_pointer_down((110, 65))
        claimed = host._handle_browse_pointer_up((300, 300))
        self.assertTrue(claimed)  # gesture still claimed, just no selection
        self.assertIsNone(host.instrument_filter)

    def test_no_stray_instrument_filter_expanded_attribute(self) -> None:
        # Acceptance criterion 8: the field is removed, not merely inert.
        host = _BrowseHost()
        tag_rect = Rect(100, 60, 80, 24)
        _goto_filter_stop(host, [("bass", tag_rect)])
        host._handle_browse_pointer_down((110, 65))
        host._handle_browse_pointer_up((110, 65))
        self.assertFalse(hasattr(host, "instrument_filter_expanded"))

    def test_not_active_returns_false(self) -> None:
        host = _BrowseHost()
        self.assertFalse(host._handle_browse_pointer_up((400, 100)))


if __name__ == "__main__":
    unittest.main()
