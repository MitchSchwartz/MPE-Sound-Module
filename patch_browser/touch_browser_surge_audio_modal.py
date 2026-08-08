"""Surge audio buffer / sample-rate picker modals for touch System settings."""

from __future__ import annotations

from patch_browser.geometry import Rect
from patch_browser.surge_audio import (
    BUFFER_PRESETS,
    SAMPLE_RATE_PRESETS,
    apply_buffer,
    apply_sample_rate,
    buffer_option_label,
    current_buffer_size,
    current_sample_rate,
    sample_rate_option_label,
)
from patch_browser.touch_ui_constants import SETTINGS_ROW_GAP, SETTINGS_ROW_H, TAP_MOVE_THRESHOLD_PX
from patch_browser.touch_ui_enums import Screen


class TouchBrowserSurgeAudioModalMixin:
    """Mixin — expects TouchPatchBrowser host attributes."""

    def buffer_settings_row_label(self) -> str:
        buf = current_buffer_size()
        return f"Audio buffer — {buffer_option_label(buf)}"

    def sample_rate_settings_row_label(self) -> str:
        return f"Sample rate — {sample_rate_option_label(current_sample_rate())}"

    def _open_surge_buffer_modal(self) -> None:
        self._surge_buffer_option_rects = []
        self._surge_buffer_cancel_rect = None
        self.screen_state = Screen.SURGE_BUFFER_MODAL

    def _open_surge_sample_rate_modal(self) -> None:
        self._surge_sample_rate_option_rects = []
        self._surge_sample_rate_cancel_rect = None
        self.screen_state = Screen.SURGE_SAMPLE_RATE_MODAL

    def _close_surge_audio_modal(self) -> None:
        self.screen_state = Screen.SETTINGS

    def _select_surge_buffer(self, buffer: int) -> None:
        if buffer == current_buffer_size():
            self._close_surge_audio_modal()
            return
        self._close_surge_audio_modal()
        hint = f"Setting buffer {buffer}…"
        self._begin_surge_audio_switch(hint, lambda b=buffer: apply_buffer(b))

    def _select_surge_sample_rate(self, sample_rate: int) -> None:
        if sample_rate == current_sample_rate():
            self._close_surge_audio_modal()
            return
        self._close_surge_audio_modal()
        hint = f"Setting sample rate {sample_rate // 1000} kHz…"
        self._begin_surge_audio_switch(
            hint,
            lambda r=sample_rate: apply_sample_rate(r),
        )

    def _draw_surge_buffer_modal(self) -> None:
        self._draw_modal_backdrop(legacy_alpha=150)

        panel_w = min(520, self.width - 48)
        option_h = 44
        col_gap = 10
        inner_pad = 24
        cols = 2
        rows = (len(BUFFER_PRESETS) + cols - 1) // cols
        grid_h = rows * option_h + max(0, rows - 1) * SETTINGS_ROW_GAP
        hint_h = self.font_sm.get_height() + 8
        panel_h = min(
            inner_pad + self.font_md.get_height() + 12 + hint_h + grid_h + 16 + SETTINGS_ROW_H + inner_pad,
            self.height - 32,
        )
        panel = Rect((self.width - panel_w) // 2, (self.height - panel_h) // 2, panel_w, panel_h)
        self._draw_modal_shell(panel, border_radius=16)

        inner_x = panel.x + inner_pad
        inner_w = panel.w - inner_pad * 2
        col_w = (inner_w - col_gap) // cols
        y = panel.y + inner_pad

        self.screen.blit(self.font_md.render("Audio buffer", True, self.theme.text), (inner_x, y))
        y += self.font_md.get_height() + 8
        self.screen.blit(
            self.font_sm.render("Lower values reduce latency; heavy patches may crackle.", True, self.theme.muted),
            (inner_x, y),
        )
        y += hint_h + 4

        current = current_buffer_size()
        self._surge_buffer_option_rects = []
        for index, preset in enumerate(BUFFER_PRESETS):
            row = index // cols
            col = index % cols
            rect = Rect(
                inner_x + col * (col_w + col_gap),
                y + row * (option_h + SETTINGS_ROW_GAP),
                col_w,
                option_h,
            )
            self._surge_buffer_option_rects.append((rect, preset))
            self._draw_theme_choice(
                rect,
                buffer_option_label(preset),
                selected=preset == current,
                pressed=self._pressed(f"buffer:{preset}"),
            )

        cancel_y = panel.y + panel.h - inner_pad - SETTINGS_ROW_H
        self._surge_buffer_cancel_rect = Rect(inner_x, cancel_y, inner_w, SETTINGS_ROW_H)
        self._draw_button(self._surge_buffer_cancel_rect, "Cancel", pressed=self._pressed("cancel"))

    def _draw_surge_sample_rate_modal(self) -> None:
        self._draw_modal_backdrop(legacy_alpha=150)

        panel_w = min(420, self.width - 48)
        option_h = 44
        col_gap = 10
        inner_pad = 24
        inner_w = panel_w - inner_pad * 2
        col_w = (inner_w - col_gap) // 2
        hint_h = self.font_sm.get_height() + 8
        panel_h = inner_pad + self.font_md.get_height() + 12 + hint_h + option_h + 16 + SETTINGS_ROW_H + inner_pad
        panel = Rect((self.width - panel_w) // 2, (self.height - panel_h) // 2, panel_w, panel_h)
        self._draw_modal_shell(panel, border_radius=16)

        inner_x = panel.x + inner_pad
        y = panel.y + inner_pad

        self.screen.blit(self.font_md.render("Sample rate", True, self.theme.text), (inner_x, y))
        y += self.font_md.get_height() + 8
        self.screen.blit(
            self.font_sm.render("Must match host capture rate in USB mode.", True, self.theme.muted),
            (inner_x, y),
        )
        y += hint_h + 4

        current = current_sample_rate()
        self._surge_sample_rate_option_rects = []
        for index, preset in enumerate(SAMPLE_RATE_PRESETS):
            rect = Rect(inner_x + index * (col_w + col_gap), y, col_w, option_h)
            self._surge_sample_rate_option_rects.append((rect, preset))
            self._draw_theme_choice(
                rect,
                sample_rate_option_label(preset),
                selected=preset == current,
                pressed=self._pressed(f"rate:{preset}"),
            )

        cancel_y = panel.y + panel.h - inner_pad - SETTINGS_ROW_H
        self._surge_sample_rate_cancel_rect = Rect(inner_x, cancel_y, inner_w, SETTINGS_ROW_H)
        self._draw_button(self._surge_sample_rate_cancel_rect, "Cancel", pressed=self._pressed("cancel"))

    def _surge_buffer_modal_hit_at(self, pos: tuple[int, int]) -> str | None:
        cancel = getattr(self, "_surge_buffer_cancel_rect", None)
        if cancel is not None and cancel.contains(*pos):
            return "cancel"
        for rect, preset in getattr(self, "_surge_buffer_option_rects", []):
            if rect.contains(*pos):
                return f"buffer:{preset}"
        return None

    def _surge_sample_rate_modal_hit_at(self, pos: tuple[int, int]) -> str | None:
        cancel = getattr(self, "_surge_sample_rate_cancel_rect", None)
        if cancel is not None and cancel.contains(*pos):
            return "cancel"
        for rect, preset in getattr(self, "_surge_sample_rate_option_rects", []):
            if rect.contains(*pos):
                return f"rate:{preset}"
        return None

    def _handle_surge_buffer_modal_pointer_down(self, pos: tuple[int, int]) -> None:
        self._clear_modal_pointer()
        hit = self._surge_buffer_modal_hit_at(pos)
        self._modal_press_hit(pos, hit)

    def _handle_surge_buffer_modal_pointer_up(self, pos: tuple[int, int]) -> None:
        if (
            self._modal_pending_key is None
            or self._pointer_move_distance(self._modal_pointer_down_pos, pos) > TAP_MOVE_THRESHOLD_PX
        ):
            self._clear_modal_pointer()
            return
        hit = self._modal_pending_key
        self._clear_modal_pointer()
        if hit == "cancel":
            self._close_surge_audio_modal()
            return
        if hit.startswith("buffer:"):
            self._select_surge_buffer(int(hit.split(":", 1)[1]))

    def _handle_surge_sample_rate_modal_pointer_down(self, pos: tuple[int, int]) -> None:
        self._clear_modal_pointer()
        hit = self._surge_sample_rate_modal_hit_at(pos)
        self._modal_press_hit(pos, hit)

    def _handle_surge_sample_rate_modal_pointer_up(self, pos: tuple[int, int]) -> None:
        if (
            self._modal_pending_key is None
            or self._pointer_move_distance(self._modal_pointer_down_pos, pos) > TAP_MOVE_THRESHOLD_PX
        ):
            self._clear_modal_pointer()
            return
        hit = self._modal_pending_key
        self._clear_modal_pointer()
        if hit == "cancel":
            self._close_surge_audio_modal()
            return
        if hit.startswith("rate:"):
            self._select_surge_sample_rate(int(hit.split(":", 1)[1]))
