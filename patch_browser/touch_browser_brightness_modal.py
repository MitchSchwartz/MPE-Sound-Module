"""Brightness picker modal — large slider isolated from settings scroll."""

from __future__ import annotations

import pygame

from patch_browser.geometry import Rect
from patch_browser.touch_ui_constants import (
    DEFAULT_BRIGHTNESS_PERCENT,
    SETTINGS_ROW_GAP,
    SETTINGS_ROW_H,
    TAP_MOVE_THRESHOLD_PX,
)
from patch_browser.touch_ui_enums import Screen


BRIGHTNESS_PRESETS = (25, 50, 75, 100)


class TouchBrowserBrightnessModalMixin:
    """Mixin — expects TouchPatchBrowser host attributes."""

    def _open_brightness_modal(self) -> None:
        self._brightness_modal_slider_rect = None
        self._brightness_modal_cancel_rect = None
        self._brightness_modal_preset_rects: list[tuple[Rect, int]] = []
        self._brightness_modal_dragging = False
        self.screen_state = Screen.BRIGHTNESS_MODAL

    def _close_brightness_modal(self) -> None:
        self._brightness_modal_dragging = False
        self.screen_state = Screen.SETTINGS

    def _select_brightness_preset(self, percent: int) -> None:
        self._apply_brightness(percent)
        self._close_brightness_modal()

    def _draw_brightness_modal(self) -> None:
        self._draw_modal_backdrop(legacy_alpha=150)

        panel_w = min(480, self.width - 48)
        option_h = 44
        col_gap = 10
        inner_pad = 24
        cols = 2
        preset_rows = (len(BRIGHTNESS_PRESETS) + 1 + cols - 1) // cols
        slider_block = 56
        hint_h = self.font_sm.get_height() + 8
        grid_h = preset_rows * option_h + max(0, preset_rows - 1) * SETTINGS_ROW_GAP
        panel_h = min(
            inner_pad
            + self.font_md.get_height()
            + 12
            + hint_h
            + slider_block
            + 16
            + grid_h
            + 16
            + SETTINGS_ROW_H
            + inner_pad,
            self.height - 32,
        )
        panel = Rect((self.width - panel_w) // 2, (self.height - panel_h) // 2, panel_w, panel_h)
        self._draw_modal_shell(panel, border_radius=16)

        inner_x = panel.x + inner_pad
        inner_w = panel.w - inner_pad * 2
        col_w = (inner_w - col_gap) // cols
        y = panel.y + inner_pad

        self.screen.blit(self.font_md.render("Brightness", True, self.theme.text), (inner_x, y))
        y += self.font_md.get_height() + 8
        self.screen.blit(
            self.font_sm.render("Drag the slider or pick a preset.", True, self.theme.muted),
            (inner_x, y),
        )
        y += hint_h + 8

        percent = self.brightness_percent
        value_surf = self.font_md.render(f"{percent}%", True, self.theme.text)
        self.screen.blit(value_surf, (inner_x, y))
        y += value_surf.get_height() + 10

        slider_h = 36
        slider_rect = Rect(inner_x, y, inner_w, slider_h)
        self._brightness_modal_slider_rect = slider_rect
        pygame.draw.rect(self.screen, self.theme.surface_alt, slider_rect.pygame_rect, border_radius=10)
        fill_w = max(0, int(inner_w * max(0.0, min(1.0, percent / 100.0))))
        if fill_w > 0:
            fill_rect = pygame.Rect(slider_rect.x, slider_rect.y, fill_w, slider_rect.h)
            pygame.draw.rect(self.screen, self.theme.accent, fill_rect, border_radius=10)
        y += slider_h + 16

        presets = [*BRIGHTNESS_PRESETS, DEFAULT_BRIGHTNESS_PERCENT]
        labels = [f"{p}%" for p in BRIGHTNESS_PRESETS] + ["Reset"]
        self._brightness_modal_preset_rects = []
        for index, (preset, label) in enumerate(zip(presets, labels, strict=True)):
            row = index // cols
            col = index % cols
            rect = Rect(
                inner_x + col * (col_w + col_gap),
                y + row * (option_h + SETTINGS_ROW_GAP),
                col_w,
                option_h,
            )
            self._brightness_modal_preset_rects.append((rect, preset))
            self._draw_theme_choice(rect, label, selected=preset == percent)

        cancel_y = panel.y + panel.h - inner_pad - SETTINGS_ROW_H
        self._brightness_modal_cancel_rect = Rect(inner_x, cancel_y, inner_w, SETTINGS_ROW_H)
        self._draw_button(self._brightness_modal_cancel_rect, "Done")

    def _brightness_modal_hit_at(self, pos: tuple[int, int]) -> str | None:
        cancel = getattr(self, "_brightness_modal_cancel_rect", None)
        if cancel is not None and cancel.contains(*pos):
            return "done"
        slider = getattr(self, "_brightness_modal_slider_rect", None)
        if slider is not None and slider.contains(*pos):
            return "slider"
        for rect, preset in getattr(self, "_brightness_modal_preset_rects", []):
            if rect.contains(*pos):
                return f"preset:{preset}"
        return None

    def _apply_brightness_modal_x(self, x: int) -> None:
        slider = getattr(self, "_brightness_modal_slider_rect", None)
        if slider is None or slider.w <= 0:
            return
        self._apply_brightness(self._brightness_from_x(x, slider))

    def _handle_brightness_modal_pointer_down(self, pos: tuple[int, int]) -> None:
        self._clear_modal_pointer()
        self._modal_pointer_down_pos = pos
        hit = self._brightness_modal_hit_at(pos)
        if hit == "slider":
            self._brightness_modal_dragging = True
            self._apply_brightness_modal_x(pos[0])
        elif hit is not None:
            self._modal_pending_key = hit

    def _handle_brightness_modal_pointer_move(self, pos: tuple[int, int]) -> None:
        if getattr(self, "_brightness_modal_dragging", False):
            self._apply_brightness_modal_x(pos[0])

    def _handle_brightness_modal_pointer_up(self, pos: tuple[int, int]) -> None:
        if getattr(self, "_brightness_modal_dragging", False):
            self._brightness_modal_dragging = False
            self._apply_brightness_modal_x(pos[0])
            self._clear_modal_pointer()
            return
        if (
            self._modal_pending_key is None
            or self._pointer_move_distance(self._modal_pointer_down_pos, pos) > TAP_MOVE_THRESHOLD_PX
        ):
            self._clear_modal_pointer()
            return
        hit = self._modal_pending_key
        self._clear_modal_pointer()
        if hit == "done":
            self._close_brightness_modal()
            return
        if hit.startswith("preset:"):
            self._select_brightness_preset(int(hit.split(":", 1)[1]))
