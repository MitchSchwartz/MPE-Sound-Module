"""Pointer-down zone classification for the browser screen.

Zone is decided once, at pointer-down, for the entire contact — no
mid-gesture reclassification. See
Documents/specs/touch-browser-browse-carousel-spec.md §Gesture architecture.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto

from patch_browser.geometry import Rect


class GestureKind(Enum):
    EDGE_CAROUSEL = auto()
    NAV_SCROLL = auto()
    MIXER = auto()
    FILTER_TAP = auto()
    TAP = auto()


@dataclass
class BrowseGestureZones:
    """Hit-test rects live for the current carousel stop.

    Callers only populate the rects that are actually on-screen for the
    current stop/nav state — e.g. `mixer` at the Home stop, `filter` at
    the Filter stop.
    """

    edge: Rect
    nav: Rect | None = None
    mixer: list[Rect] = field(default_factory=list)
    filter: Rect | None = None


def classify_pointer_down(x: int, y: int, zones: BrowseGestureZones) -> GestureKind:
    """Classify a pointer-down at (x, y) into a `GestureKind`.

    Priority (first match wins): edge_carousel -> mixer -> filter_tap ->
    nav_scroll -> tap.
    """
    if zones.edge.contains(x, y):
        return GestureKind.EDGE_CAROUSEL
    if any(rect.contains(x, y) for rect in zones.mixer):
        return GestureKind.MIXER
    if zones.filter is not None and zones.filter.contains(x, y):
        return GestureKind.FILTER_TAP
    if zones.nav is not None and zones.nav.contains(x, y):
        return GestureKind.NAV_SCROLL
    return GestureKind.TAP
