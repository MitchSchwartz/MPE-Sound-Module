"""Touch patch browser — draw mixin."""

from __future__ import annotations

import time

import math
import pygame

from patch_browser.draw_primitives import (
    draw_all_patches_icon,
    draw_chevron,
    draw_current_patch_icon,
    draw_sidebar_panel_icon,
)
from patch_browser.geometry import Rect
from patch_browser.mixer import MixerChannel
from patch_browser.patch_identity import (
    patch_browse_instrument_subtitle,
    patch_browse_subtitle,
    patch_list_subtitle,
)
from patch_browser.dsi_splash import shutdown_animation_phase
from patch_browser.touch_ui_constants import (
    CPU_METER_BAR_W,
    CPU_METER_LABEL_GAP,
    DETAIL_TITLE_PAD_X,
    FADER_HANDLE_H,
    FADER_HANDLE_W,
    LONG_PRESS_S,
    LOOPER_HUD_PAD_X,
    SETTINGS_PANEL_FOOTER_H,
    SETTINGS_PANEL_HEADER_H,
    SETTINGS_ROW_GAP,
    SETTINGS_ROW_H,
)
from patch_browser.audio_profile import header_badge_label
from patch_browser.audio_engine import engine_hud_label, engine_hud_semantic, engine_hud_should_show
from patch_browser.midi_clock import looper_hud_label, looper_hud_should_show
from patch_browser.touch_ui_enums import (
    CalibrateMode,
    LeftNavMode,
)
from patch_browser.ui_text import (
    blit_text_block,
    draw_wrapped_text_in_rect,
    ellipsize_text,
    text_block_height,
    wrap_text_lines,
)
from patch_browser.scroll_widgets import draw_vertical_scroll_edge_hints
from patch_browser.ui_theme import (
    ACCENT_PRESETS,
    ACCENT_STYLE_MINIMAL,
    ACCENT_STYLE_MONOCHROME,
    THEME_MODE_OLED_BLACK,
    THEME_MODE_STANDARD,
    THEME_VIEW_COLORS,
    THEME_VIEW_MAIN,
    THEME_VIEW_PICKER,
    rgb_to_hex,
    theme_semantic_color,
)


