"""Touch patch browser — browse carousel wiring mixin.

Phase B: state + the layout-facing offset/active helpers. Phase C adds
pointer-down routing (edge pan, filter-pane taps) on top of the same
state. See
Documents/specs/touch-browser-browse-carousel-spec.md §Gesture
architecture.
"""

from __future__ import annotations

from patch_browser.browse_carousel import BrowseCarousel
from patch_browser.touch_ui_enums import LeftNavMode


class TouchBrowserBrowseMixin:
    """Mixin — expects TouchPatchBrowser host attributes."""

    def _init_browse_carousel_state(self) -> None:
        self._browse_carousel = BrowseCarousel()

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
