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
    offset_summary,
    quantize_subdivision_label,
)
from patch_browser.touch_ui_constants import SETTINGS_ROW_GAP, SETTINGS_ROW_H, TAP_MOVE_THRESHOLD_PX
from patch_browser.touch_ui_enums import Screen


class TouchBrowserMidiSyncModalMixin:
    """Mixin — expects TouchPatchBrowser host attributes."""

    def _open_midi_sync_modal(self) -> None:
        self._midi_sync_option_rects: list[tuple[Rect, str]] = []
        self._midi_sync_offset_toggle_rect = None
        self._midi_sync_triplet_toggle_rect = None
        self._midi_sync_cancel_rect = None
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

    def _draw_midi_sync_modal(self) -> None:
        self._draw_modal_backdrop(legacy_alpha=150)

        panel_w = min(520, self.width - 48)
        option_h = 48
        toggle_h = SETTINGS_ROW_H
        inner_pad = 24
        inner_w = panel_w - inner_pad * 2
        hint_h = self.font_sm.get_height() + 8
        grid_h = len(QUANTIZE_OPTIONS) * option_h + max(0, len(QUANTIZE_OPTIONS) - 1) * SETTINGS_ROW_GAP
        panel_h = (
            inner_pad
            + self.font_md.get_height()
            + 12
            + hint_h
            + grid_h
            + SETTINGS_ROW_GAP
            + toggle_h
            + SETTINGS_ROW_GAP
            + toggle_h
            + 16
            + SETTINGS_ROW_H
            + inner_pad
        )
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

        current_q = current_quantize()
        self._midi_sync_option_rects = []
        for choice in QUANTIZE_OPTIONS:
            rect = Rect(inner_x, y, inner_w, option_h)
            self._midi_sync_option_rects.append((rect, choice))
            pressed = self._pressed(f"midi_sync:q:{choice}")
            self._draw_theme_choice(
                rect,
                quantize_subdivision_label(choice),
                selected=choice == current_q,
                pressed=pressed,
            )
            y += option_h + SETTINGS_ROW_GAP

        triplet_rect = Rect(inner_x, y, inner_w, toggle_h)
        self._midi_sync_triplet_toggle_rect = triplet_rect
        triplet_on = current_triplet() and current_q != "off"
        self._draw_normalize_toggle(
            triplet_rect,
            triplet_on,
            has_gain=current_q != "off",
            label="Triplet (3 notes per 2 beats)",
        )
        y += toggle_h + SETTINGS_ROW_GAP

        offset_rect = Rect(inner_x, y, inner_w, toggle_h)
        self._midi_sync_offset_toggle_rect = offset_rect
        self._draw_normalize_toggle(
            offset_rect,
            current_offset_auto(),
            has_gain=True,
            label=f"Auto offset ({offset_summary()})",
        )
        y += toggle_h + 16

        cancel = Rect(inner_x, y, inner_w, SETTINGS_ROW_H)
        self._midi_sync_cancel_rect = cancel
        pressed = self._pressed("midi_sync:cancel")
        bg = self.theme.accent if pressed else self.theme.surface_alt
        text_color = self.theme.bg if pressed else self.theme.text
        pygame.draw.rect(self.screen, bg, cancel.pygame_rect, border_radius=10)
        label = self.font_md.render("Done", True, text_color)
        self.screen.blit(
            label,
            (cancel.x + (cancel.w - label.get_width()) // 2, cancel.y + (cancel.h - label.get_height()) // 2),
        )

        self._modal_panel_rect = panel

    def _midi_sync_modal_hit_at(self, pos: tuple[int, int]) -> str | None:
        if self._midi_sync_cancel_rect and self._midi_sync_cancel_rect.contains(*pos):
            return "cancel"
        for rect, choice in self._midi_sync_option_rects:
            if rect.contains(*pos):
                return f"q:{choice}"
        if self._midi_sync_offset_toggle_rect and self._midi_sync_offset_toggle_rect.contains(*pos):
            return "offset_auto"
        if self._midi_sync_triplet_toggle_rect and self._midi_sync_triplet_toggle_rect.contains(*pos):
            return "triplet"
        return None

    def _handle_midi_sync_modal_pointer_down(self, pos: tuple[int, int]) -> None:
        self._clear_modal_pointer()
        hit = self._midi_sync_modal_hit_at(pos)
        self._modal_press_hit(pos, hit)

    def _handle_midi_sync_modal_pointer_up(self, pos: tuple[int, int]) -> None:
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
