"""Touch patch browser — instrument chip filter mixin."""

from __future__ import annotations

import pygame

from patch_browser.all_patches_index import first_sort_letter
from patch_browser.draw_primitives import draw_filter_icon
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
    INSTRUMENT_FILTER_BTN_SIZE,
)
from patch_browser.touch_ui_enums import LeftNavMode

ALL_INSTRUMENT_CHIP = "__all__"


class TouchBrowserInstrumentsMixin:
    """Mixin — expects TouchPatchBrowser host attributes."""

    def _init_instrument_filter_state(self) -> None:
        self.instrument_filter: str | None = None
        self.instrument_filter_expanded = False
        self.instrument_filter_btn_rect = Rect(0, 0, 0, 0)
        self.instrument_chip_panel_rect = Rect(0, 0, 0, 0)
        self.instrument_chip_rects: list[tuple[str, Rect]] = []
        self._instrument_chip_panel_height = 0
        self._all_patches_display_flat: list[dict] = []
        self._all_patches_display_letter_index: dict[str, int] = {}

    def _instrument_filter_active(self) -> bool:
        return self.instrument_filter is not None

    def _instrument_chip_offset(self) -> int:
        if not self._show_instrument_chips() or not self.instrument_filter_expanded:
            return 0
        return self._instrument_chip_panel_height + 4

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

    def _toggle_instrument_filter_panel(self) -> None:
        self.instrument_filter_expanded = not self.instrument_filter_expanded
        self._layout()

    def _layout_wrapped_instrument_chips(
        self,
        chip_ids: list[str],
        *,
        panel: Rect,
    ) -> tuple[list[tuple[str, Rect]], int]:
        chip_h = INSTRUMENT_CHIP_ROW_H - 8
        x = panel.x + INSTRUMENT_CHIP_PAD_X
        y = panel.y + 4
        row_start_x = x
        max_x = panel.right - INSTRUMENT_CHIP_PAD_X
        rects: list[tuple[str, Rect]] = []
        for chip_id in chip_ids:
            label = "All" if chip_id == ALL_INSTRUMENT_CHIP else instrument_chip_label(chip_id)
            text_w = self.font_sm.size(label)[0]
            chip_w = text_w + 20
            if x + chip_w > max_x and x > row_start_x:
                x = row_start_x
                y += chip_h + INSTRUMENT_CHIP_GAP
            rects.append((chip_id, Rect(x, y, chip_w, chip_h)))
            x += chip_w + INSTRUMENT_CHIP_GAP
        panel_height = max(INSTRUMENT_CHIP_ROW_H, y + chip_h + 4 - panel.y)
        return rects, panel_height

    def _refresh_instrument_chips(self) -> None:
        if not self._show_instrument_chips():
            self.instrument_chip_rects = []
            self._instrument_chip_panel_height = 0
            self._rebuild_all_patches_display_index()
            return

        context_patches = self._patches_for_chip_context()
        available = instruments_with_patches(context_patches)
        if self.instrument_filter and self.instrument_filter not in available:
            self.instrument_filter = None

        chip_ids: list[str] = [ALL_INSTRUMENT_CHIP] + available
        if self.instrument_filter_expanded:
            self.instrument_chip_rects, self._instrument_chip_panel_height = (
                self._layout_wrapped_instrument_chips(chip_ids, panel=self.instrument_chip_panel_rect)
            )
        else:
            self.instrument_chip_rects = []
            self._instrument_chip_panel_height = 0
        self._rebuild_all_patches_display_index()

    def _layout_instrument_filter_rail(
        self,
        *,
        rail_x: int,
        content_top: int,
    ) -> None:
        if not self._show_instrument_chips():
            self.instrument_filter_btn_rect = Rect(0, 0, 0, 0)
            return
        self.instrument_filter_btn_rect = Rect(
            rail_x,
            content_top,
            INSTRUMENT_FILTER_BTN_SIZE,
            INSTRUMENT_FILTER_BTN_SIZE,
        )

    def _layout_instrument_chip_panel(
        self,
        *,
        margin: int,
        content_top: int,
        nav_header_h: int,
        list_w: int,
    ) -> None:
        if not self._show_instrument_chips():
            self.instrument_chip_panel_rect = Rect(0, 0, 0, 0)
            return
        top = content_top + nav_header_h + 4
        self.instrument_chip_panel_rect = Rect(margin, top, list_w, INSTRUMENT_CHIP_ROW_H)
        self._refresh_instrument_chips()
        if self.instrument_filter_expanded:
            self.instrument_chip_panel_rect.h = self._instrument_chip_panel_height

    def _instrument_filter_hit(self, pos: tuple[int, int]) -> bool:
        return self.instrument_filter_btn_rect.contains(*pos)

    def _chip_pressed(self, chip_id: str) -> bool:
        return self._pressed(f"chip:{chip_id}")

    def _instrument_chip_active(self) -> bool:
        active = self._touch_press.active_id
        return active is not None and active.startswith("chip:")

    def _handle_instrument_filter_pointer_down(self, pos: tuple[int, int]) -> bool:
        if not self._instrument_filter_hit(pos):
            return False
        self._touch_press.set("chip:__filter_btn__")
        return True

    def _handle_instrument_filter_pointer_up(self, pos: tuple[int, int]) -> bool:
        if not self._chip_pressed("__filter_btn__"):
            return False
        self._touch_press.clear()
        if self._instrument_filter_hit(pos):
            self._toggle_instrument_filter_panel()
            return True
        return False

    def _chip_hit_at_pos(self, pos: tuple[int, int]) -> tuple[bool, str | None]:
        if not self.instrument_filter_expanded:
            return False, None
        for chip_id, rect in self.instrument_chip_rects:
            if rect.contains(*pos):
                return True, chip_id
        return False, None

    def _handle_instrument_chip_pointer_down(self, pos: tuple[int, int]) -> bool:
        if self._handle_instrument_filter_pointer_down(pos):
            return True
        if not self.instrument_filter_expanded:
            return False
        if not self.instrument_chip_panel_rect.contains(*pos):
            return False
        hit, chip_id = self._chip_hit_at_pos(pos)
        if hit and chip_id is not None:
            self._touch_press.set(f"chip:{chip_id}")
            return True
        return False

    def _handle_instrument_chip_pointer_move(self, pos: tuple[int, int]) -> bool:
        return self._instrument_chip_active()

    def _try_select_instrument_chip(self, pos: tuple[int, int]) -> bool:
        hit, chip_id = self._chip_hit_at_pos(pos)
        if hit and chip_id is not None:
            self._set_instrument_filter(self._instrument_from_chip_id(chip_id))
            self.instrument_filter_expanded = False
            self._layout()
            return True
        return False

    def _handle_instrument_chip_pointer_up(self, pos: tuple[int, int]) -> bool:
        if self._chip_pressed("__filter_btn__"):
            return self._handle_instrument_filter_pointer_up(pos)
        if not self._instrument_chip_active():
            return False
        self._touch_press.clear()
        return self._try_select_instrument_chip(pos)

    def _draw_instrument_filter_button(self) -> None:
        if not self._show_instrument_chips():
            return
        btn = self.instrument_filter_btn_rect
        if btn.w <= 0:
            return
        active = self._instrument_filter_active()
        expanded = self.instrument_filter_expanded
        btn_pressed = self._chip_pressed("__filter_btn__")
        if active or expanded or btn_pressed:
            btn_bg = self.theme.accent
            icon_color = self.theme.bg
        else:
            btn_bg = self.theme.surface_alt
            icon_color = self.theme.text
        pygame.draw.rect(self.screen, btn_bg, btn.pygame_rect, border_radius=8)
        draw_filter_icon(self.screen, btn, icon_color)

    def _draw_instrument_chip_panel(self) -> None:
        if not self.instrument_filter_expanded or not self.instrument_chip_rects:
            return
        panel = self.instrument_chip_panel_rect
        pygame.draw.rect(self.screen, self.theme.surface, panel.pygame_rect, border_radius=10)
        for chip_id, rect in self.instrument_chip_rects:
            instrument = self._instrument_from_chip_id(chip_id)
            selected = instrument == self.instrument_filter or (
                chip_id == ALL_INSTRUMENT_CHIP and self.instrument_filter is None
            )
            pressed = self._chip_pressed(chip_id)
            if selected or pressed:
                bg = self.theme.accent
                text_color = self.theme.bg
            else:
                bg = self.theme.surface_alt
                text_color = self.theme.text
            pygame.draw.rect(self.screen, bg, rect.pygame_rect, border_radius=14)
            chip_label = "All" if chip_id == ALL_INSTRUMENT_CHIP else instrument_chip_label(chip_id)
            chip_surf = self.font_sm.render(chip_label, True, text_color)
            cx = rect.x + (rect.w - chip_surf.get_width()) // 2
            cy = rect.y + (rect.h - chip_surf.get_height()) // 2
            self.screen.blit(chip_surf, (cx, cy))

    def _draw_instrument_chips(self) -> None:
        self._draw_instrument_chip_panel()
