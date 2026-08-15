"""Touch patch browser — browse carousel wiring mixin.

Phase B: state + the layout-facing offset/active helpers. Phase C adds
pointer-down routing (edge pan, filter-pane taps) on top of the same
state — classified once at pointer-down and consumed by move/up (no
mid-gesture reclassification), wired into both the SDL (mouse) and
evdev browser touch paths. See
Documents/specs/touch-browser-browse-carousel-spec.md §Gesture
architecture.
"""

from __future__ import annotations

from patch_browser.browse_carousel import BrowseCarousel
from patch_browser.geometry import Rect
from patch_browser.gesture_router import BrowseGestureZones, GestureKind, classify_pointer_down
from patch_browser.touch_ui_constants import BROWSE_EDGE_GRAB_W, TAP_MOVE_THRESHOLD_PX
from patch_browser.touch_ui_enums import LeftNavMode


class TouchBrowserBrowseMixin:
    """Mixin — expects TouchPatchBrowser host attributes."""

    def _init_browse_carousel_state(self) -> None:
        self._browse_carousel = BrowseCarousel()
        self._browse_filter_tap_active = False
        self._browse_filter_tap_tag: str | None = None
        self._browse_filter_tap_down_pos: tuple[int, int] | None = None

    def _browse_carousel_active(self) -> bool:
        """Whether the Home/Filter track applies to the current screen.

        Scoped to FOLDERS/PATCHES — ALL_PATCHES has no Patch pane to
        slide against (its nav list fills the width and owns the A–Z
        rail), and a collapsed nav has no track to grab (matches the
        pre-carousel default of hiding the filter UI when collapsed).
        """
        return (
            self.left_nav_mode in (LeftNavMode.FOLDERS, LeftNavMode.PATCHES)
            and not self.left_nav_collapsed
        )

    def _browse_track_offset_px(self) -> float:
        return self._browse_carousel.offset_px

    def _browse_gesture_active(self) -> bool:
        """A browse-owned gesture (edge pan or filter tag press) is live."""
        return self._browse_carousel.state.dragging or self._browse_filter_tap_active

    def _browse_gesture_zones(self) -> BrowseGestureZones:
        edge = Rect(0, self.left_panel_rect.y, BROWSE_EDGE_GRAB_W, self.left_panel_rect.h)
        # Filter tags / pane-body pan are handled in `_handle_browse_pointer_down`
        # so tag hits and swipe-back drags are not conflated in the router.
        return BrowseGestureZones(edge=edge, filter=None)

    def _browse_repaint_after_drag(self) -> None:
        """Layout + draw immediately so the track follows the finger."""
        self._layout()
        self._draw()

    def _handle_browse_pointer_down(self, pos: tuple[int, int]) -> bool:
        """Classify + claim pointer-down for the browse carousel's zones.

        Returns True if claimed — callers should not fall through to
        nav/mixer/tap handling for this contact. Classification happens
        once here; move/up consume the stored state instead of
        re-classifying (spec: no mid-gesture reclassification).
        """
        if not self._browse_carousel_active():
            return False
        kind = classify_pointer_down(pos[0], pos[1], self._browse_gesture_zones())
        if kind == GestureKind.EDGE_CAROUSEL:
            self._browse_carousel.begin_drag(pos[0])
            return True

        if (
            self._browse_carousel.stop == "filter"
            and self.browse_filter_rect.w > 0
            and self.browse_filter_rect.contains(*pos)
        ):
            tag_id = self._browse_filter_tag_hit(pos)
            if tag_id is not None:
                self._browse_filter_tap_tag = tag_id
                self._browse_filter_tap_active = True
                self._browse_filter_tap_down_pos = pos
                return True
            # Swipe-back: drag anywhere on the filter pane body, not just the edge strip.
            self._browse_carousel.begin_drag(pos[0])
            return True

        return False

    def _handle_browse_pointer_move(self, pos: tuple[int, int]) -> bool:
        if self._browse_filter_tap_active and self._browse_filter_tap_down_pos is not None:
            down_x, down_y = self._browse_filter_tap_down_pos
            dx = abs(pos[0] - down_x)
            dy = abs(pos[1] - down_y)
            if dx > TAP_MOVE_THRESHOLD_PX and dx >= dy:
                self._browse_filter_tap_active = False
                self._browse_filter_tap_tag = None
                self._browse_filter_tap_down_pos = None
                self._browse_carousel.begin_drag(down_x)
                self._browse_carousel.update_drag(pos[0])
                self._browse_repaint_after_drag()
            return True
        if self._browse_carousel.state.dragging:
            self._browse_carousel.update_drag(pos[0])
            self._browse_repaint_after_drag()
            return True
        if self._browse_filter_tap_active:
            return True
        return False

    def _handle_browse_pointer_up(self, pos: tuple[int, int]) -> bool:
        if self._browse_carousel.state.dragging:
            self._browse_carousel.end_drag()
            self._browse_repaint_after_drag()
            return True
        if self._browse_filter_tap_active:
            self._browse_filter_tap_active = False
            self._browse_filter_tap_down_pos = None
            tag_id = self._browse_filter_tag_hit(pos)
            pressed_tag = self._browse_filter_tap_tag
            self._browse_filter_tap_tag = None
            if tag_id is not None and tag_id == pressed_tag:
                # Persistence requirement: select the tag, keep the Filter
                # stop — do not change `self._browse_carousel.state.stop`.
                self._set_instrument_filter(self._instrument_from_chip_id(tag_id))
            return True
        return False
