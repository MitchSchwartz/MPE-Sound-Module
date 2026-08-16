"""Audio output profile picker — standalone, USB direct, USB session record."""

from __future__ import annotations

import pygame

from patch_browser.audio_profile import PROFILE_OPTIONS, current_profile, normalize_profile
from patch_browser.geometry import Rect
from patch_browser.touch_ui_constants import SETTINGS_ROW_GAP, SETTINGS_ROW_H, TAP_MOVE_THRESHOLD_PX
from patch_browser.touch_ui_enums import Screen


class TouchBrowserAudioProfileModalMixin:
    """Mixin — expects TouchPatchBrowser host attributes."""

    def _open_audio_profile_modal(self) -> None:
        self._audio_profile_option_rects: list[tuple[Rect, str]] = []
        self._audio_profile_cancel_rect = None
        self.screen_state = Screen.AUDIO_PROFILE_MODAL

    def _close_audio_profile_modal(self) -> None:
        self.screen_state = Screen.SETTINGS

    def _select_audio_profile(self, profile: str) -> None:
        profile = normalize_profile(profile)
        if profile == current_profile():
            self._close_audio_profile_modal()
            return
        self._close_audio_profile_modal()
        self._begin_audio_profile_switch(profile)

    def _draw_audio_profile_choice(
        self,
        rect: Rect,
        title: str,
        subtitle: str,
        *,
        selected: bool,
        pressed: bool = False,
    ) -> None:
        if pressed:
            bg = self.theme.accent
            title_color = self.theme.bg
            sub_color = self.theme.bg
        else:
            bg = self.theme.surface_alt
            title_color = self.theme.accent if selected else self.theme.text
            sub_color = self.theme.muted
        pygame.draw.rect(self.screen, bg, rect.pygame_rect, border_radius=10)
        if selected and not pressed:
            pygame.draw.rect(self.screen, self.theme.accent, rect.pygame_rect, width=2, border_radius=10)
        title_surf = self.font_md.render(title, True, title_color)
        self.screen.blit(title_surf, (rect.x + 14, rect.y + 10))
        sub_surf = self.font_sm.render(subtitle, True, sub_color)
        self.screen.blit(sub_surf, (rect.x + 14, rect.y + 10 + title_surf.get_height() + 2))

    def _draw_audio_profile_modal(self) -> None:
        self._draw_modal_backdrop(legacy_alpha=150)

        panel_w = min(520, self.width - 48)
        option_h = 64
        inner_pad = 24
        inner_w = panel_w - inner_pad * 2
        hint_h = self.font_sm.get_height() + 8
        grid_h = len(PROFILE_OPTIONS) * option_h + max(0, len(PROFILE_OPTIONS) - 1) * SETTINGS_ROW_GAP
        panel_h = (
            inner_pad
            + self.font_md.get_height()
            + 12
            + hint_h
            + grid_h
            + 16
            + SETTINGS_ROW_H
            + inner_pad
        )
        panel = Rect((self.width - panel_w) // 2, (self.height - panel_h) // 2, panel_w, panel_h)
        self._draw_modal_shell(panel, border_radius=16)

        inner_x = panel.x + inner_pad
        y = panel.y + inner_pad

        self.screen.blit(self.font_md.render("Audio output", True, self.theme.text), (inner_x, y))
        y += self.font_md.get_height() + 8
        self.screen.blit(
            self.font_sm.render("Where Surge plays and what the PC records over USB.", True, self.theme.muted),
            (inner_x, y),
        )
        y += hint_h + 4

        current = current_profile()
        self._audio_profile_option_rects = []
        for index, (profile_id, title, subtitle) in enumerate(PROFILE_OPTIONS):
            rect = Rect(inner_x, y + index * (option_h + SETTINGS_ROW_GAP), inner_w, option_h)
            self._audio_profile_option_rects.append((rect, profile_id))
            self._draw_audio_profile_choice(
                rect,
                title,
                subtitle,
                selected=profile_id == current,
                pressed=self._pressed(f"profile:{profile_id}"),
            )

        cancel_y = panel.y + panel.h - inner_pad - SETTINGS_ROW_H
        self._audio_profile_cancel_rect = Rect(inner_x, cancel_y, inner_w, SETTINGS_ROW_H)
        self._draw_button(self._audio_profile_cancel_rect, "Cancel", pressed=self._pressed("cancel"))

    def _audio_profile_modal_hit_at(self, pos: tuple[int, int]) -> str | None:
        cancel = getattr(self, "_audio_profile_cancel_rect", None)
        if cancel is not None and cancel.contains(*pos):
            return "cancel"
        for rect, profile_id in getattr(self, "_audio_profile_option_rects", []):
            if rect.contains(*pos):
                return f"profile:{profile_id}"
        return None

    def _handle_audio_profile_modal_pointer_down(self, pos: tuple[int, int]) -> None:
        self._clear_modal_pointer()
        hit = self._audio_profile_modal_hit_at(pos)
        self._modal_press_hit(pos, hit)

    def _handle_audio_profile_modal_pointer_up(self, pos: tuple[int, int]) -> None:
        if (
            self._modal_pending_key is None
            or self._pointer_move_distance(self._modal_pointer_down_pos, pos) > TAP_MOVE_THRESHOLD_PX
        ):
            self._clear_modal_pointer()
            return
        hit = self._modal_pending_key
        self._clear_modal_pointer()
        if hit == "cancel":
            self._close_audio_profile_modal()
            return
        if hit.startswith("profile:"):
            self._select_audio_profile(hit.split(":", 1)[1])
