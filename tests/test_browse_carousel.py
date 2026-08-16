"""Unit tests for patch_browser.browse_carousel offset/snap math.

Phase A gate — see
Documents/specs/touch-browser-browse-carousel-spec.md §Browse carousel
interaction and §Acceptance criteria 2/3.
"""

from __future__ import annotations

import unittest

from patch_browser.browse_carousel import BrowseCarousel, BrowseCarouselState
from patch_browser.touch_ui_constants import (
    BROWSE_OFFSET_FILTER,
    BROWSE_OFFSET_HOME,
    BROWSE_SNAP_COMMIT_PX,
)


class BrowseCarouselDefaultsTests(unittest.TestCase):
    def test_default_state_is_home(self) -> None:
        carousel = BrowseCarousel()
        self.assertEqual(carousel.stop, "home")
        self.assertEqual(carousel.offset_px, BROWSE_OFFSET_HOME)


class FollowFingerTests(unittest.TestCase):
    def test_offset_tracks_finger_dx(self) -> None:
        carousel = BrowseCarousel()
        carousel.begin_drag(x=20)
        carousel.update_drag(x=50)
        self.assertEqual(carousel.offset_px, BROWSE_OFFSET_HOME + 30)

    def test_offset_clamped_at_filter_bound(self) -> None:
        carousel = BrowseCarousel()
        carousel.begin_drag(x=0)
        carousel.update_drag(x=10_000)
        self.assertEqual(carousel.offset_px, BROWSE_OFFSET_FILTER)

    def test_offset_clamped_at_home_bound(self) -> None:
        state = BrowseCarouselState(offset_px=BROWSE_OFFSET_FILTER, stop="filter")
        carousel = BrowseCarousel(state)
        carousel.begin_drag(x=0)
        carousel.update_drag(x=-10_000)
        self.assertEqual(carousel.offset_px, BROWSE_OFFSET_HOME)

    def test_update_drag_before_begin_drag_is_noop(self) -> None:
        carousel = BrowseCarousel()
        carousel.update_drag(x=200)
        self.assertEqual(carousel.offset_px, BROWSE_OFFSET_HOME)


class SnapCommitTests(unittest.TestCase):
    def test_home_to_filter_commits_past_threshold(self) -> None:
        # Acceptance criterion 2.
        carousel = BrowseCarousel()
        carousel.begin_drag(x=0)
        carousel.update_drag(x=BROWSE_SNAP_COMMIT_PX)
        stop = carousel.end_drag()
        self.assertEqual(stop, "filter")
        self.assertEqual(carousel.offset_px, BROWSE_OFFSET_FILTER)
        self.assertFalse(carousel.state.dragging)

    def test_home_to_filter_reverts_under_threshold(self) -> None:
        carousel = BrowseCarousel()
        carousel.begin_drag(x=0)
        carousel.update_drag(x=BROWSE_SNAP_COMMIT_PX - 1)
        stop = carousel.end_drag()
        self.assertEqual(stop, "home")
        self.assertEqual(carousel.offset_px, BROWSE_OFFSET_HOME)

    def test_filter_to_home_commits_past_threshold(self) -> None:
        # Acceptance criterion 3.
        state = BrowseCarouselState(offset_px=BROWSE_OFFSET_FILTER, stop="filter")
        carousel = BrowseCarousel(state)
        carousel.begin_drag(x=0)
        carousel.update_drag(x=-BROWSE_SNAP_COMMIT_PX)
        stop = carousel.end_drag()
        self.assertEqual(stop, "home")
        self.assertEqual(carousel.offset_px, BROWSE_OFFSET_HOME)

    def test_filter_to_home_reverts_under_threshold(self) -> None:
        state = BrowseCarouselState(offset_px=BROWSE_OFFSET_FILTER, stop="filter")
        carousel = BrowseCarousel(state)
        carousel.begin_drag(x=0)
        carousel.update_drag(x=-(BROWSE_SNAP_COMMIT_PX - 1))
        stop = carousel.end_drag()
        self.assertEqual(stop, "filter")
        self.assertEqual(carousel.offset_px, BROWSE_OFFSET_FILTER)

    def test_wrong_direction_drag_reverts_to_start_stop(self) -> None:
        # Dragging left from Home (toward off-screen Filter overshoot)
        # should not commit anywhere new.
        carousel = BrowseCarousel()
        carousel.begin_drag(x=0)
        carousel.update_drag(x=-100)
        stop = carousel.end_drag()
        self.assertEqual(stop, "home")
        self.assertEqual(carousel.offset_px, BROWSE_OFFSET_HOME)

    def test_end_drag_without_begin_drag_is_noop(self) -> None:
        carousel = BrowseCarousel()
        stop = carousel.end_drag()
        self.assertEqual(stop, "home")


class CancelDragTests(unittest.TestCase):
    def test_cancel_drag_reverts_to_start_stop(self) -> None:
        carousel = BrowseCarousel()
        carousel.begin_drag(x=0)
        carousel.update_drag(x=BROWSE_SNAP_COMMIT_PX)
        carousel.cancel_drag()
        self.assertEqual(carousel.stop, "home")
        self.assertEqual(carousel.offset_px, BROWSE_OFFSET_HOME)
        self.assertFalse(carousel.state.dragging)


if __name__ == "__main__":
    unittest.main()
