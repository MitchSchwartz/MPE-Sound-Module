"""Audio output picker — which DAC the graph binds.

Spec: Documents/specs/audio-output-selection-spec.md.

Two things here are deliberate and are the whole point of the feature:

* Only devices that are actually CONNECTED are tappable. "Why is a
  not-available device shown?" — Mitch, 2026-09-01. A row you cannot pick is
  not a choice.
* A saved-but-absent device is still DRAWN, dimmed and marked "saved — not
  connected", and is not tappable. Without the row the stored preference is
  invisible and there is no way to tell whether one was ever set; with it, the
  row explains the fall-through the user is currently hearing.
"""

from __future__ import annotations

import pygame

from patch_browser.audio_output import current_selection, menu_rows, normalize_selection
from patch_browser.geometry import Rect
from patch_browser.touch_ui_constants import SETTINGS_ROW_GAP, SETTINGS_ROW_H, TAP_MOVE_THRESHOLD_PX
from patch_browser.touch_ui_enums import Screen


class TouchBrowserAudioOutputModalMixin:
    """Mixin — expects TouchPatchBrowser host attributes."""

    def _open_audio_output_modal(self) -> None:
        self._audio_output_option_rects: list[tuple[Rect, str]] = []
        self._audio_output_cancel_rect = None
        # Enumeration shells out, so snapshot it once per open rather than on
        # every frame: the draw runs at display rate and this forks.
        self._audio_output_rows = menu_rows()
        self.screen_state = Screen.AUDIO_OUTPUT_MODAL

    def _close_audio_output_modal(self) -> None:
        self.screen_state = Screen.SETTINGS

    def _select_audio_output(self, key: str) -> None:
        key = normalize_selection(key)
        selection, _label = current_selection()
        if key == selection:
            self._close_audio_output_modal()
            return
        label = ""
        for row in getattr(self, "_audio_output_rows", ()):
            if row.key == key:
                label = row.title
                break
        self._close_audio_output_modal()
        self._begin_audio_output_switch(key, label)

    def _draw_audio_output_choice(
        self,
        rect: Rect,
        title: str,
        subtitle: str,
        *,
        selected: bool,
        pressed: bool = False,
        enabled: bool = True,
    ) -> None:
        if not enabled:
            # Drawn, never tappable. The muted title is the signal that this is
            # a record of a choice, not an offer.
            pygame.draw.rect(self.screen, self.theme.surface_alt, rect.pygame_rect, border_radius=10)
            title_color = self.theme.muted
            sub_color = self.theme.muted
        elif pressed:
            pygame.draw.rect(self.screen, self.theme.accent, rect.pygame_rect, border_radius=10)
            title_color = self.theme.bg
            sub_color = self.theme.bg
        else:
            pygame.draw.rect(self.screen, self.theme.surface_alt, rect.pygame_rect, border_radius=10)
            title_color = self.theme.accent if selected else self.theme.text
            sub_color = self.theme.muted
        if selected and enabled and not pressed:
            pygame.draw.rect(self.screen, self.theme.accent, rect.pygame_rect, width=2, border_radius=10)
        title_surf = self.font_md.render(title, True, title_color)
        self.screen.blit(title_surf, (rect.x + 14, rect.y + 10))
        sub_surf = self.font_sm.render(subtitle, True, sub_color)
        self.screen.blit(sub_surf, (rect.x + 14, rect.y + 10 + title_surf.get_height() + 2))

    def _draw_audio_output_modal(self) -> None:
        self._draw_modal_backdrop(legacy_alpha=150)

        rows = getattr(self, "_audio_output_rows", None) or menu_rows()
        panel_w = min(520, self.width - 48)
        option_h = 64
        inner_pad = 24
        inner_w = panel_w - inner_pad * 2
        hint_h = self.font_sm.get_height() + 8
        grid_h = len(rows) * option_h + max(0, len(rows) - 1) * SETTINGS_ROW_GAP
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

        self.screen.blit(self.font_md.render("Audio device", True, self.theme.text), (inner_x, y))
        y += self.font_md.get_height() + 8
        self.screen.blit(
            self.font_sm.render("Only connected devices can be chosen.", True, self.theme.muted),
            (inner_x, y),
        )
        y += hint_h + 4

        self._audio_output_option_rects = []
        for index, row in enumerate(rows):
            rect = Rect(inner_x, y + index * (option_h + SETTINGS_ROW_GAP), inner_w, option_h)
            # A disabled row gets NO hit rect at all, so it cannot be selected
            # by a tap that lands on it. Filtering at draw time and again at hit
            # time would be two places to keep in agreement.
            if row.enabled:
                self._audio_output_option_rects.append((rect, row.key))
            self._draw_audio_output_choice(
                rect,
                row.title,
                row.subtitle,
                selected=row.selected,
                pressed=self._pressed(f"output:{row.key}"),
                enabled=row.enabled,
            )

        cancel_y = panel.y + panel.h - inner_pad - SETTINGS_ROW_H
        self._audio_output_cancel_rect = Rect(inner_x, cancel_y, inner_w, SETTINGS_ROW_H)
        self._draw_button(self._audio_output_cancel_rect, "Cancel", pressed=self._pressed("cancel"))

    def _audio_output_modal_hit_at(self, pos: tuple[int, int]) -> str | None:
        cancel = getattr(self, "_audio_output_cancel_rect", None)
        if cancel is not None and cancel.contains(*pos):
            return "cancel"
        for rect, key in getattr(self, "_audio_output_option_rects", []):
            if rect.contains(*pos):
                return f"output:{key}"
        return None

    def _handle_audio_output_modal_pointer_down(self, pos: tuple[int, int]) -> None:
        self._clear_modal_pointer()
        hit = self._audio_output_modal_hit_at(pos)
        self._modal_press_hit(pos, hit)

    def _handle_audio_output_modal_pointer_up(self, pos: tuple[int, int]) -> None:
        if (
            self._modal_pending_key is None
            or self._pointer_move_distance(self._modal_pointer_down_pos, pos) > TAP_MOVE_THRESHOLD_PX
        ):
            self._clear_modal_pointer()
            return
        hit = self._modal_pending_key
        self._clear_modal_pointer()
        if hit == "cancel":
            self._close_audio_output_modal()
            return
        if hit.startswith("output:"):
            # split on the FIRST colon only: the key itself is usb:VID:PID[:SER].
            self._select_audio_output(hit.split(":", 1)[1])
