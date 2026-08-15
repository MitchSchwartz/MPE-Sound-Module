"""Touch patch browser — browse filter pane (instrument tags) mixin."""

from __future__ import annotations

import pygame

from patch_browser.all_patches_index import first_sort_letter
from patch_browser.browse_filter_pane import ALL_INSTRUMENT_TAG, layout_filter_pane_tags
from patch_browser.geometry import Rect
from patch_browser.instrument_filter import (
    filter_patches_by_instrument,
    instrument_chip_label,
    instrument_counts,
    instruments_with_patches,
    patches_in_browse_subtree,
    primary_instrument,
)
from patch_browser.touch_ui_constants import (
    BROWSE_EDGE_GRAB_W,
    BROWSE_FILTER_HEADER_H,
    BROWSE_FILTER_TAG_GAP,
    BROWSE_FILTER_TAG_MIN_W,
    BROWSE_FILTER_TAG_PAD_X,
    BROWSE_FILTER_TAG_ROW_H,
)
from patch_browser.touch_ui_enums import LeftNavMode

ALL_INSTRUMENT_CHIP = ALL_INSTRUMENT_TAG


class TouchBrowserInstrumentsMixin:
    """Mixin — expects TouchPatchBrowser host attributes."""

    def _init_instrument_filter_state(self) -> None:
        self.instrument_filter: str | None = None
        self.browse_filter_rect = Rect(0, 0, 0, 0)
        self.browse_filter_tag_rects: list[tuple[str, Rect]] = []
        self._browse_filter_packed_tags: list = []
        self._browse_filter_tag_ids_cache: list[str] = []
        self._browse_filter_counts_cache: dict[str, int] = {}
        self._all_patches_display_flat: list[dict] = []
        self._all_patches_display_letter_index: dict[str, int] = {}

    def _instrument_filter_active(self) -> bool:
        return self.instrument_filter is not None

    def _patches_for_chip_context(self) -> list[dict]:
        if self.left_nav_mode == LeftNavMode.ALL_PATCHES:
            return list(self.all_patches_flat)
        if not self.categories:
            return []
        return patches_in_browse_subtree(
            self.scanner,
            self._browse_category_name(),
            self._browse_inner_segments(),
        )

    def _patch_passes_instrument_filter(self, patch: dict) -> bool:
        if not self.instrument_filter:
            return True
        return primary_instrument(patch) == self.instrument_filter

    def _display_all_patches(self) -> list[dict]:
        return filter_patches_by_instrument(self.all_patches_flat, self.instrument_filter)

    def _rebuild_all_patches_display_index(self) -> None:
        patches = self._display_all_patches()
        letter_index: dict[str, int] = {}
        for index, patch in enumerate(patches):
            letter = first_sort_letter(patch["name"])
            if letter not in letter_index:
                letter_index[letter] = index
        self._all_patches_display_flat = patches
        self._all_patches_display_letter_index = letter_index

    def _instrument_from_chip_id(self, chip_id: str) -> str | None:
        if chip_id == ALL_INSTRUMENT_CHIP:
            return None
        return chip_id

    def _set_instrument_filter(self, instrument: str | None) -> None:
        if instrument == self.instrument_filter:
            return
        self.instrument_filter = instrument
        self.nav_list._scroll_pixels = 0.0
        self.nav_list.stop_momentum()
        self._refresh_instrument_chips()
        self._refresh_lists()

    def _layout_browse_filter_pane(self, *, pane: Rect) -> None:
        self.browse_filter_rect = pane
        self._refresh_instrument_chips()

    def _refresh_instrument_chips(self) -> None:
        # `_all_patches_display_flat` depends only on `instrument_filter`
        # and the full patch set — refresh it regardless of whether the
        # filter pane itself is on-screen right now.
        self._rebuild_all_patches_display_index()

        if self.browse_filter_rect.w <= 0:
            self.browse_filter_tag_rects = []
            self._browse_filter_packed_tags = []
            return

        # Mid-drag, only the pane's x is moving — its content (which
        # instruments have patches here, their counts) can't change until
        # the drag ends. Reuse the last full pack instead of re-walking
        # the folder tree and re-aggregating counts on every motion
        # sample; the font-measurement repack below still runs (its cost
        # is bounded by tag count, not patch count).
        if self._browse_carousel.state.dragging and self._browse_filter_tag_ids_cache:
            tag_ids = self._browse_filter_tag_ids_cache
            counts = self._browse_filter_counts_cache
        else:
            context_patches = self._patches_for_chip_context()
            available = instruments_with_patches(context_patches)
            if self.instrument_filter and self.instrument_filter not in available:
                self.instrument_filter = None

            counts = instrument_counts(context_patches)
            counts[ALL_INSTRUMENT_CHIP] = len(context_patches)
            tag_ids = [ALL_INSTRUMENT_CHIP] + available
            self._browse_filter_tag_ids_cache = tag_ids
            self._browse_filter_counts_cache = counts

        def _label(tag_id: str) -> str:
            name = "All" if tag_id == ALL_INSTRUMENT_CHIP else instrument_chip_label(tag_id)
            return f"{name} ({counts.get(tag_id, 0)})"

        # Rigid-body offset from the pane's own x — NOT max(pane.x, ...),
        # which would pin tags to an absolute floor and leave them
        # visible/hit-testable at x=48 even while the pane itself sits
        # off-screen at the Home stop (pane.x well below 0).
        content_x = self.browse_filter_rect.x + BROWSE_EDGE_GRAB_W
        content_rect = Rect(
            content_x,
            self.browse_filter_rect.y + BROWSE_FILTER_HEADER_H,
            max(0, self.browse_filter_rect.right - content_x),
            max(0, self.browse_filter_rect.h - BROWSE_FILTER_HEADER_H),
        )
        packed, _content_h = layout_filter_pane_tags(
            tag_ids,
            label_fn=_label,
            measure_fn=lambda label: self.font_md.size(label)[0],
            content_rect=content_rect,
            row_h=BROWSE_FILTER_TAG_ROW_H,
            pad_x=BROWSE_FILTER_TAG_PAD_X,
            gap=BROWSE_FILTER_TAG_GAP,
            min_chip_w=BROWSE_FILTER_TAG_MIN_W,
        )
        self._browse_filter_packed_tags = packed
        self.browse_filter_tag_rects = [(tag.tag_id, tag.rect) for tag in packed]

    def _browse_filter_header_label(self) -> str:
        count = len(self._patches_for_chip_context())
        noun = "patch" if count == 1 else "patches"
        return f"{count} {noun}"

    def _browse_filter_tag_hit(self, pos: tuple[int, int]) -> str | None:
        for tag_id, rect in self.browse_filter_tag_rects:
            if rect.contains(*pos):
                return tag_id
        return None

    def _draw_browse_filter_pane(self) -> None:
        pane = self.browse_filter_rect
        if pane.w <= 0:
            return
        pygame.draw.rect(self.screen, self.theme.surface, pane.pygame_rect, border_radius=10)
        header_x = pane.x + BROWSE_EDGE_GRAB_W + BROWSE_FILTER_TAG_PAD_X
        header = self.font_sm.render(self._browse_filter_header_label(), True, self.theme.muted)
        self.screen.blit(header, (header_x, pane.y + 8))
        for tag in self._browse_filter_packed_tags:
            selected = tag.tag_id == self.instrument_filter or (
                tag.tag_id == ALL_INSTRUMENT_CHIP and self.instrument_filter is None
            )
            bg = self.theme.accent if selected else self.theme.surface_alt
            text_color = self.theme.bg if selected else self.theme.text
            pygame.draw.rect(self.screen, bg, tag.rect.pygame_rect, border_radius=12)
            surf = self.font_md.render(tag.label, True, text_color)
            cx = tag.rect.x + (tag.rect.w - surf.get_width()) // 2
            cy = tag.rect.y + (tag.rect.h - surf.get_height()) // 2
            self.screen.blit(surf, (cx, cy))
