"""Unit tests for patch_browser.gesture_router zone classification.

Synthetic 800x480 layout rects standing in for the browse carousel's
Home and Filter stops (Phase A gate — see
Documents/specs/touch-browser-browse-carousel-spec.md).
"""

from __future__ import annotations

import unittest

from tests.fake_pygame import install_fake_pygame

install_fake_pygame()

from patch_browser.geometry import Rect  # noqa: E402
from patch_browser.gesture_router import (  # noqa: E402
    BrowseGestureZones,
    GestureKind,
    classify_pointer_down,
)
from patch_browser.touch_ui_constants import BROWSE_EDGE_GRAB_W, LEFT_NAV_WIDTH  # noqa: E402

CONTENT_TOP = 40
CONTENT_H = 440


def _home_zones() -> BrowseGestureZones:
    """Home stop: Nav + Patch visible, Filter off-screen left."""
    edge = Rect(0, CONTENT_TOP, BROWSE_EDGE_GRAB_W, CONTENT_H)
    nav = Rect(BROWSE_EDGE_GRAB_W, CONTENT_TOP, LEFT_NAV_WIDTH - BROWSE_EDGE_GRAB_W, CONTENT_H)
    mixer = [Rect(LEFT_NAV_WIDTH, CONTENT_TOP, 54, 168)]  # fader column stand-in
    return BrowseGestureZones(edge=edge, nav=nav, mixer=mixer, filter=None)


def _filter_zones() -> BrowseGestureZones:
    """Filter stop: Filter + Nav visible, Patch off-screen right."""
    edge = Rect(0, CONTENT_TOP, BROWSE_EDGE_GRAB_W, CONTENT_H)
    filter_rect = Rect(BROWSE_EDGE_GRAB_W, CONTENT_TOP, 532 - BROWSE_EDGE_GRAB_W, CONTENT_H)
    nav = Rect(532, CONTENT_TOP, LEFT_NAV_WIDTH, CONTENT_H)
    return BrowseGestureZones(edge=edge, nav=nav, mixer=[], filter=filter_rect)


class ClassifyPointerDownTests(unittest.TestCase):
    def test_left_edge_is_always_carousel(self) -> None:
        # Acceptance criterion 4: x=30 always classifies edge_carousel,
        # regardless of stop.
        self.assertEqual(
            classify_pointer_down(30, 100, _home_zones()), GestureKind.EDGE_CAROUSEL
        )
        self.assertEqual(
            classify_pointer_down(30, 100, _filter_zones()), GestureKind.EDGE_CAROUSEL
        )

    def test_nav_list_at_home_stop(self) -> None:
        # Acceptance criterion 5: nav x=120 classifies nav_scroll, not carousel.
        self.assertEqual(
            classify_pointer_down(120, 100, _home_zones()), GestureKind.NAV_SCROLL
        )

    def test_fader_column_is_mixer_not_carousel(self) -> None:
        # Acceptance criterion 6.
        zones = _home_zones()
        fader_rect = zones.mixer[0]
        self.assertEqual(
            classify_pointer_down(fader_rect.centerx, fader_rect.centery, zones),
            GestureKind.MIXER,
        )

    def test_filter_pane_tap_at_filter_stop(self) -> None:
        zones = _filter_zones()
        assert zones.filter is not None
        self.assertEqual(
            classify_pointer_down(zones.filter.centerx, zones.filter.centery, zones),
            GestureKind.FILTER_TAP,
        )

    def test_nav_list_at_filter_stop(self) -> None:
        zones = _filter_zones()
        assert zones.nav is not None
        self.assertEqual(
            classify_pointer_down(zones.nav.centerx, zones.nav.centery, zones),
            GestureKind.NAV_SCROLL,
        )

    def test_mixer_takes_priority_over_filter(self) -> None:
        overlap = Rect(100, 100, 50, 50)
        zones = BrowseGestureZones(
            edge=Rect(0, 0, BROWSE_EDGE_GRAB_W, 480),
            filter=overlap,
            mixer=[overlap],
        )
        self.assertEqual(classify_pointer_down(110, 110, zones), GestureKind.MIXER)

    def test_filter_takes_priority_over_nav(self) -> None:
        overlap = Rect(100, 100, 50, 50)
        zones = BrowseGestureZones(
            edge=Rect(0, 0, BROWSE_EDGE_GRAB_W, 480),
            filter=overlap,
            nav=overlap,
        )
        self.assertEqual(classify_pointer_down(110, 110, zones), GestureKind.FILTER_TAP)

    def test_elsewhere_falls_back_to_tap(self) -> None:
        zones = _home_zones()
        self.assertEqual(classify_pointer_down(790, 470, zones), GestureKind.TAP)


if __name__ == "__main__":
    unittest.main()
