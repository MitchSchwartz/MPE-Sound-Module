"""Touch patch browser — browse carousel wiring mixin.

Filter/Home navigation uses explicit header buttons on Pi (instant snap).
Optional edge drag stays behind BROWSE_DRAG_ENABLED for a future
CPU-light tick-based implementation — not full layout+draw per move.
"""

from __future__ import annotations

from typing import Literal

from patch_browser.browse_carousel import BrowseCarousel
from patch_browser.geometry import Rect
from patch_browser.gesture_router import BrowseGestureZones, GestureKind, classify_pointer_down
from patch_browser.touch_ui_constants import (
    BROWSE_DRAG_ENABLED,
    BROWSE_EDGE_GRAB_W,
    TAP_MOVE_THRESHOLD_PX,
)
from patch_browser.touch_ui_enums import LeftNavMode

Stop = Literal["home", "filter"]


class TouchBrowserBrowseMixin:
    """Mixin — expects TouchPatchBrowser host attributes."""

    def _init_browse_carousel_state(self) -> None:
        self._browse_carousel = BrowseCarousel()
        self.browse_filter_open_btn = Rect(0, 0, 0, 0)
        self.browse_filter_back_btn = Rect(0, 0, 0, 0)
        self._browse_filter_tap_active = False
        self._browse_filter_tap_tag: str | None = None
        self._browse_filter_tap_down_pos: tuple[int, int] | None = None

    def _browse_carousel_active(self) -> bool:
        return (
            self.left_nav_mode in (LeftNavMode.FOLDERS, LeftNavMode.PATCHES)
            and not self.left_nav_collapsed
        )

    def _browse_track_offset_px(self) -> float:
        return self._browse_carousel.offset_px

    @property
    def _browse_stop(self) -> Stop:
        return self._browse_carousel.stop

    def _browse_gesture_active(self) -> bool:
        return self._browse_carousel.state.dragging or self._browse_filter_tap_active

    def _browse_gesture_zones(self) -> BrowseGestureZones:
        edge = Rect(0, self.left_panel_rect.y, BROWSE_EDGE_GRAB_W, self.left_panel_rect.h)
        return BrowseGestureZones(edge=edge, filter=None)

    def _set_browse_stop(self, stop: Stop) -> None:
        if not self._browse_carousel_active():
            return
        if self._browse_carousel.state.dragging:
            self._browse_carousel.cancel_drag()
        self._browse_carousel._snap_to(stop)
        self._layout()

    def _open_browse_filter(self) -> None:
        self._set_browse_stop("filter")

    def _close_browse_filter(self) -> None:
        self._set_browse_stop("home")

    def _handle_browse_pointer_down(self, pos: tuple[int, int]) -> bool:
        if not self._browse_carousel_active():
            return False

        if BROWSE_DRAG_ENABLED:
            kind = classify_pointer_down(pos[0], pos[1], self._browse_gesture_zones())
            if kind == GestureKind.EDGE_CAROUSEL:
                self._browse_carousel.begin_drag(pos[0])
                return True
            if (
                self._browse_carousel.stop == "filter"
                and self.browse_filter_rect.w > 0
                and self.browse_filter_rect.contains(*pos)
                and self._browse_filter_tag_hit(pos) is None
            ):
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
        return False

    def _handle_browse_pointer_move(self, pos: tuple[int, int]) -> bool:
        if not BROWSE_DRAG_ENABLED:
            return bool(self._browse_filter_tap_active)

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
                self._layout()
            return True
        if self._browse_carousel.state.dragging:
            self._browse_carousel.update_drag(pos[0])
            self._layout()
            return True
        if self._browse_filter_tap_active:
            return True
        return False

    def _handle_browse_pointer_up(self, pos: tuple[int, int]) -> bool:
        if BROWSE_DRAG_ENABLED and self._browse_carousel.state.dragging:
            self._browse_carousel.end_drag()
            self._layout()
            return True
        if self._browse_filter_tap_active:
            self._browse_filter_tap_active = False
            self._browse_filter_tap_down_pos = None
            tag_id = self._browse_filter_tag_hit(pos)
            pressed_tag = self._browse_filter_tap_tag
            self._browse_filter_tap_tag = None
            if tag_id is not None and tag_id == pressed_tag:
                self._set_instrument_filter(self._instrument_from_chip_id(tag_id))
            return True
        return False
