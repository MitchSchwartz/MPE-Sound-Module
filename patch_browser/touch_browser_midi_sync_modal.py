"""Looper sync settings modal — quantize grid and output timing toggles."""

from __future__ import annotations

import pygame

from patch_browser.geometry import Rect
from patch_browser.midi_sync_settings import (
    QUANTIZE_OPTIONS,
    apply_offset_auto,
    apply_quantize,
    apply_triplet,
    current_offset_auto,
    current_quantize,
    current_triplet,
    offset_toggle_label,
    quantize_subdivision_label,
)
from patch_browser.scroll_widgets import ContentScrollArea, draw_vertical_scroll_edge_hints
from patch_browser.touch_ui_constants import SETTINGS_ROW_GAP, SETTINGS_ROW_H, TAP_MOVE_THRESHOLD_PX
from patch_browser.touch_ui_enums import Screen


class TouchBrowserMidiSyncModalMixin:
    """Mixin — expects TouchPatchBrowser host attributes."""

    def _open_midi_sync_modal(self) -> None:
        self._midi_sync_option_rects: list[tuple[Rect, str]] = []
        self._midi_sync_offset_toggle_rect = None
        self._midi_sync_triplet_toggle_rect = None
        self._midi_sync_cancel_rect = None
        self._midi_sync_scroll = ContentScrollArea(Rect(0, 0, 1, 1))
        self._midi_sync_scroll.reset()
        self._midi_sync_scroll_vp = None
        self.screen_state = Screen.MIDI_SYNC_MODAL

    def _close_midi_sync_modal(self) -> None:
        self.screen_state = Screen.SETTINGS

    def _select_midi_quantize(self, value: str) -> None:
        if value == current_quantize():
            return
        self._close_midi_sync_modal()
        self._begin_midi_sync_switch(
            f"Setting quantize {quantize_subdivision_label(value)}…",
            lambda v=value: apply_quantize(v),
        )

    def _toggle_midi_offset_auto(self) -> None:
        target = not current_offset_auto()
        self._close_midi_sync_modal()
        self._begin_midi_sync_switch(
            "Updating buffer offset…",
            lambda: apply_offset_auto(target),
        )

    def _toggle_midi_triplet(self) -> None:
        target = not current_triplet()
        self._close_midi_sync_modal()
        self._begin_midi_sync_switch(
            "Updating triplet grid…",
            lambda: apply_triplet(target),
        )

    def _layout_midi_sync_body_rows(self, inner_x: int, list_top: int, inner_w: int) -> int:
        option_h = 48
        toggle_h = SETTINGS_ROW_H
        current_q = current_quantize()

        self._midi_sync_option_rects = []
        y = list_top
        for choice in QUANTIZE_OPTIONS:
            rect = Rect(inner_x, y, inner_w, option_h)
            self._midi_sync_option_rects.append((rect, choice))
            y += option_h + SETTINGS_ROW_GAP

        triplet_rect = Rect(inner_x, y, inner_w, toggle_h)
        self._midi_sync_triplet_toggle_rect = triplet_rect
        y += toggle_h + SETTINGS_ROW_GAP

        offset_rect = Rect(inner_x, y, inner_w, toggle_h)
        self._midi_sync_offset_toggle_rect = offset_rect
        y += toggle_h

        return max(0, y - list_top)

    def _draw_midi_sync_modal(self) -> None:
        self._draw_modal_backdrop(legacy_alpha=150)

        panel_w = min(520, self.width - 48)
        inner_pad = 24
        inner_w = panel_w - inner_pad * 2
        hint_h = self.font_sm.get_height() + 8
        footer_h = SETTINGS_ROW_H + inner_pad
        panel_h = self.height - 24
        panel = Rect((self.width - panel_w) // 2, (self.height - panel_h) // 2, panel_w, panel_h)
        self._draw_modal_shell(panel, border_radius=16)

        inner_x = panel.x + inner_pad
        y = panel.y + inner_pad

        self.screen.blit(self.font_md.render("Looper sync", True, self.theme.text), (inner_x, y))
        y += self.font_md.get_height() + 8
        self.screen.blit(
            self.font_sm.render(
                "Align Roli note-ons to RC-5 grid; advance for Surge buffer latency.",
                True,
                self.theme.muted,
            ),
            (inner_x, y),
        )
        y += hint_h + 8

        list_top = y
        list_h = panel.bottom - footer_h - list_top
        scroll_vp = Rect(inner_x, list_top, inner_w, max(80, list_h))
        self._midi_sync_scroll_vp = scroll_vp
        self._midi_sync_scroll.viewport = scroll_vp
        self._midi_sync_scroll.content_height = self._layout_midi_sync_body_rows(
            inner_x,
            list_top,
            inner_w,
        )

        current_q = current_quantize()
        scroll = int(self._midi_sync_scroll.scroll_pixels)
        clip = self.screen.get_clip()
        self.screen.set_clip(scroll_vp.pygame_rect)

        for rect, choice in self._midi_sync_option_rects:
            screen_rect = Rect(rect.x, rect.y - scroll, rect.w, rect.h)
            if screen_rect.bottom < scroll_vp.y or screen_rect.y > scroll_vp.bottom:
                continue
            self._draw_theme_choice(
                screen_rect,
                quantize_subdivision_label(choice),
                selected=choice == current_q,
                pressed=self._pressed(f"midi_sync:q:{choice}"),
            )

        triplet_rect = self._midi_sync_triplet_toggle_rect
        if triplet_rect is not None:
            screen_triplet = Rect(triplet_rect.x, triplet_rect.y - scroll, triplet_rect.w, triplet_rect.h)
            if not (screen_triplet.bottom < scroll_vp.y or screen_triplet.y > scroll_vp.bottom):
                triplet_on = current_triplet() and current_q != "off"
                self._draw_normalize_toggle(
                    screen_triplet,
                    triplet_on,
                    has_gain=current_q != "off",
                    label="Triplet",
                )

        offset_rect = self._midi_sync_offset_toggle_rect
        if offset_rect is not None:
            screen_offset = Rect(offset_rect.x, offset_rect.y - scroll, offset_rect.w, offset_rect.h)
            if not (screen_offset.bottom < scroll_vp.y or screen_offset.y > scroll_vp.bottom):
                self._draw_normalize_toggle(
                    screen_offset,
                    current_offset_auto(),
                    has_gain=True,
                    label=offset_toggle_label(),
                )

        self.screen.set_clip(clip)

        draw_vertical_scroll_edge_hints(
            self.screen,
            scroll_vp,
            self._midi_sync_scroll,
            self.theme,
        )

        cancel_y = panel.bottom - inner_pad - SETTINGS_ROW_H
        self._midi_sync_cancel_rect = Rect(inner_x, cancel_y, inner_w, SETTINGS_ROW_H)
        pressed = self._pressed("midi_sync:cancel")
        bg = self.theme.accent if pressed else self.theme.surface_alt
        text_color = self.theme.bg if pressed else self.theme.text
        pygame.draw.rect(self.screen, bg, self._midi_sync_cancel_rect.pygame_rect, border_radius=10)
        label = self.font_md.render("Done", True, text_color)
        self.screen.blit(
            label,
            (
                self._midi_sync_cancel_rect.x + (self._midi_sync_cancel_rect.w - label.get_width()) // 2,
                self._midi_sync_cancel_rect.y + (self._midi_sync_cancel_rect.h - label.get_height()) // 2,
            ),
        )

        self._modal_panel_rect = panel

    def _midi_sync_body_hit_at(self, pos: tuple[int, int]) -> str | None:
        scroll = int(getattr(self, "_midi_sync_scroll", ContentScrollArea(Rect(0, 0, 1, 1))).scroll_pixels)
        for rect, choice in getattr(self, "_midi_sync_option_rects", []):
            screen_rect = Rect(rect.x, rect.y - scroll, rect.w, rect.h)
            if screen_rect.contains(*pos):
                return f"q:{choice}"
        triplet_rect = getattr(self, "_midi_sync_triplet_toggle_rect", None)
        if triplet_rect is not None:
            screen_triplet = Rect(triplet_rect.x, triplet_rect.y - scroll, triplet_rect.w, triplet_rect.h)
            if screen_triplet.contains(*pos):
                return "triplet"
        offset_rect = getattr(self, "_midi_sync_offset_toggle_rect", None)
        if offset_rect is not None:
            screen_offset = Rect(offset_rect.x, offset_rect.y - scroll, offset_rect.w, offset_rect.h)
            if screen_offset.contains(*pos):
                return "offset_auto"
        return None

    def _midi_sync_modal_hit_at(self, pos: tuple[int, int]) -> str | None:
        cancel = getattr(self, "_midi_sync_cancel_rect", None)
        if cancel is not None and cancel.contains(*pos):
            return "cancel"
        scroll_vp = getattr(self, "_midi_sync_scroll_vp", None)
        if scroll_vp is not None and scroll_vp.contains(*pos):
            return self._midi_sync_body_hit_at(pos)
        return None

    def _handle_midi_sync_modal_pointer_down(self, pos: tuple[int, int]) -> None:
        self._clear_modal_pointer()
        hit = self._midi_sync_modal_hit_at(pos)
        self._modal_press_hit(pos, hit)
        scroll = getattr(self, "_midi_sync_scroll", None)
        scroll_vp = getattr(self, "_midi_sync_scroll_vp", None)
        if scroll is not None and scroll_vp is not None and scroll_vp.contains(*pos):
            scroll.pointer_down(pos)

    def _handle_midi_sync_modal_pointer_move(self, pos: tuple[int, int]) -> None:
        scroll = getattr(self, "_midi_sync_scroll", None)
        if scroll is not None:
            scroll.pointer_move(pos)
            if scroll.scroll_gesture_active:
                self._touch_press.clear()

    def _handle_midi_sync_modal_pointer_up(self, pos: tuple[int, int]) -> None:
        scroll = getattr(self, "_midi_sync_scroll", None)
        if scroll is not None and scroll.pointer_up(pos):
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
        if hit == "cancel":
            self._close_midi_sync_modal()
            return
        if hit == "offset_auto":
            self._toggle_midi_offset_auto()
            return
        if hit == "triplet":
            if current_quantize() != "off":
                self._toggle_midi_triplet()
            return
        if hit.startswith("q:"):
            self._select_midi_quantize(hit.split(":", 1)[1])
