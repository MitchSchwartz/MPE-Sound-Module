"""Touch patch browser — instrument chip filter mixin."""

from __future__ import annotations

import pygame

from patch_browser.all_patches_index import first_sort_letter
from patch_browser.geometry import Rect
from patch_browser.instrument_filter import (
    filter_patches_by_instrument,
    instrument_chip_label,
    instruments_with_patches,
    patches_in_browse_subtree,
    primary_instrument,
)
from patch_browser.touch_ui_constants import (
    INSTRUMENT_CHIP_GAP,
    INSTRUMENT_CHIP_PAD_X,
    INSTRUMENT_CHIP_ROW_H,
)
from patch_browser.touch_ui_enums import LeftNavMode


class TouchBrowserInstrumentsMixin:
    """Mixin — expects TouchPatchBrowser host attributes."""

    def _init_instrument_filter_state(self) -> None:
        self.instrument_filter: str | None = None
        self.instrument_chip_rects: list[tuple[str | None, Rect]] = []
        self.instrument_chip_row_rect = Rect(0, 0, 0, 0)
        self._instrument_chip_content_width = 0
        self._instrument_chip_scroll_x = 0.0
        self._instrument_chip_drag_start_x: int | None = None
        self._instrument_chip_drag_scroll_start = 0.0
        self._all_patches_display_flat: list[dict] = []
        self._all_patches_display_letter_index: dict[str, int] = {}
        self._instrument_chip_pressed: str | None = None

    def _instrument_chip_offset(self) -> int:
        return INSTRUMENT_CHIP_ROW_H + 4 if self._show_instrument_chips() else 0

    def _show_instrument_chips(self) -> bool:
        return (
            not self.left_nav_collapsed
            and self.left_nav_mode in (LeftNavMode.PATCHES, LeftNavMode.ALL_PATCHES)
        )

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

    def _set_instrument_filter(self, instrument: str | None) -> None:
        if instrument == self.instrument_filter:
            return
        self.instrument_filter = instrument
        self.nav_list._scroll_pixels = 0.0
        self.nav_list.stop_momentum()
        self._refresh_instrument_chips()
        self._refresh_lists()

    def _refresh_instrument_chips(self) -> None:
        if not self._show_instrument_chips():
            self.instrument_chip_rects = []
            self._instrument_chip_content_width = 0
            self._rebuild_all_patches_display_index()
            return

        context_patches = self._patches_for_chip_context()
        available = instruments_with_patches(context_patches)
        if self.instrument_filter and self.instrument_filter not in available:
            self.instrument_filter = None

        chip_ids: list[str | None] = [None] + available
        self.instrument_chip_rects = []
        x = self.instrument_chip_row_rect.x + INSTRUMENT_CHIP_PAD_X
        y = self.instrument_chip_row_rect.y + 4
        chip_h = INSTRUMENT_CHIP_ROW_H - 8
        for chip_id in chip_ids:
            label = "All" if chip_id is None else instrument_chip_label(chip_id)
            text_w = self.font_sm.size(label)[0]
            chip_w = text_w + 20
            self.instrument_chip_rects.append(
                (chip_id, Rect(x, y, chip_w, chip_h))
            )
            x += chip_w + INSTRUMENT_CHIP_GAP
        self._instrument_chip_content_width = max(
            0,
            x - self.instrument_chip_row_rect.x - INSTRUMENT_CHIP_PAD_X,
        )
        max_scroll = max(
            0.0,
            float(self._instrument_chip_content_width - self.instrument_chip_row_rect.w),
        )
        self._instrument_chip_scroll_x = max(
            0.0,
            min(self._instrument_chip_scroll_x, max_scroll),
        )
        self._rebuild_all_patches_display_index()

    def _layout_instrument_chip_row(
        self,
        *,
        margin: int,
        content_top: int,
        nav_header_h: int,
        list_w: int,
    ) -> int:
        if not self._show_instrument_chips():
            self.instrument_chip_row_rect = Rect(0, 0, 0, 0)
            return 0
        top = content_top + nav_header_h + 4
        self.instrument_chip_row_rect = Rect(margin, top, list_w, INSTRUMENT_CHIP_ROW_H)
        self._refresh_instrument_chips()
        return INSTRUMENT_CHIP_ROW_H + 4

    def _handle_instrument_chip_pointer_down(self, pos: tuple[int, int]) -> bool:
        if not self.instrument_chip_row_rect.contains(*pos):
            return False
        self._instrument_chip_drag_start_x = pos[0]
        self._instrument_chip_drag_scroll_start = self._instrument_chip_scroll_x
        self._instrument_chip_pressed = self._chip_id_at_pos(pos)
        return True

    def _handle_instrument_chip_pointer_move(self, pos: tuple[int, int]) -> bool:
        if self._instrument_chip_drag_start_x is None:
            return False
        delta = self._instrument_chip_drag_start_x - pos[0]
        max_scroll = max(
            0.0,
            float(self._instrument_chip_content_width - self.instrument_chip_row_rect.w),
        )
        self._instrument_chip_scroll_x = max(
            0.0,
            min(self._instrument_chip_drag_scroll_start + delta, max_scroll),
        )
        return True

    def _chip_id_at_pos(self, pos: tuple[int, int]) -> str | None:
        local_x = pos[0] + self._instrument_chip_scroll_x
        for chip_id, rect in self.instrument_chip_rects:
            if rect.x <= local_x < rect.right:
                return chip_id
        return None

    def _try_select_instrument_chip(self, pos: tuple[int, int]) -> bool:
        if not self.instrument_chip_row_rect.contains(*pos):
            return False
        chip_id = self._chip_id_at_pos(pos)
        if chip_id is not None:
            self._set_instrument_filter(chip_id)
            return True
        return False

    def _handle_instrument_chip_pointer_up(self, pos: tuple[int, int]) -> bool:
        if self._instrument_chip_drag_start_x is None:
            return False
        moved = abs(pos[0] - self._instrument_chip_drag_start_x) > 8
        self._instrument_chip_drag_start_x = None
        self._instrument_chip_pressed = None
        if moved:
            return True
        return self._try_select_instrument_chip(pos)

    def _draw_instrument_chips(self) -> None:
        if not self._show_instrument_chips() or not self.instrument_chip_rects:
            return
        row = self.instrument_chip_row_rect
        clip = self.screen.get_clip()
        self.screen.set_clip(row.pygame_rect)
        scroll = int(self._instrument_chip_scroll_x)
        for chip_id, rect in self.instrument_chip_rects:
            screen_rect = Rect(rect.x - scroll, rect.y, rect.w, rect.h)
            if screen_rect.right < row.x or screen_rect.x > row.right:
                continue
            selected = chip_id == self.instrument_filter or (
                chip_id is None and self.instrument_filter is None
            )
            pressed = chip_id == self._instrument_chip_pressed
            if selected:
                bg = self.theme.accent
                text_color = self.theme.bg
            elif pressed:
                bg = self.theme.surface
                text_color = self.theme.text
            else:
                bg = self.theme.surface_alt
                text_color = self.theme.text
            pygame.draw.rect(
                self.screen,
                bg,
                screen_rect.pygame_rect,
                border_radius=14,
            )
            label = "All" if chip_id is None else instrument_chip_label(chip_id)
            surf = self.font_sm.render(label, True, text_color)
            tx = screen_rect.x + (screen_rect.w - surf.get_width()) // 2
            ty = screen_rect.y + (screen_rect.h - surf.get_height()) // 2
            self.screen.blit(surf, (tx, ty))
        self.screen.set_clip(clip)
