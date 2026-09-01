"""Surge audio buffer / sample-rate picker modals for touch System settings."""

from __future__ import annotations

from patch_browser.geometry import Rect
from patch_browser.scroll_widgets import ContentScrollArea, draw_vertical_scroll_edge_hints
from patch_browser.surge_audio import (
    JACK_PERIOD_PRESETS,
    SAMPLE_RATE_PRESETS,
    apply_buffer,
    apply_sample_rate,
    buffer_settings_label,
    current_jack_period,
    current_sample_rate,
    graph_buffer_option_label,
    sample_rate_option_label,
    sample_rate_settings_label,
)
from patch_browser.touch_ui_constants import SETTINGS_ROW_GAP, SETTINGS_ROW_H, TAP_MOVE_THRESHOLD_PX
from patch_browser.touch_ui_enums import Screen


class TouchBrowserSurgeAudioModalMixin:
    """Mixin — expects TouchPatchBrowser host attributes."""

    def buffer_settings_row_label(self) -> str:
        return buffer_settings_label()

    def sample_rate_settings_row_label(self) -> str:
        return sample_rate_settings_label()

    def _open_surge_buffer_modal(self) -> None:
        self._surge_buffer_option_rects = []
        self._surge_buffer_cancel_rect = None
        self._surge_buffer_scroll = ContentScrollArea(Rect(0, 0, 1, 1))
        self._surge_buffer_scroll.reset()
        self._surge_buffer_scroll_vp = None
        self.screen_state = Screen.SURGE_BUFFER_MODAL

    def _open_surge_sample_rate_modal(self) -> None:
        self._surge_sample_rate_option_rects = []
        self._surge_sample_rate_cancel_rect = None
        self.screen_state = Screen.SURGE_SAMPLE_RATE_MODAL

    def _close_surge_audio_modal(self) -> None:
        self.screen_state = Screen.SETTINGS

    def _select_surge_buffer(self, buffer: int) -> None:
        if buffer == current_jack_period():
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

    def _layout_surge_buffer_option_rows(self, inner_x: int, list_top: int, inner_w: int) -> int:
        option_h = 44
        self._surge_buffer_option_rects = []
        y = list_top
        for preset in JACK_PERIOD_PRESETS:
            rect = Rect(inner_x, y, inner_w, option_h)
            self._surge_buffer_option_rects.append((rect, preset))
            y += option_h + SETTINGS_ROW_GAP
        return max(0, y - list_top)

    def _draw_surge_buffer_modal(self) -> None:
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

        self.screen.blit(self.font_md.render("Audio buffer", True, self.theme.text), (inner_x, y))
        y += self.font_md.get_height() + 8
        self.screen.blit(
            self.font_sm.render(
                "Lower values reduce latency; heavy patches may crackle.",
                True,
                self.theme.muted,
            ),
            (inner_x, y),
        )
        y += hint_h + 8

        list_top = y
        list_h = panel.bottom - footer_h - list_top
        scroll_vp = Rect(inner_x, list_top, inner_w, max(80, list_h))
        self._surge_buffer_scroll_vp = scroll_vp
        self._surge_buffer_scroll.viewport = scroll_vp
        self._surge_buffer_scroll.content_height = self._layout_surge_buffer_option_rows(
            inner_x,
            list_top,
            inner_w,
        )

        current = current_jack_period()
        scroll = int(self._surge_buffer_scroll.scroll_pixels)
        clip = self.screen.get_clip()
        self.screen.set_clip(scroll_vp.pygame_rect)
        for rect, preset in self._surge_buffer_option_rects:
            screen_rect = Rect(rect.x, rect.y - scroll, rect.w, rect.h)
            if screen_rect.bottom < scroll_vp.y or screen_rect.y > scroll_vp.bottom:
                continue
            self._draw_theme_choice(
                screen_rect,
                graph_buffer_option_label(preset),
                selected=preset == current,
                pressed=self._pressed(f"buffer:{preset}"),
            )
        self.screen.set_clip(clip)

        draw_vertical_scroll_edge_hints(
            self.screen,
            scroll_vp,
            self._surge_buffer_scroll,
            self.theme,
        )

        cancel_y = panel.bottom - inner_pad - SETTINGS_ROW_H
        self._surge_buffer_cancel_rect = Rect(inner_x, cancel_y, inner_w, SETTINGS_ROW_H)
        self._draw_button(self._surge_buffer_cancel_rect, "Cancel", pressed=self._pressed("cancel"))

    def _draw_surge_sample_rate_modal(self) -> None:
        self._draw_modal_backdrop(legacy_alpha=150)

        panel_w = min(420, self.width - 48)
        option_h = 44
        inner_pad = 24
        inner_w = panel_w - inner_pad * 2
        hint_h = self.font_sm.get_height() + 8
        grid_h = len(SAMPLE_RATE_PRESETS) * option_h + max(0, len(SAMPLE_RATE_PRESETS) - 1) * SETTINGS_ROW_GAP
        panel_h = inner_pad + self.font_md.get_height() + 12 + hint_h + grid_h + 16 + SETTINGS_ROW_H + inner_pad
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
            rect = Rect(inner_x, y + index * (option_h + SETTINGS_ROW_GAP), inner_w, option_h)
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
        scroll = int(getattr(self, "_surge_buffer_scroll", ContentScrollArea(Rect(0, 0, 1, 1))).scroll_pixels)
        for rect, preset in getattr(self, "_surge_buffer_option_rects", []):
            screen_rect = Rect(rect.x, rect.y - scroll, rect.w, rect.h)
            if screen_rect.contains(*pos):
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
        scroll = getattr(self, "_surge_buffer_scroll", None)
        scroll_vp = getattr(self, "_surge_buffer_scroll_vp", None)
        if scroll is not None and scroll_vp is not None and scroll_vp.contains(*pos):
            scroll.pointer_down(pos)

    def _handle_surge_buffer_modal_pointer_move(self, pos: tuple[int, int]) -> None:
        scroll = getattr(self, "_surge_buffer_scroll", None)
        if scroll is not None:
            scroll.pointer_move(pos)
            if scroll.scroll_gesture_active:
                self._touch_press.clear()

    def _handle_surge_buffer_modal_pointer_up(self, pos: tuple[int, int]) -> None:
        scroll = getattr(self, "_surge_buffer_scroll", None)
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
