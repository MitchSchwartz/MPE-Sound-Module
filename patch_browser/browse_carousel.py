"""Home <-> Filter horizontal browse track carousel.

Two-stop horizontal pan driven by a left-edge swipe. See
Documents/specs/touch-browser-browse-carousel-spec.md §Browse carousel
interaction. This module only tracks offset/stop state; wiring it to
real pointer events is Phase C (`touch_browser_browse.py`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from patch_browser.touch_ui_constants import (
    BROWSE_OFFSET_FILTER,
    BROWSE_OFFSET_HOME,
    BROWSE_SNAP_COMMIT_PX,
)

Stop = Literal["home", "filter"]

_STOP_OFFSET: dict[Stop, float] = {
    "home": BROWSE_OFFSET_HOME,
    "filter": BROWSE_OFFSET_FILTER,
}


@dataclass
class BrowseCarouselState:
    offset_px: float = BROWSE_OFFSET_HOME
    stop: Stop = "home"
    dragging: bool = False
    drag_start_x: float | None = None
    drag_start_offset: float = BROWSE_OFFSET_HOME


class BrowseCarousel:
    """Drives a `BrowseCarouselState` through an edge-pan drag.

    Offset follows the finger 1:1, clamped to `[BROWSE_OFFSET_HOME,
    BROWSE_OFFSET_FILTER]`. On release, commits to the other stop if the
    drag crossed the snap threshold, else reverts to the starting stop.
    """

    def __init__(self, state: BrowseCarouselState | None = None) -> None:
        self.state = state or BrowseCarouselState()

    @property
    def offset_px(self) -> float:
        return self.state.offset_px

    @property
    def stop(self) -> Stop:
        return self.state.stop

    def begin_drag(self, x: float) -> None:
        self.state.dragging = True
        self.state.drag_start_x = x
        self.state.drag_start_offset = self.state.offset_px

    def update_drag(self, x: float) -> None:
        if not self.state.dragging or self.state.drag_start_x is None:
            return
        dx = x - self.state.drag_start_x
        new_offset = self.state.drag_start_offset + dx
        self.state.offset_px = _clamp(new_offset, BROWSE_OFFSET_HOME, BROWSE_OFFSET_FILTER)

    def end_drag(self) -> Stop:
        """Snap to the committed stop, end the drag, and return it."""
        if not self.state.dragging:
            return self.state.stop

        start_stop = self.state.stop
        delta = self.state.offset_px - self.state.drag_start_offset
        travel = BROWSE_OFFSET_FILTER - BROWSE_OFFSET_HOME
        threshold = min(travel * 0.5, BROWSE_SNAP_COMMIT_PX)

        target = start_stop
        if start_stop == "home" and delta >= threshold:
            target = "filter"
        elif start_stop == "filter" and -delta >= threshold:
            target = "home"

        self._snap_to(target)
        self.state.dragging = False
        self.state.drag_start_x = None
        return target

    def cancel_drag(self) -> None:
        """Abort the in-progress drag and snap back to the starting stop."""
        if not self.state.dragging:
            return
        self._snap_to(self.state.stop)
        self.state.dragging = False
        self.state.drag_start_x = None

    def _snap_to(self, stop: Stop) -> None:
        self.state.stop = stop
        self.state.offset_px = _STOP_OFFSET[stop]


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))
