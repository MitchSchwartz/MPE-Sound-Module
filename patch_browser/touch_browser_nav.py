"""Touch patch browser — centralized left-nav transitions (#24)."""

from __future__ import annotations

from patch_browser.touch_ui_enums import LeftNavMode


def nav_mode_changes_geometry(prev: LeftNavMode, new: LeftNavMode) -> bool:
    """True when nav width / main-pane geometry must be recomputed."""
    all_mode = LeftNavMode.ALL_PATCHES
    return (prev == all_mode) != (new == all_mode)


class TouchBrowserNavMixin:
    """Owns left-nav mode transitions, scroll snapshots, and A–Z rail cleanup."""

    def _clear_az_rail_nav_state(self) -> None:
        self._az_rail_capture = False
        self._az_rail_scrub_letter = None
        self._az_rail_active_letter = None
        self._az_rail_active_until = 0.0

    def _snapshot_all_patches_scroll(self) -> None:
        if self.left_nav_mode == LeftNavMode.ALL_PATCHES:
            self._all_patches_saved_scroll = self.nav_list._scroll_pixels

    def _restore_all_patches_scroll(self) -> None:
        self.nav_list._scroll_pixels = min(
            self._all_patches_saved_scroll,
            self.nav_list._max_scroll_pixels(),
        )
        self.nav_list._sync_scroll_offset()
        self.nav_list.stop_momentum()

    def _enter_nav_mode(
        self,
        mode: LeftNavMode,
        *,
        browse_folder_index: int | None = None,
        browse_inner_segments: tuple[str, ...] | None = None,
        left_nav_collapsed: bool | None = None,
        scroll_to_selection: bool = False,
        reset_list_scroll: bool = False,
        relayout: bool | None = None,
        snapshot_all_scroll: bool = False,
        restore_all_scroll: bool = False,
        rebuild_all_patches: bool = False,
    ) -> None:
        """
        Single entry for left-nav mode changes.

        Sets mode, optional folder index / collapse, clears stale A–Z state,
        runs layout + list refresh, and restores All-patches scroll when asked.
        """
        prev_mode = self.left_nav_mode

        if snapshot_all_scroll and prev_mode == LeftNavMode.ALL_PATCHES:
            self._snapshot_all_patches_scroll()

        if rebuild_all_patches:
            self._rebuild_all_patches_index()

        if browse_folder_index is not None:
            if self.categories:
                self.browse_folder_index = max(
                    0, min(browse_folder_index, len(self.categories) - 1)
                )
            else:
                self.browse_folder_index = 0

        if browse_inner_segments is not None:
            self.browse_inner_segments = tuple(browse_inner_segments)

        if left_nav_collapsed is not None:
            self.left_nav_collapsed = left_nav_collapsed

        self.left_nav_mode = mode

        if mode != LeftNavMode.ALL_PATCHES:
            self._clear_az_rail_nav_state()

        if relayout is None:
            relayout = nav_mode_changes_geometry(prev_mode, mode)

        if relayout:
            self._relayout()
        else:
            self._update_nav_list_geometry()
            self._refresh_lists(scroll_to_selection=scroll_to_selection)

        if restore_all_scroll and mode == LeftNavMode.ALL_PATCHES:
            self._restore_all_patches_scroll()

        if reset_list_scroll:
            self.nav_list._scroll_pixels = 0.0
            self.nav_list.stop_momentum()
            self.nav_list._clamp_scroll()