class TouchBrowserDrawMixin:
    """Mixin — expects TouchPatchBrowser host attributes."""

    def _semantic_color(self, kind: str) -> tuple[int, int, int]:
        return theme_semantic_color(self.theme, kind)

    def _draw_heart_icon(self, rect: Rect, filled: bool) -> None:
        pygame.draw.rect(self.screen, self.theme.surface_alt, rect.pygame_rect, border_radius=8)
        symbol = "♥" if filled else "♡"
        color = self.theme.accent if filled else self.theme.muted
        text = self.font_lg.render(symbol, True, color)
        tx = rect.x + (rect.w - text.get_width()) // 2
        ty = rect.y + (rect.h - text.get_height()) // 2
        self.screen.blit(text, (tx, ty))
    def _draw_icon_button(
        self,
        rect: Rect,
        icon: str,
        *,
        accent: bool = False,
        muted: bool = False,
        pressed: bool = False,
    ) -> None:
        if pressed:
            color = self.theme.accent
            icon_color = self.theme.bg
        elif muted:
            color = self.theme.surface
            icon_color = self.theme.text
        elif accent:
            color = self.theme.accent
            icon_color = self.theme.bg
        else:
            color = self.theme.surface_alt
            icon_color = self.theme.text
        pygame.draw.rect(self.screen, color, rect.pygame_rect, border_radius=8)
        if icon == "back":
            draw_chevron(self.screen, rect, icon_color, direction="left")
        elif icon == "panel_close":
            draw_sidebar_panel_icon(self.screen, rect, icon_color, panel_open=True)
        elif icon == "panel_open":
            draw_sidebar_panel_icon(self.screen, rect, icon_color, panel_open=False)
    def _draw_modal_backdrop(self, legacy_alpha: int = 150) -> None:
        alpha = self.theme.backdrop_alpha if self.theme.backdrop_alpha is not None else legacy_alpha
        overlay = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, alpha))
        self.screen.blit(overlay, (0, 0))
    def _settings_overlay_alpha(self) -> int:
        if self.theme.backdrop_alpha is not None:
            slide = self._settings_slide
            return int(self.theme.backdrop_alpha * (0.35 + 0.65 * slide))
        return int(140 + 60 * self._settings_slide)
    def _draw_hairline(self, y: int, x_left: int, x_right: int) -> None:
        if self.theme.hairline_alpha <= 0 or x_right <= x_left:
            return
        line = pygame.Surface((x_right - x_left, 1), pygame.SRCALPHA)
        r, g, b = self.theme.accent
        line.fill((r, g, b, self.theme.hairline_alpha))
        self.screen.blit(line, (x_left, y))
    def _draw_divider_line(self, x_left: int, y: int, x_right: int) -> None:
        if self.theme.hairline_alpha > 0:
            self._draw_hairline(y, x_left, x_right)
        else:
            pygame.draw.line(
                self.screen,
                self.theme.surface_alt,
                (x_left, y),
                (x_right, y),
                1,
            )
    def _draw_elevated_panel(self, rect: Rect, *, border_radius: int = 16) -> None:
        color = self.theme.panel_surface()
        pygame.draw.rect(self.screen, color, rect.pygame_rect, border_radius=border_radius)
        if self.theme.elevated_top_highlight:
            inset = min(border_radius, max(0, rect.w // 4))
            highlight = tuple(min(255, channel + 14) for channel in color)
            pygame.draw.line(
                self.screen,
                highlight,
                (rect.x + inset, rect.y),
                (rect.right - inset, rect.y),
                1,
            )

    def _draw_modal_shell(self, panel: Rect, *, border_radius: int = 16) -> None:
        """Draw a centered modal panel and track its rect for backdrop dismiss."""
        self._modal_panel_rect = panel
        self._draw_elevated_panel(panel, border_radius=border_radius)
    def _nav_icon_colors(
        self,
        *,
        selected: bool,
        disabled: bool,
    ) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
        if disabled:
            return self.theme.surface, self.theme.muted
        if selected:
            return self.theme.surface_alt, self.theme.accent
        return self.theme.surface_alt, self.theme.text

    def _draw_nav_icon_button(
        self,
        rect: Rect,
        *,
        selected: bool,
        disabled: bool = False,
        draw_icon,
    ) -> None:
        bg, icon_color = self._nav_icon_colors(selected=selected, disabled=disabled)
        pygame.draw.rect(self.screen, bg, rect.pygame_rect, border_radius=8)
        draw_icon(self.screen, rect, icon_color)

    def _draw_nav_current_button(self, rect: Rect, *, selected: bool, disabled: bool) -> None:
        self._draw_nav_icon_button(
            rect,
            selected=selected,
            disabled=disabled,
            draw_icon=draw_current_patch_icon,
        )

    def _draw_nav_all_button(self, rect: Rect, *, selected: bool) -> None:
        def _draw(surface, icon_rect, icon_color):
            draw_all_patches_icon(surface, icon_rect, icon_color, self.font_sm)

        self._draw_nav_icon_button(rect, selected=selected, draw_icon=_draw)

    def _draw_nav_header(self) -> None:
        pygame.draw.rect(self.screen, self.theme.surface, self.nav_header_rect.pygame_rect)

        if self.left_nav_mode in (LeftNavMode.PATCHES, LeftNavMode.ALL_PATCHES):
            self._draw_icon_button(self.nav_back_btn, "back", muted=True)
        all_selected = self.left_nav_mode == LeftNavMode.ALL_PATCHES
        current_selected = (
            self.left_nav_mode == LeftNavMode.PATCHES
            and self.loaded_patch_info is not None
            and self.browse_folder_index == self.loaded_folder_index
            and self._browse_inner_segments() == self.loaded_inner_segments
        )
        current_disabled = self.loaded_patch_info is None
        self._draw_nav_current_button(
            self.nav_current_btn,
            selected=current_selected,
            disabled=current_disabled,
        )
        self._draw_nav_all_button(self.nav_all_btn, selected=all_selected)
        self._draw_instrument_filter_button()
        if self.left_nav_mode != LeftNavMode.ALL_PATCHES:
            self._draw_icon_button(self.nav_collapse_btn, "panel_close", muted=True)
    def _draw_folder_title_bar(self) -> None:
        if self.nav_folder_title_rect is None:
            return
        rect = self.nav_folder_title_rect
        pygame.draw.rect(self.screen, self.theme.surface_alt, rect.pygame_rect, border_radius=8)
        pygame.draw.line(
            self.screen,
            self.theme.muted,
            (rect.x + 10, rect.bottom - 1),
            (rect.right - 10, rect.bottom - 1),
            1,
        )
        folder_name = self._browse_folder_title()
        if self.left_nav_mode == LeftNavMode.ALL_PATCHES:
            count = len(self._all_patches_display_flat)
            folder_name = f"All patches ({count})"
        max_w = max(1, rect.w - 24)
        clipped = ellipsize_text(self.font_sm, folder_name, max_w)
        label = self.font_sm.render(clipped, True, self.theme.text)
        self.screen.blit(label, (rect.x + 12, rect.y + (rect.h - label.get_height()) // 2))

    def _row_touch_feedback(self, index: int) -> tuple[bool, float]:
        """Pressed highlight + long-press progress (0–1) for nav list row index."""
        pending = getattr(self, "_long_press_pending", None)
        if pending and pending.get("index") == index and not getattr(
            self.nav_list, "_pointer_scrolled", False
        ):
            elapsed = time.time() - pending["started"]
            return True, min(1.0, elapsed / LONG_PRESS_S)
        if (
            self.nav_list.pressed_index == index
            and not getattr(self.nav_list, "_pointer_scrolled", False)
        ):
            return True, 0.0
        return False, 0.0

    def _draw_row_touch_chrome(self, row_rect: pygame.Rect, index: int) -> None:
        pressed, progress = self._row_touch_feedback(index)
        if not pressed:
            return
        pygame.draw.rect(self.screen, self.theme.surface_alt, row_rect, border_radius=8)
        if progress > 0:
            bar_h = 3
            bar_w = max(4, int(row_rect.w * progress))
            pygame.draw.rect(
                self.screen,
                self.theme.accent,
                (row_rect.x, row_rect.bottom - bar_h - 2, bar_w, bar_h),
                border_radius=2,
            )

    def _draw_button(
        self,
        rect: Rect,
        label: str,
        accent: bool = False,
        small: bool = False,
        muted: bool = False,
        danger: bool = False,
        pressed: bool = False,
    ) -> None:
        """Modal/action button. accent=primary commit; danger=destructive; default=dismiss/secondary."""
        if pressed:
            bg = self.theme.accent
            text_color = self.theme.bg
        elif muted:
            bg = self.theme.surface
            text_color = self.theme.muted
        elif danger:
            bg = self.theme.danger
            text_color = self.theme.bg
        elif accent:
            bg = self.theme.accent
            text_color = self.theme.bg
        else:
            bg = self.theme.surface_alt
            text_color = self.theme.text
        pygame.draw.rect(self.screen, bg, rect.pygame_rect, border_radius=8)
        font = self.font_sm if small else self.font_md
        max_w = max(1, rect.w - 16)
        lines = wrap_text_lines(font, label, max_w, max_lines=2)
        block_h = text_block_height(font, len(lines), line_spacing=2)
        start_y = rect.y + max(0, (rect.h - block_h) // 2)
        for i, line in enumerate(lines):
            surf = font.render(line, True, text_color)
            tx = rect.x + (rect.w - surf.get_width()) // 2
            ty = start_y + i * (font.get_linesize() + 2)
            self.screen.blit(surf, (tx, ty))
    def _draw_vertical_fader(self, channel: MixerChannel) -> None:
        value = self._mixer_value(channel)
        handle_y = self._value_to_handle_y(channel, value)
        handle_x = channel.column_rect.x + (channel.column_rect.w - FADER_HANDLE_W) // 2

        track_color = self.theme.surface_alt if channel.enabled else self.theme.surface
        pygame.draw.rect(self.screen, track_color, channel.track_rect.pygame_rect, border_radius=5)

        # Tick marks for mixing-board feel
        for tick in range(5):
            ty = channel.track_rect.y + int(channel.track_rect.h * tick / 4)
            pygame.draw.line(
                self.screen,
                self.theme.muted,
                (channel.track_rect.right + 3, ty),
                (channel.track_rect.right + 8, ty),
                1,
            )

        handle_color = self.theme.accent if channel.enabled else self.theme.muted
        handle_rect = pygame.Rect(handle_x, handle_y, FADER_HANDLE_W, FADER_HANDLE_H)
        pygame.draw.rect(self.screen, handle_color, handle_rect, border_radius=6)
        pygame.draw.rect(self.screen, self.theme.text, handle_rect, width=1, border_radius=6)
        pygame.draw.line(
            self.screen,
            self.theme.bg,
            (handle_rect.x + 8, handle_rect.centery),
            (handle_rect.right - 8, handle_rect.centery),
            2,
        )

        control = self._mixer_control(channel.channel_id)
        if channel.enabled and control is not None:
            value_label = control.format(value)
        else:
            value_label = "—"
        val_s = self.font_sm.render(value_label, True, self.theme.muted if channel.enabled else self.theme.muted)
        val_x = channel.column_rect.x + (channel.column_rect.w - val_s.get_width()) // 2
        self.screen.blit(val_s, (val_x, channel.track_rect.bottom + 6))

        label_s = self.font_sm.render(channel.label, True, self.theme.text if channel.enabled else self.theme.muted)
        label_x = channel.column_rect.x + (channel.column_rect.w - label_s.get_width()) // 2
        self.screen.blit(label_s, (label_x, channel.track_rect.bottom + 24))
    def _draw_mixer_strip(self) -> None:
        for channel in self.mixer_channels:
            self._draw_vertical_fader(channel)
    def _draw_slider(self, rect: Rect, ratio: float, label: str) -> None:
        pygame.draw.rect(self.screen, self.theme.surface_alt, rect.pygame_rect, border_radius=10)
        fill_w = int(rect.w * max(0.0, min(1.0, ratio)))
        if fill_w > 0:
            fill_rect = pygame.Rect(rect.x, rect.y, fill_w, rect.h)
            pygame.draw.rect(self.screen, self.theme.accent, fill_rect, border_radius=10)
        text = self.font_sm.render(label, True, self.theme.muted)
        self.screen.blit(text, (rect.x, rect.y - 22))
    def _cpu_meter_color(self, percent: float) -> tuple[int, int, int]:
        if percent < 40.0:
            return self.theme.ok
        if percent < 75.0:
            return self.theme.playing
        return self.theme.danger
    def _draw_cpu_meter(self, rect: Rect) -> None:
        snap = self.cpu_monitor.snapshot()
        label = self.font_sm.render("CPU", True, self.theme.muted)
        label_x = rect.x
        label_y = rect.y + (rect.h - label.get_height()) // 2
        self.screen.blit(label, (label_x, label_y))

        bar_h = label.get_height()
        bar_x = rect.x + label.get_width() + CPU_METER_LABEL_GAP
        bar_y = label_y
        bar_rect = pygame.Rect(bar_x, bar_y, CPU_METER_BAR_W, bar_h)
        pygame.draw.rect(self.screen, self.theme.surface_alt, bar_rect, border_radius=3)

        if not snap["online"] or snap["percent"] is None:
            dash = self.font_sm.render("—", True, self.theme.muted)
            dash_x = bar_rect.x + (bar_rect.w - dash.get_width()) // 2
            dash_y = bar_rect.y + (bar_rect.h - dash.get_height()) // 2
            self.screen.blit(dash, (dash_x, dash_y))
            return

        percent = max(0.0, min(100.0, float(snap["percent"])))
        fill_h = max(1, int(bar_rect.h * (percent / 100.0)))
        fill_rect = pygame.Rect(bar_rect.x, bar_rect.bottom - fill_h, bar_rect.w, fill_h)
        pygame.draw.rect(
            self.screen,
            self._cpu_meter_color(percent),
            fill_rect,
            border_radius=3,
        )

    def _draw_engine_hud(self, rect: Rect) -> None:
        if rect.w <= 0:
            return
        state = self.engine_monitor.snapshot()
        if not engine_hud_should_show(state):
            return
        label = engine_hud_label(state)
        if not label:
            return
        semantic = engine_hud_semantic(state)
        fill = self.theme.surface_alt
        text_color = self._semantic_color(semantic)
        pygame.draw.rect(self.screen, fill, rect.pygame_rect, border_radius=8)
        badge = self.font_sm.render(label, True, text_color)
        self.screen.blit(
            badge,
            (
                rect.x + (rect.w - badge.get_width()) // 2,
                rect.y + (rect.h - badge.get_height()) // 2,
            ),
        )

    def _draw_looper_hud(self, rect: Rect) -> None:
        if rect.w <= 0:
            return
        snap = self.looper_monitor.snapshot()
        if not looper_hud_should_show(snap, user_enabled=getattr(self, "show_looper_hud", True)):
            return
        label = looper_hud_label(snap)
        sl = snap.get("sl") or {}
        running = bool(snap.get("running")) or bool(sl.get("active"))

        fill = self.theme.surface_alt
        if running:
            text_color = self.theme.accent
        else:
            text_color = self.theme.text

        pygame.draw.rect(self.screen, fill, rect.pygame_rect, border_radius=8)

        dot_x = rect.x + LOOPER_HUD_PAD_X
        text_x = dot_x
        if running:
            dot_y = rect.y + rect.h // 2
            pygame.draw.circle(self.screen, self.theme.accent, (dot_x + 3, dot_y), 4)
            text_x = dot_x + 12

        if label:
            badge = self.font_sm.render(label, True, text_color)
            self.screen.blit(
                badge,
                (
                    text_x + max(0, (rect.w - (text_x - rect.x) - badge.get_width()) // 2),
                    rect.y + (rect.h - badge.get_height()) // 2,
                ),
            )

    def _draw_audio_profile_badge(self, rect: Rect) -> None:
        label = header_badge_label()
        from patch_browser.usb_audio_recovery import is_recovering

        recovering = label == "Sync"
        usb = label == "USB" or recovering
        fill = self.theme.surface_alt
        if recovering:
            text_color = self.theme.accent
        else:
            text_color = self.theme.accent if usb else self.theme.muted
        pygame.draw.rect(self.screen, fill, rect.pygame_rect, border_radius=8)
        badge = self.font_sm.render(label, True, text_color)
        self.screen.blit(
            badge,
            (
                rect.x + (rect.w - badge.get_width()) // 2,
                rect.y + (rect.h - badge.get_height()) // 2,
            ),
        )

    def _draw_status_bar(self) -> None:
        pygame.draw.rect(self.screen, self.theme.surface, self.status_rect.pygame_rect, border_radius=10)
        if self.loaded_patch_info:
            title = self.loaded_patch_info["name"]
            subtitle = self.loaded_patch_info["category"]
        else:
            title = "No patch loaded"
            subtitle = "Select a patch from the left list"

        from patch_browser.usb_audio_recovery import status_subtitle

        recovery_hint = status_subtitle()
        if recovery_hint:
            subtitle = recovery_hint

        title_x = getattr(self, "status_title_x", self.status_rect.x + 12)
        widget_left = getattr(self, "engine_hud_rect", getattr(self, "looper_hud_rect", self.audio_profile_badge_rect)).x
        if widget_left <= title_x or getattr(self, "engine_hud_rect", Rect(0, 0, 0, 0)).w <= 0:
            widget_left = getattr(self, "looper_hud_rect", self.audio_profile_badge_rect).x
        if widget_left <= title_x:
            widget_left = self.audio_profile_badge_rect.x
        title_max_w = max(1, widget_left - title_x - 12)
        title_lines = wrap_text_lines(self.font_md, title, title_max_w, max_lines=1)
        self.screen.blit(
            self.font_md.render(title_lines[0], True, self.theme.text),
            (title_x, self.status_rect.y + 6),
        )
        sub_lines = wrap_text_lines(self.font_sm, subtitle, title_max_w, max_lines=1)
        sub_color = self.theme.accent if recovery_hint else self.theme.muted
        self.screen.blit(
            self.font_sm.render(sub_lines[0], True, sub_color),
            (title_x, self.status_rect.y + 26),
        )
        self._draw_audio_profile_badge(self.audio_profile_badge_rect)
        self._draw_engine_hud(getattr(self, "engine_hud_rect", Rect(0, 0, 0, 0)))
        if getattr(self, "show_looper_hud", True):
            self._draw_looper_hud(self.looper_hud_rect)
        if self.show_cpu_meter:
            self._draw_cpu_meter(self.cpu_meter_rect)
        self._draw_button(
            self.system_settings_btn,
            "⋯",
            small=True,
            muted=True,
            pressed=self._pressed("header:settings"),
        )
        self._draw_hairline(
            self.status_rect.bottom - 1,
            self.status_rect.x + 12,
            self.status_rect.right - 12,
        )
    def _draw_left_nav_collapsed(self) -> None:
        pygame.draw.rect(self.screen, self.theme.surface, self.left_panel_rect.pygame_rect, border_radius=10)
        expand_rect = Rect(
            self.left_panel_rect.x + 4,
            self.left_panel_rect.y + 8,
            self.left_panel_rect.w - 8,
            32,
        )
        self._draw_icon_button(expand_rect, "panel_open", muted=True)
    def _draw_all_patches_list(self) -> None:
        pygame.draw.rect(self.screen, self.theme.surface, self.nav_list.rect.pygame_rect, border_radius=10)
        clip = self.screen.get_clip()
        self.screen.set_clip(self.nav_list.rect.pygame_rect)

        patches = self._all_patches_display_flat
        if not patches:
            self.screen.set_clip(clip)
            return

        row_h = self.nav_list.row_height
        start = int(self.nav_list._scroll_pixels // row_h)
        sub_pixel = self.nav_list._scroll_pixels - start * row_h
        end = min(len(patches), start + self.nav_list.visible_count() + 3)
        y = self.nav_list.rect.y + self.nav_list.padding - int(sub_pixel)

        for index in range(start, end):
            patch = patches[index]
            row_rect = pygame.Rect(
                self.nav_list.rect.x + 4,
                y,
                self.nav_list.rect.w - 8,
                row_h - 2,
            )
            is_loaded = self.nav_list.loaded_marker_index == index
            self._draw_row_touch_chrome(row_rect, index)
            if is_loaded:
                pygame.draw.rect(self.screen, self.theme.surface_alt, row_rect, border_radius=8)

            favorited = self._patch_is_favorited(patch)
            heart = "♥" if favorited else "♡"
            heart_color = self.theme.accent if favorited else self.theme.muted
            heart_s = self.font_sm.render(heart, True, heart_color)
            self.screen.blit(heart_s, (row_rect.x + 6, row_rect.y + 8))

            name_max_w = max(1, row_rect.w - 34)
            name_clipped = ellipsize_text(self.font_md, patch["name"], name_max_w)
            name_color = self.theme.text if is_loaded else self.theme.muted
            name_s = self.font_md.render(name_clipped, True, name_color)
            self.screen.blit(name_s, (row_rect.x + 26, row_rect.y + 6))

            folder_clipped = ellipsize_text(
                self.font_sm,
                patch_list_subtitle(patch),
                name_max_w,
            )
            folder_s = self.font_sm.render(folder_clipped, True, self.theme.muted)
            self.screen.blit(folder_s, (row_rect.x + 26, row_rect.y + 26))

            if is_loaded:
                pygame.draw.circle(
                    self.screen,
                    self.theme.accent,
                    (row_rect.right - 12, row_rect.centery),
                    4,
                )

            y += row_h

        self.screen.set_clip(clip)

    def _draw_browse_patches_list(self) -> None:
        pygame.draw.rect(self.screen, self.theme.surface, self.nav_list.rect.pygame_rect, border_radius=10)
        clip = self.screen.get_clip()
        self.screen.set_clip(self.nav_list.rect.pygame_rect)

        entries = self._browse_nav_entries
        if not entries:
            self.screen.set_clip(clip)
            return

        row_h = self.nav_list.row_height
        start = int(self.nav_list._scroll_pixels // row_h)
        sub_pixel = self.nav_list._scroll_pixels - start * row_h
        end = min(len(entries), start + self.nav_list.visible_count() + 3)
        y = self.nav_list.rect.y + self.nav_list.padding - int(sub_pixel)

        for index in range(start, end):
            entry = entries[index]
            row_rect = pygame.Rect(
                self.nav_list.rect.x + 4,
                y,
                self.nav_list.rect.w - 8,
                row_h - 2,
            )
            is_highlight = self.nav_list.highlight_index == index
            is_loaded = self.nav_list.loaded_marker_index == index
            self._draw_row_touch_chrome(row_rect, index)
            if is_highlight or is_loaded:
                pygame.draw.rect(self.screen, self.theme.surface_alt, row_rect, border_radius=8)

            text_color = self.theme.text if is_highlight or is_loaded else self.theme.muted
            name_max_w = max(1, row_rect.w - 28)
            if entry["kind"] == "folder":
                clipped = ellipsize_text(self.font_md, entry["label"], name_max_w)
                name_s = self.font_md.render(clipped, True, text_color)
                ty = row_rect.y + (row_rect.h - name_s.get_height()) // 2
                self.screen.blit(name_s, (row_rect.x + 10, ty))
            else:
                patch = entry["patch"]
                name_clipped = ellipsize_text(self.font_md, patch["name"], name_max_w)
                name_s = self.font_md.render(name_clipped, True, text_color)
                self.screen.blit(name_s, (row_rect.x + 10, row_rect.y + 6))
                inst_clipped = ellipsize_text(
                    self.font_sm,
                    patch_browse_instrument_subtitle(patch),
                    name_max_w,
                )
                inst_s = self.font_sm.render(inst_clipped, True, self.theme.muted)
                self.screen.blit(inst_s, (row_rect.x + 10, row_rect.y + 26))

            if is_loaded:
                pygame.draw.circle(
                    self.screen,
                    self.theme.accent,
                    (row_rect.right - 16, row_rect.centery),
                    5,
                )

            y += row_h

        self.screen.set_clip(clip)

    def _draw_az_rail(self) -> None:
        if self.az_rail_rect.w <= 0:
            return
        pygame.draw.rect(
            self.screen,
            self.theme.surface,
            self.az_rail_rect.pygame_rect,
            border_radius=8,
        )
        now = time.time()
        active_letter = getattr(self, "_az_rail_active_letter", None)
        active_until = getattr(self, "_az_rail_active_until", 0.0)
        scrub_letter = getattr(self, "_az_rail_scrub_letter", None)
        capturing = getattr(self, "_az_rail_capture", False)
        for letter, rect in self.az_rail_letter_rects:
            has_patches = letter in self._all_patches_display_letter_index
            is_active = letter == active_letter and now < active_until
            is_pressed = capturing and letter == scrub_letter
            if is_active or is_pressed:
                pill = pygame.Rect(
                    rect.x - 8,
                    rect.y + 1,
                    rect.w + 10,
                    max(rect.h - 2, 10),
                )
                pygame.draw.rect(self.screen, self.theme.surface_alt, pill, border_radius=6)
                pygame.draw.rect(self.screen, self.theme.accent, pill, width=2, border_radius=6)
            label = "#" if letter == "#" else letter
            if is_active or is_pressed:
                color = self.theme.accent
            elif has_patches:
                color = self.theme.text
            else:
                color = self.theme.muted
            text = self.font_sm.render(label, True, color)
            tx = rect.x + (rect.w - text.get_width()) // 2
            ty = rect.y + max(0, (rect.h - text.get_height()) // 2)
            self.screen.blit(text, (tx, ty))

    def _draw_left_nav_expanded(self) -> None:
        pygame.draw.rect(self.screen, self.theme.surface, self.left_panel_rect.pygame_rect, border_radius=10)

        self._draw_nav_header()
        self._draw_instrument_chips()
        self._draw_folder_title_bar()

        if self.left_nav_mode == LeftNavMode.ALL_PATCHES:
            self._draw_all_patches_list()
            self._draw_az_rail()
        elif self.left_nav_mode == LeftNavMode.PATCHES:
            self._draw_browse_patches_list()
        else:
            font = self.font_sm
            self.nav_list.row_touch_feedback = self._row_touch_feedback
            self.nav_list.draw(self.screen, font, self.theme)
            self.nav_list.row_touch_feedback = None
    def _draw_main_detail(self) -> None:
        pygame.draw.rect(
            self.screen,
            self.theme.content_surface(),
            self.main_rect.pygame_rect,
            border_radius=10,
        )

        if not self.detail_patch:
            hint = self.font_md.render("Select a patch", True, self.theme.muted)
            sub = self.font_sm.render("Patch controls appear here", True, self.theme.muted)
            self.screen.blit(
                hint,
                (self.main_rect.x + 24, self.main_rect.centery - 30),
            )
            self.screen.blit(
                sub,
                (self.main_rect.x + 24, self.main_rect.centery),
            )
            return

        name_lines, cat_lines, name_y, cat_y, _header_bottom = self._detail_title_block()
        blit_text_block(
            self.screen,
            self.font_lg,
            name_lines,
            self.main_rect.x + DETAIL_TITLE_PAD_X,
            name_y,
            self.theme.text,
            line_spacing=4,
        )

        blit_text_block(
            self.screen,
            self.font_sm,
            cat_lines,
            self.main_rect.x + DETAIL_TITLE_PAD_X,
            cat_y,
            self.theme.muted,
            line_spacing=2,
        )

        self._draw_mixer_strip()
        self._draw_normalize_toggle(
            self.normalize_btn,
            self._normalization_enabled_for_detail(),
            has_gain=self._normalization_has_gain(),
            disabled=not self.loader.normalization.is_globally_enabled(),
        )
        self._draw_heart_icon(self.favorites_btn, self._patch_is_favorited(self.detail_patch))
    def _draw_browser(self) -> None:
        self.screen.fill(self.theme.bg)
        self._draw_status_bar()

        if self.left_nav_collapsed:
            self._draw_left_nav_collapsed()
        else:
            self._draw_left_nav_expanded()

        if self.left_nav_mode != LeftNavMode.ALL_PATCHES:
            self._draw_main_detail()

    def _draw_settings_action_row(
        self,
        rect: Rect,
        label: str,
        *,
        muted: bool = False,
        pressed: bool = False,
    ) -> None:
        if pressed:
            bg = self.theme.accent
            text_color = self.theme.bg
        else:
            bg = self.theme.surface_alt if muted else self.theme.surface
            text_color = self.theme.muted if muted else self.theme.text
        pygame.draw.rect(self.screen, bg, rect.pygame_rect, border_radius=10)
        draw_wrapped_text_in_rect(
            self.screen,
            self.font_md,
            label,
            rect.x,
            rect.y,
            rect.w,
            rect.h,
            text_color,
            pad_x=16,
            line_spacing=2,
            max_lines=2,
        )
    def _draw_settings(self) -> None:
        self._draw_browser()
        self._draw_settings_panel()

    def _draw_audio_profile_switch_overlay(self) -> None:
        """Blocking overlay while set-audio-profile.sh runs off the main thread."""
        self._draw_modal_backdrop(legacy_alpha=170)
        panel_w = min(420, self.width - 48)
        panel_h = 160
        panel = Rect((self.width - panel_w) // 2, (self.height - panel_h) // 2, panel_w, panel_h)
        self._draw_elevated_panel(panel, border_radius=16)

        elapsed = time.monotonic() - getattr(self, "_audio_profile_switch_started", time.monotonic())
        title = self.font_md.render("Switching audio…", True, self.theme.text)
        self.screen.blit(title, (panel.x + 24, panel.y + 28))

        from patch_browser.audio_profile import profile_switch_overlay_hint

        target = getattr(self, "_audio_profile_switch_target", None)
        progress = getattr(self, "_audio_switch_progress_hint", "") or ""
        static = profile_switch_overlay_hint(target) if target else "Restarting Surge for new audio route"
        hint = progress or static
        draw_wrapped_text_in_rect(
            self.screen,
            self.font_sm,
            hint,
            panel.x + 24,
            panel.y + 64,
            panel.w - 48,
            48,
            self.theme.muted,
            max_lines=2,
        )

        spin_y = panel.y + panel.h - 44
        phase = shutdown_animation_phase(elapsed)
        dot_count = 8
        radius = 14
        cx = panel.x + panel.w // 2
        cy = spin_y
        for i in range(dot_count):
            angle = (phase + i / dot_count) * 6.28318
            x = int(cx + radius * math.cos(angle))
            y = int(cy + radius * math.sin(angle))
            alpha = int(40 + 215 * (i / dot_count))
            color = tuple(min(255, int(c * alpha / 255)) for c in self.theme.accent)
            pygame.draw.circle(self.screen, color, (x, y), 4)

    def _draw_surge_audio_switch_overlay(self) -> None:
        """Blocking overlay while set-surge-audio.sh runs off the main thread."""
        self._draw_modal_backdrop(legacy_alpha=170)
        panel_w = min(420, self.width - 48)
        panel_h = 160
        panel = Rect((self.width - panel_w) // 2, (self.height - panel_h) // 2, panel_w, panel_h)
        self._draw_elevated_panel(panel, border_radius=16)

        elapsed = time.monotonic() - getattr(self, "_surge_audio_switch_started", time.monotonic())
        title = self.font_md.render("Applying audio settings…", True, self.theme.text)
        self.screen.blit(title, (panel.x + 24, panel.y + 28))

        hint = getattr(self, "_surge_audio_switch_hint", "") or "Restarting Surge"
        draw_wrapped_text_in_rect(
            self.screen,
            self.font_sm,
            hint,
            panel.x + 24,
            panel.y + 64,
            panel.w - 48,
            48,
            self.theme.muted,
            max_lines=2,
        )

        spin_y = panel.y + panel.h - 44
        phase = shutdown_animation_phase(elapsed)
        dot_count = 8
        radius = 14
        cx = panel.x + panel.w // 2
        cy = spin_y
        for i in range(dot_count):
            angle = (phase + i / dot_count) * 6.28318
            x = int(cx + radius * math.cos(angle))
            y = int(cy + radius * math.sin(angle))
            alpha = int(40 + 215 * (i / dot_count))
            color = tuple(min(255, int(c * alpha / 255)) for c in self.theme.accent)
            pygame.draw.circle(self.screen, color, (x, y), 4)

    def _draw_power_menu(self) -> None:
        self._draw_modal_backdrop(legacy_alpha=120)

        panel_w = min(360, self.width - 48)
        panel_h = 280
        panel = Rect((self.width - panel_w) // 2, (self.height - panel_h) // 2, panel_w, panel_h)
        self._draw_modal_shell(panel, border_radius=16)

        self.screen.blit(self.font_md.render("Power", True, self.theme.text), (panel.x + 24, panel.y + 20))

        self._power_option_rects = []
        y = panel.y + 70
        power_ids = ("power:shutdown", "power:restart", "power:cancel")
        for i, option in enumerate(["Shutdown", "Restart", "Cancel"]):
            rect = Rect(panel.x + 24, y, panel.w - 48, SETTINGS_ROW_H)
            self._power_option_rects.append(rect)
            self._draw_touch_row(
                rect,
                option,
                pressed=self._pressed(power_ids[i]),
                danger=option != "Cancel",
            )
            y += SETTINGS_ROW_H + SETTINGS_ROW_GAP
    def _draw_power_confirm(self) -> None:
        self._draw_modal_backdrop(legacy_alpha=150)

        panel_w = min(420, self.width - 48)
        panel_h = 220
        panel = Rect((self.width - panel_w) // 2, (self.height - panel_h) // 2, panel_w, panel_h)
        self._draw_modal_shell(panel, border_radius=16)

        action = "Shut down?" if self.power_action == "shutdown" else "Restart?"
        self.screen.blit(
            self.font_md.render(action, True, self._semantic_color("danger")),
            (panel.x + 24, panel.y + 24),
        )

        self._confirm_no = Rect(panel.x + 24, panel.y + 100, (panel.w - 60) // 2, 52)
        self._confirm_yes = Rect(self._confirm_no.x + self._confirm_no.w + 12, panel.y + 100, (panel.w - 60) // 2, 52)
        self._draw_button(self._confirm_no, "Cancel", pressed=self._pressed("confirm:cancel"))
        self._draw_button(self._confirm_yes, "Confirm", danger=True, pressed=self._pressed("confirm:yes"))

    def _draw_theme_section_label(self, x: int, y: int, label: str) -> None:
        self.screen.blit(self.font_sm.render(label, True, self.theme.muted), (x, y))

    def _draw_touch_row(
        self,
        rect: Rect,
        label: str,
        *,
        pressed: bool = False,
        selected: bool = False,
        danger: bool = False,
        muted: bool = False,
    ) -> None:
        if pressed:
            bg = self.theme.accent
            text_color = self.theme.bg
        elif muted:
            bg = self.theme.surface_alt
            text_color = self.theme.muted
        else:
            bg = self.theme.surface_alt
            text_color = self.theme.danger if danger else self.theme.text
        pygame.draw.rect(self.screen, bg, rect.pygame_rect, border_radius=10)
        if selected and not pressed:
            pygame.draw.rect(self.screen, self.theme.accent, rect.pygame_rect, width=2, border_radius=10)
            text_color = self.theme.accent
        self.screen.blit(
            self.font_md.render(label, True, text_color),
            (rect.x + 16, rect.y + (rect.h - self.font_md.get_height()) // 2),
        )

    def _draw_theme_choice(
        self,
        rect: Rect,
        label: str,
        *,
        selected: bool,
        pressed: bool = False,
    ) -> None:
        if pressed:
            bg = self.theme.accent
            label_color = self.theme.bg
        else:
            bg = self.theme.surface_alt
            label_color = self.theme.accent if selected else self.theme.text
        pygame.draw.rect(self.screen, bg, rect.pygame_rect, border_radius=10)
        if selected and not pressed:
            pygame.draw.rect(self.screen, self.theme.accent, rect.pygame_rect, width=2, border_radius=10)
        self.screen.blit(
            self.font_sm.render(label, True, label_color),
            (rect.x + 12, rect.y + (rect.h - self.font_sm.get_height()) // 2),
        )

    def _draw_theme_swatch(
        self,
        rect: Rect,
        rgb: tuple[int, int, int],
        *,
        selected: bool,
        pressed: bool = False,
    ) -> None:
        pygame.draw.rect(self.screen, rgb, rect.pygame_rect, border_radius=8)
        if pressed:
            pygame.draw.rect(self.screen, self.theme.accent, rect.pygame_rect, width=3, border_radius=8)
        elif selected:
            pygame.draw.rect(self.screen, self.theme.text, rect.pygame_rect, width=2, border_radius=8)

    def _draw_theme_add_swatch(self, rect: Rect) -> None:
        pygame.draw.rect(self.screen, self.theme.surface_alt, rect.pygame_rect, border_radius=8)
        pygame.draw.rect(self.screen, self.theme.muted, rect.pygame_rect, width=1, border_radius=8)
        label = self.font_md.render("+", True, self.theme.text)
        self.screen.blit(
            label,
            (rect.x + (rect.w - label.get_width()) // 2, rect.y + (rect.h - label.get_height()) // 2),
        )

    def _theme_swatch_grid_layout(
        self,
        *,
        y: int,
        inner_w: int,
        entries: list[tuple[str, tuple[int, int, int], bool]],
        swatch_size: int = 40,
        swatch_gap: int = 10,
        cols: int = 4,
    ) -> tuple[int, list[tuple[Rect, tuple[int, int, int], str]], list[tuple[Rect, str]]]:
        swatch_rects: list[tuple[Rect, tuple[int, int, int], str]] = []
        delete_rects: list[tuple[Rect, str]] = []
        delete_size = 18
        for index, (hit_id, rgb, deletable) in enumerate(entries):
            row = index // cols
            col = index % cols
            total_row_w = cols * swatch_size + (cols - 1) * swatch_gap
            row_x = max(0, (inner_w - total_row_w) // 2)
            rect = Rect(
                row_x + col * (swatch_size + swatch_gap),
                y + row * (swatch_size + swatch_gap),
                swatch_size,
                swatch_size,
            )
            swatch_rects.append((rect, rgb, hit_id))
            if deletable:
                delete_rect = Rect(rect.right - delete_size + 4, rect.y - 4, delete_size, delete_size)
                delete_rects.append((delete_rect, hit_id.removeprefix("custom:")))
        rows = (len(entries) + cols - 1) // cols if entries else 0
        next_y = y + rows * swatch_size + max(0, rows - 1) * swatch_gap
        return next_y, swatch_rects, delete_rects

    def _draw_theme_swatch_grid(
        self,
        *,
        origin_x: int,
        origin_y: int,
        scroll: int,
        swatch_rects: list[tuple[Rect, tuple[int, int, int], str]],
        delete_rects: list[tuple[Rect, str]],
        draft_rgb: tuple[int, int, int],
    ) -> None:
        for rect, rgb, hit_id in swatch_rects:
            screen_rect = Rect(origin_x + rect.x, origin_y + rect.y - scroll, rect.w, rect.h)
            selected = draft_rgb == rgb
            pressed = self._pressed(hit_id)
            if hit_id == "custom_new":
                if pressed:
                    pygame.draw.rect(self.screen, self.theme.accent, screen_rect.pygame_rect, border_radius=8)
                    label = self.font_md.render("+", True, self.theme.bg)
                else:
                    self._draw_theme_add_swatch(screen_rect)
                    label = None
                if label is not None:
                    self.screen.blit(
                        label,
                        (
                            screen_rect.x + (screen_rect.w - label.get_width()) // 2,
                            screen_rect.y + (screen_rect.h - label.get_height()) // 2,
                        ),
                    )
            else:
                self._draw_theme_swatch(screen_rect, rgb, selected=selected, pressed=pressed)
        for delete_rect, color_id in delete_rects:
            delete_hit = f"delete:{color_id}"
            screen_rect = Rect(
                origin_x + delete_rect.x,
                origin_y + delete_rect.y - scroll,
                delete_rect.w,
                delete_rect.h,
            )
            pressed = self._pressed(delete_hit)
            bg = self.theme.accent if pressed else self.theme.surface
            text_color = self.theme.bg if pressed else self.theme.text
            pygame.draw.rect(self.screen, bg, screen_rect.pygame_rect, border_radius=9)
            pygame.draw.rect(self.screen, self.theme.muted, screen_rect.pygame_rect, width=1, border_radius=9)
            cross = self.font_sm.render("×", True, text_color)
            self.screen.blit(
                cross,
                (
                    screen_rect.x + (screen_rect.w - cross.get_width()) // 2,
                    screen_rect.y + (screen_rect.h - cross.get_height()) // 2 - 1,
                ),
            )

    def _draw_theme_main_panel(self, panel: Rect) -> None:
        draft = self._theme_draft()
        inner_x = panel.x + 24
        inner_w = panel.w - 48
        gap = SETTINGS_ROW_GAP
        option_h = 44
        col_gap = 10
        col_w = (inner_w - col_gap) // 2
        y = panel.y + 56

        self._draw_theme_section_label(inner_x, y, "Base theme")
        y += self.font_sm.get_height() + 8
        self._theme_base_option_rects = [
            Rect(inner_x, y, col_w, option_h),
            Rect(inner_x + col_w + col_gap, y, col_w, option_h),
        ]
        self._draw_theme_choice(
            self._theme_base_option_rects[0],
            "Original dark",
            selected=draft.theme_mode == THEME_MODE_STANDARD,
            pressed=self._pressed("base:0"),
        )
        self._draw_theme_choice(
            self._theme_base_option_rects[1],
            "OLED dark",
            selected=draft.theme_mode == THEME_MODE_OLED_BLACK,
            pressed=self._pressed("base:1"),
        )
        y += option_h + gap + 8

        self._draw_theme_section_label(inner_x, y, "Accent style")
        y += self.font_sm.get_height() + 8
        self._theme_style_option_rects = [
            Rect(inner_x, y, col_w, option_h),
            Rect(inner_x + col_w + col_gap, y, col_w, option_h),
        ]
        self._draw_theme_choice(
            self._theme_style_option_rects[0],
            "Monochrome",
            selected=draft.accent_style == ACCENT_STYLE_MONOCHROME,
            pressed=self._pressed("style:0"),
        )
        self._draw_theme_choice(
            self._theme_style_option_rects[1],
            "Minimal accent",
            selected=draft.accent_style == ACCENT_STYLE_MINIMAL,
            pressed=self._pressed("style:1"),
        )
        y += option_h + gap + 8

        self._draw_theme_section_label(inner_x, y, "Accent color")
        y += self.font_sm.get_height() + 8
        preview_h = 52
        self._theme_accent_preview_rect = Rect(inner_x, y, preview_h, preview_h)
        self._draw_theme_swatch(self._theme_accent_preview_rect, draft.accent_rgb, selected=True)
        hex_label = self.font_md.render(rgb_to_hex(draft.accent_rgb), True, self.theme.text)
        self.screen.blit(hex_label, (self._theme_accent_preview_rect.right + 16, y + 8))
        choose_h = 44
        self._theme_choose_color_btn = Rect(inner_x, y + preview_h + 10, inner_w, choose_h)
        self._draw_button(
            self._theme_choose_color_btn,
            "Choose color…",
            pressed=self._pressed("choose_colors"),
        )

        self._theme_color_swatch_rects = []
        self._theme_color_delete_rects = []

        btn_h = 52
        btn_gap = 12
        btn_y = panel.bottom - btn_h - 20
        self._theme_cancel_rect = Rect(inner_x, btn_y, (inner_w - btn_gap) // 2, btn_h)
        self._theme_done_rect = Rect(
            self._theme_cancel_rect.right + btn_gap,
            btn_y,
            (inner_w - btn_gap) // 2,
            btn_h,
        )
        self._draw_button(self._theme_cancel_rect, "Cancel", pressed=self._pressed("cancel"))
        self._draw_button(self._theme_done_rect, "Done", accent=True, pressed=self._pressed("done"))

    def _draw_theme_colors_panel(self, panel: Rect) -> None:
        draft = self._theme_draft()
        inner_x = panel.x + 24
        inner_w = panel.w - 48
        label_h = self.font_sm.get_height()
        section_gap = 8
        block_gap = 12

        back_h = 52
        footer_y = panel.bottom - back_h - 20
        self._theme_colors_back_rect = Rect(inner_x, footer_y, inner_w, back_h)

        scroll_top = panel.y + 56
        scroll_h = max(80, footer_y - scroll_top - 8)
        scroll_vp = Rect(inner_x, scroll_top, inner_w, scroll_h)
        self._theme_colors_scroll.viewport = scroll_vp

        sections: list[tuple[int, str]] = []
        cy = 0
        sections.append((cy, "Presets"))
        cy += label_h + section_gap
        preset_entries = [(f"preset:{i}", rgb, False) for i, (_name, rgb) in enumerate(ACCENT_PRESETS)]
        cy, preset_rects, _preset_delete = self._theme_swatch_grid_layout(
            y=cy,
            inner_w=inner_w,
            entries=preset_entries,
        )
        cy += block_gap

        custom_colors = getattr(self, "_custom_accent_colors", [])
        saved_rects: list[tuple[Rect, tuple[int, int, int], str]] = []
        saved_delete: list[tuple[Rect, str]] = []
        if custom_colors:
            sections.append((cy, "Saved"))
            cy += label_h + section_gap
            saved_entries = [(f"custom:{color.color_id}", color.rgb, True) for color in custom_colors]
            cy, saved_rects, saved_delete = self._theme_swatch_grid_layout(
                y=cy,
                inner_w=inner_w,
                entries=saved_entries,
            )
            cy += 8

        sections.append((cy, "Custom"))
        cy += label_h + section_gap
        cy, custom_rects, custom_delete = self._theme_swatch_grid_layout(
            y=cy,
            inner_w=inner_w,
            entries=[("custom_new", draft.accent_rgb, False)],
            cols=1,
        )

        self._theme_colors_scroll.content_height = cy
        self._theme_color_swatch_rects_content = preset_rects + saved_rects + custom_rects
        self._theme_color_delete_rects_content = saved_delete + custom_delete
        self._theme_color_swatch_rects = []
        self._theme_color_delete_rects = []
        self._theme_base_option_rects = []
        self._theme_style_option_rects = []

        scroll = int(self._theme_colors_scroll.scroll_pixels)
        clip = self.screen.get_clip()
        self.screen.set_clip(scroll_vp.pygame_rect)
        for section_y, title in sections:
            self._draw_theme_section_label(inner_x, scroll_top + section_y - scroll, title)
        all_swatch_rects = preset_rects + saved_rects + custom_rects
        all_delete_rects = saved_delete + custom_delete
        self._draw_theme_swatch_grid(
            origin_x=inner_x,
            origin_y=scroll_top,
            scroll=scroll,
            swatch_rects=all_swatch_rects,
            delete_rects=all_delete_rects,
            draft_rgb=draft.accent_rgb,
        )
        self.screen.set_clip(clip)

        draw_vertical_scroll_edge_hints(
            self.screen,
            scroll_vp,
            self._theme_colors_scroll,
            self.theme,
        )
        self._draw_button(self._theme_colors_back_rect, "Back", pressed=self._pressed("colors_back"))
        self._theme_cancel_rect = None
        self._theme_done_rect = None

    def _draw_theme_picker_panel(self, panel: Rect) -> None:
        inner_x = panel.x + 24
        inner_w = panel.w - 48
        rgb = getattr(self, "_picker_rgb", self._theme_draft().accent_rgb)
        y = panel.y + 56

        preview_h = 64
        self._picker_preview_rect = Rect(inner_x, y, inner_w, preview_h)
        pygame.draw.rect(self.screen, rgb, self._picker_preview_rect.pygame_rect, border_radius=12)
        hex_surf = self.font_md.render(rgb_to_hex(rgb), True, self.theme.bg if sum(rgb) > 382 else self.theme.text)
        self.screen.blit(
            hex_surf,
            (
                self._picker_preview_rect.x + 16,
                self._picker_preview_rect.y + (preview_h - hex_surf.get_height()) // 2,
            ),
        )
        y += preview_h + 20

        slider_h = 36
        slider_gap = 28
        self._picker_slider_rects = {}
        for channel, label, value in (
            ("r", "Red", rgb[0]),
            ("g", "Green", rgb[1]),
            ("b", "Blue", rgb[2]),
        ):
            slider = Rect(inner_x, y + 18, inner_w, slider_h)
            self._picker_slider_rects[channel] = slider
            self._draw_slider(slider, value / 255.0, f"{label}  {value}")
            y += slider_h + slider_gap

        btn_h = 44
        btn_gap = 10
        btn_y = panel.bottom - btn_h - 20
        btn_w = (inner_w - btn_gap * 2) // 3
        self._picker_back_rect = Rect(inner_x, btn_y, btn_w, btn_h)
        self._picker_save_rect = Rect(self._picker_back_rect.right + btn_gap, btn_y, btn_w, btn_h)
        self._picker_delete_rect = Rect(self._picker_save_rect.right + btn_gap, btn_y, btn_w, btn_h)
        self._draw_button(self._picker_back_rect, "Back", pressed=self._pressed("picker_back"))
        self._draw_button(self._picker_save_rect, "Save", accent=True, pressed=self._pressed("picker_save"))
        can_delete = getattr(self, "_picker_editing_id", None) is not None
        self._draw_button(
            self._picker_delete_rect,
            "Delete",
            danger=can_delete,
            muted=not can_delete,
            pressed=self._pressed("picker_delete"),
        )

        self._theme_color_swatch_rects = []
        self._theme_color_delete_rects = []
        self._theme_base_option_rects = []
        self._theme_style_option_rects = []
        self._theme_cancel_rect = None
        self._theme_done_rect = None

    def _draw_theme_modal(self) -> None:
        self._draw_modal_backdrop(legacy_alpha=150)

        draft = self._theme_draft()
        view = self._theme_view()
        panel_w = min(520, self.width - 48)
        panel_h = min(456, self.height - 32)
        panel = Rect((self.width - panel_w) // 2, (self.height - panel_h) // 2, panel_w, panel_h)
        self._draw_modal_shell(panel, border_radius=16)

        if view == THEME_VIEW_PICKER:
            title = "Custom color"
        elif view == THEME_VIEW_COLORS:
            title = "Accent color"
        else:
            title = "Theme"
        self.screen.blit(self.font_md.render(title, True, self.theme.text), (panel.x + 24, panel.y + 18))

        if view == THEME_VIEW_PICKER:
            self._draw_theme_picker_panel(panel)
        elif view == THEME_VIEW_COLORS:
            self._draw_theme_colors_panel(panel)
        else:
            self._draw_theme_main_panel(panel)

    def _picker_channel_from_x(self, x: int, rect: Rect) -> int:
        if rect.w <= 0:
            return 0
        ratio = (x - rect.x) / rect.w
        return max(0, min(255, round(ratio * 255)))

    def _draw_calibrate_confirm(self) -> None:
        self._draw_modal_backdrop(legacy_alpha=150)

        mode = self._pending_calibrate_mode
        targets, total = self._calibration_scope_stats(mode)
        title = self._calibration_mode_label(mode)

        panel_w = min(520, self.width - 48)
        body_raw = (
            self._calibration_mode_description(mode, targets, total),
            "Touch browser will stop; loader takes over the display.",
            self._calibration_duration_hint(targets),
            "Do not touch the screen during measurement.",
        )
        body_max_w = panel_w - 48
        body_lines: list[str] = []
        for paragraph in body_raw:
            body_lines.extend(wrap_text_lines(self.font_sm, paragraph, body_max_w))

        title_surf = self.font_md.render(f"{title}?", True, self.theme.text)
        body_h = text_block_height(self.font_sm, len(body_lines), line_spacing=6)
        btn_h = 52
        btn_gap = 12
        panel_h = 18 + title_surf.get_height() + 16 + body_h + 20 + btn_h + 24
        panel = Rect((self.width - panel_w) // 2, (self.height - panel_h) // 2, panel_w, panel_h)
        self._draw_modal_shell(panel, border_radius=16)

        self.screen.blit(title_surf, (panel.x + 24, panel.y + 18))

        body_y = panel.y + 18 + title_surf.get_height() + 16
        blit_text_block(
            self.screen,
            self.font_sm,
            body_lines,
            panel.x + 24,
            body_y,
            self.theme.muted,
            line_spacing=6,
        )

        btn_y = body_y + body_h + 20
        self._calibrate_confirm_no = Rect(panel.x + 24, btn_y, (panel.w - 60) // 2, btn_h)
        self._calibrate_confirm_yes = Rect(
            self._calibrate_confirm_no.x + self._calibrate_confirm_no.w + btn_gap,
            btn_y,
            (panel.w - 60) // 2,
            btn_h,
        )
        start_disabled = mode == CalibrateMode.MISSING_ONLY and targets == 0
        self._draw_button(self._calibrate_confirm_no, "Cancel", pressed=self._pressed("cal:cancel"))
        self._draw_button(
            self._calibrate_confirm_yes,
            "Start",
            accent=not start_disabled,
            muted=start_disabled,
            pressed=self._pressed("cal:start"),
        )
    def _draw_toast(self) -> None:
        if time.time() > self.toast_until or not self.toast_message:
            return
        max_w = min(560, self.width - 48)
        lines = wrap_text_lines(self.font_sm, self.toast_message, max_w, max_lines=3)
        line_surfs = [self.font_sm.render(line, True, self.theme.text) for line in lines]
        pad_x, pad_y = 16, 10
        w = max(s.get_width() for s in line_surfs) + pad_x * 2
        block_h = text_block_height(self.font_sm, len(lines), line_spacing=2)
        h = block_h + pad_y * 2
        rect = pygame.Rect((self.width - w) // 2, self.height - 80 - max(0, block_h - 20), w, h)
        pygame.draw.rect(self.screen, self.theme.surface_alt, rect, border_radius=10)
        blit_text_block(
            self.screen,
            self.font_sm,
            lines,
            rect.x + pad_x,
            rect.y + pad_y,
            self.theme.text,
            line_spacing=2,
        )
