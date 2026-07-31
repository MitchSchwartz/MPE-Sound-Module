"""Touch patch browser — draw mixin."""

from __future__ import annotations

import json
import math
import os
import queue
import subprocess
import threading
import time
from pathlib import Path

import pygame

from patch_browser.calibration_constants import (
    MPE_CALIB_FROM_BROWSER,
    MPE_CALIB_FROM_BROWSER_ACTIVE,
)
from patch_browser.geometry import Rect
from patch_browser.mixer import MixerChannel
from patch_browser.patch_scanner import FAVORITES_NAME, favorites_display_name
from patch_browser.scroll_widgets import ContentScrollArea, ScrollList
from patch_browser.touch_ui_constants import *
from patch_browser.touch_ui_enums import (
    CalibrateMode,
    LeftNavMode,
    Screen,
    audio_profile_display,
)
from patch_browser.ui_prefs import (
    load_ui_preference,
    load_volume_level,
    read_ui_prefs_file,
    save_theme_mode,
    save_ui_preference,
    save_volume_level,
    write_ui_prefs_file,
)
from patch_browser.ui_text import (
    blit_text_block,
    draw_wrapped_text_in_rect,
    ellipsize_text,
    text_block_height,
    wrap_text_lines,
    wrapped_row_height,
)
from patch_browser.ui_theme import THEME_MODE_OLED_BLACK, THEME_MODE_STANDARD, Theme


class TouchBrowserDrawMixin:
    """Mixin — expects TouchPatchBrowser host attributes."""

    def _draw_heart_icon(self, rect: Rect, filled: bool) -> None:
        pygame.draw.rect(self.screen, self.theme.surface_alt, rect.pygame_rect, border_radius=8)
        symbol = "♥" if filled else "♡"
        color = self.theme.danger if filled else self.theme.muted
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
    ) -> None:
        if muted:
            color = self.theme.surface
        elif accent:
            color = self.theme.accent
        else:
            color = self.theme.surface_alt
        pygame.draw.rect(self.screen, color, rect.pygame_rect, border_radius=8)
        icon_color = (255, 255, 255) if accent else self.theme.text
        if icon == "back":
            self._draw_chevron(self.screen, rect, icon_color, direction="left")
        elif icon == "panel_close":
            self._draw_sidebar_panel_icon(self.screen, rect, icon_color, panel_open=True)
        elif icon == "panel_open":
            self._draw_sidebar_panel_icon(self.screen, rect, icon_color, panel_open=False)
    def _draw_sidebar_panel_icon(
        surface: pygame.Surface,
        rect: Rect,
        color: tuple[int, int, int],
        *,
        panel_open: bool,
    ) -> None:
        """Sidebar panel open/close — split layout icon (not a plain back chevron)."""
        pad = 6
        ix = rect.x + pad
        iy = rect.y + (rect.h - 14) // 2
        iw = max(18, rect.w - pad * 2)
        ih = 14
        split_x = ix + max(6, int(iw * 0.36))

        frame = pygame.Rect(ix, iy, iw, ih)
        pygame.draw.rect(surface, color, frame, width=2, border_radius=2)
        pygame.draw.line(surface, color, (split_x, iy + 2), (split_x, iy + ih - 2), 2)

        cy = iy + ih // 2
        if panel_open:
            sidebar = pygame.Rect(ix + 2, iy + 2, split_x - ix - 3, ih - 4)
            pygame.draw.rect(surface, color, sidebar, border_radius=1)
            cx = split_x + (ix + iw - split_x) // 2 + 1
            for dx in (4, 9):
                points = [(cx + dx, cy - 4), (cx + dx - 4, cy), (cx + dx, cy + 4)]
                pygame.draw.lines(surface, color, False, points, 2)
        else:
            strip_w = max(4, int(iw * 0.14))
            strip = pygame.Rect(ix + 2, iy + 2, strip_w, ih - 4)
            pygame.draw.rect(surface, color, strip, border_radius=1)
            cx = ix + strip_w + (iw - strip_w) // 2
            for dx in (-4, -9):
                points = [(cx + dx, cy - 4), (cx + dx + 4, cy), (cx + dx, cy + 4)]
                pygame.draw.lines(surface, color, False, points, 2)
    def _draw_chevron(
        surface: pygame.Surface,
        rect: Rect,
        color: tuple[int, int, int],
        *,
        direction: str,
    ) -> None:
        cx, cy = rect.centerx, rect.centery
        if direction == "left":
            points = [(cx + 5, cy - 8), (cx - 5, cy), (cx + 5, cy + 8)]
        else:
            points = [(cx - 5, cy - 8), (cx + 5, cy), (cx - 5, cy + 8)]
        pygame.draw.lines(surface, color, False, points, 3)
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
        line.fill((255, 255, 255, self.theme.hairline_alpha))
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
    def _draw_nav_header(self) -> None:
        pygame.draw.rect(self.screen, self.theme.surface, self.nav_header_rect.pygame_rect)

        if self.left_nav_mode == LeftNavMode.PATCHES:
            self._draw_icon_button(self.nav_back_btn, "back", muted=True)
        if self._show_current_folder_button():
            self._draw_button(self.nav_current_btn, "Current", small=True, accent=True)
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
        folder_name = self._browse_category_name()
        max_w = max(1, rect.w - 24)
        clipped = ellipsize_text(self.font_sm, folder_name, max_w)
        label = self.font_sm.render(clipped, True, self.theme.text)
        self.screen.blit(label, (rect.x + 12, rect.y + (rect.h - label.get_height()) // 2))
    def _draw_button(
        self,
        rect: Rect,
        label: str,
        accent: bool = False,
        small: bool = False,
        muted: bool = False,
    ) -> None:
        if muted:
            color = self.theme.surface
        elif accent:
            color = self.theme.accent
        else:
            color = self.theme.surface_alt
        pygame.draw.rect(self.screen, color, rect.pygame_rect, border_radius=8)
        font = self.font_sm if small else self.font_md
        text_color = (255, 255, 255) if accent else self.theme.text
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

        if channel.enabled and channel.channel_id == "volume":
            value_label = f"{round(value * 100)}"
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
        self.screen.blit(label, (rect.x, rect.y))

        bar_y = rect.y + label.get_height() + 1
        bar_rect = pygame.Rect(rect.x, bar_y, rect.w, CPU_METER_BAR_H)
        pygame.draw.rect(self.screen, self.theme.surface_alt, bar_rect, border_radius=3)

        if not snap["online"] or snap["percent"] is None:
            dash = self.font_sm.render("—", True, self.theme.muted)
            dash_x = bar_rect.x + (bar_rect.w - dash.get_width()) // 2
            dash_y = bar_rect.y + (bar_rect.h - dash.get_height()) // 2
            self.screen.blit(dash, (dash_x, dash_y))
            return

        percent = max(0.0, min(100.0, float(snap["percent"])))
        fill_w = max(1, int(bar_rect.w * (percent / 100.0)))
        fill_rect = pygame.Rect(bar_rect.x, bar_rect.y, fill_w, bar_rect.h)
        pygame.draw.rect(
            self.screen,
            self._cpu_meter_color(percent),
            fill_rect,
            border_radius=3,
        )
    def _draw_status_bar(self) -> None:
        pygame.draw.rect(self.screen, self.theme.surface, self.status_rect.pygame_rect, border_radius=10)
        if self.loaded_patch_info:
            title = self.loaded_patch_info["name"]
            subtitle = self.loaded_patch_info["category"]
        else:
            title = "No patch loaded"
            subtitle = "Select a patch from the left list"

        title_max_w = max(1, self.status_rect.w - self.system_settings_btn.w - 36)
        title_lines = wrap_text_lines(self.font_md, title, title_max_w, max_lines=1)
        self.screen.blit(
            self.font_md.render(title_lines[0], True, self.theme.text),
            (self.status_rect.x + 12, self.status_rect.y + 6),
        )
        sub_lines = wrap_text_lines(self.font_sm, subtitle, title_max_w, max_lines=1)
        self.screen.blit(
            self.font_sm.render(sub_lines[0], True, self.theme.muted),
            (self.status_rect.x + 12, self.status_rect.y + 26),
        )
        if self.show_cpu_meter:
            self._draw_cpu_meter(self.cpu_meter_rect)
        self._draw_button(self.system_settings_btn, "...", small=True, muted=True)
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
    def _draw_left_nav_expanded(self) -> None:
        pygame.draw.rect(self.screen, self.theme.surface, self.left_panel_rect.pygame_rect, border_radius=10)

        self._draw_nav_header()
        self._draw_folder_title_bar()

        font = self.font_md if self.left_nav_mode == LeftNavMode.PATCHES else self.font_sm
        self.nav_list.draw(self.screen, font, self.theme)
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

        name_lines = wrap_text_lines(
            self.font_lg,
            self.detail_patch["name"],
            max(1, self.main_rect.w - 48),
            max_lines=2,
        )
        name_block_h = text_block_height(self.font_lg, len(name_lines), line_spacing=4)
        blit_text_block(
            self.screen,
            self.font_lg,
            name_lines,
            self.main_rect.x + 24,
            self.main_rect.y + 24,
            self.theme.text,
            line_spacing=4,
        )

        cat_y = self.main_rect.y + 24 + name_block_h + 8
        cat_lines = wrap_text_lines(
            self.font_sm,
            self.detail_patch["category"],
            max(1, self.main_rect.w - 48),
            max_lines=2,
        )
        blit_text_block(
            self.screen,
            self.font_sm,
            cat_lines,
            self.main_rect.x + 24,
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

        self._draw_main_detail()

        footer = self.font_sm.render(
            "Scroll without selecting · tap patch to load",
            True,
            self.theme.muted,
        )
        self.screen.blit(footer, (24, self.height - 20))
    def _draw_settings_action_row(self, rect: Rect, label: str, *, muted: bool = False) -> None:
        bg = self.theme.surface_alt if muted else self.theme.surface
        pygame.draw.rect(self.screen, bg, rect.pygame_rect, border_radius=10)
        text_color = self.theme.muted if muted else self.theme.text
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
    def _draw_settings_panel(self) -> None:
        panel = self._settings_panel_screen_rect()
        alpha = self._settings_overlay_alpha()
        overlay = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, alpha))
        self.screen.blit(overlay, (0, 0))

        self._draw_elevated_panel(panel, border_radius=16)
        if self.theme.backdrop_alpha is None:
            shadow = pygame.Surface((panel.w, panel.h), pygame.SRCALPHA)
            shadow.fill((0, 0, 0, 40))
            self.screen.blit(shadow, (panel.x - 4, panel.y + 2))

        header_rect = Rect(panel.x, panel.y, panel.w, SETTINGS_PANEL_HEADER_H)
        self._draw_divider_line(
            header_rect.x + 16,
            header_rect.bottom - 1,
            header_rect.right - 16,
        )
        self.screen.blit(
            self.font_md.render("System", True, self.theme.text),
            (panel.x + 20, panel.y + 16),
        )
        close_screen = self._panel_local_to_screen(self._close_settings_btn)
        self._draw_icon_button(close_screen, "×", muted=True)

        scroll_vp = self._settings_scroll_viewport_screen()
        clip = self.screen.get_clip()
        self.screen.set_clip(scroll_vp.pygame_rect)

        scroll = int(self._settings_content_scroll.scroll_pixels)
        content_x = panel.x

        slider = self._panel_local_to_screen(self.brightness_slider_rect, scrolled=True)
        self._draw_slider(
            slider,
            self.brightness_percent / 100.0,
            f"Brightness  {self.brightness_percent}%",
        )

        cpu_toggle = self._panel_local_to_screen(self.cpu_meter_toggle_rect, scrolled=True)
        self._draw_normalize_toggle(
            cpu_toggle,
            self.show_cpu_meter,
            has_gain=True,
            label="CPU meter",
        )

        oled_toggle = self._panel_local_to_screen(self.oled_black_toggle_rect, scrolled=True)
        self._draw_normalize_toggle(
            oled_toggle,
            self.theme_mode == THEME_MODE_OLED_BLACK,
            has_gain=True,
            label="OLED black",
        )

        norm_toggle = self._panel_local_to_screen(self.norm_global_toggle_rect, scrolled=True)
        self._draw_normalize_toggle(
            norm_toggle,
            self.loader.normalization.is_globally_enabled(),
            has_gain=True,
            label="Patch normalization",
        )

        audio_y = panel.y + self._settings_audio_profile_y - scroll
        audio_lines = wrap_text_lines(
            self.font_sm,
            audio_profile_display(),
            self.settings_panel_rect.w - 48,
            max_lines=1,
        )
        blit_text_block(
            self.screen,
            self.font_sm,
            audio_lines,
            content_x + 20,
            audio_y,
            self.theme.muted,
            line_spacing=2,
        )

        status = self.surge_monitor.get_status_summary()
        status_y = panel.y + self._settings_status_y - scroll
        status_lines = wrap_text_lines(
            self.font_sm,
            f"Surge: {status['status']} — {status['details']}",
            self.settings_panel_rect.w - 48,
            max_lines=2,
        )
        status_color = self.theme.ok if status["status"] == "Running" else self.theme.danger
        blit_text_block(
            self.screen,
            self.font_sm,
            status_lines,
            content_x + 20,
            status_y,
            status_color,
            line_spacing=2,
        )

        if self._surge_restart_btn:
            restart = self._panel_local_to_screen(self._surge_restart_btn, scrolled=True)
            self._draw_settings_action_row(restart, "Restart Surge")

        cal_missing = self._panel_local_to_screen(self._calibrate_missing_btn, scrolled=True)
        self._draw_settings_action_row(cal_missing, "Calibrate missing patches")

        cal_force = self._panel_local_to_screen(self._calibrate_force_btn, scrolled=True)
        self._draw_settings_action_row(cal_force, "Force full re-calibration", muted=True)

        self.screen.set_clip(clip)

        footer_y = panel.y + self.settings_panel_rect.h - SETTINGS_PANEL_FOOTER_H
        self._draw_divider_line(panel.x + 16, footer_y, panel.right - 16)
        power = self._panel_local_to_screen(self._power_btn)
        pygame.draw.rect(self.screen, self.theme.surface_alt, power.pygame_rect, border_radius=10)
        draw_wrapped_text_in_rect(
            self.screen,
            self.font_md,
            "Power…",
            power.x,
            power.y,
            power.w,
            power.h,
            self.theme.text,
            pad_x=16,
            max_lines=1,
        )
    def _draw_settings(self) -> None:
        self._draw_browser()
        self._draw_settings_panel()
    def _draw_power_menu(self) -> None:
        self._draw_modal_backdrop(legacy_alpha=120)

        panel_w = min(360, self.width - 48)
        panel_h = 280
        panel = Rect((self.width - panel_w) // 2, (self.height - panel_h) // 2, panel_w, panel_h)
        self._draw_elevated_panel(panel, border_radius=16)

        self.screen.blit(self.font_md.render("Power", True, self.theme.text), (panel.x + 24, panel.y + 20))

        self._power_option_rects = []
        y = panel.y + 70
        for i, option in enumerate(["Shutdown", "Restart", "Cancel"]):
            rect = Rect(panel.x + 24, y, panel.w - 48, SETTINGS_ROW_H)
            self._power_option_rects.append(rect)
            color = self.theme.accent if i == 2 else self.theme.surface_alt
            pygame.draw.rect(self.screen, color, rect.pygame_rect, border_radius=10)
            self.screen.blit(
                self.font_md.render(option, True, self.theme.text),
                (rect.x + 16, rect.y + (rect.h - self.font_md.get_height()) // 2),
            )
            y += SETTINGS_ROW_H + SETTINGS_ROW_GAP
    def _draw_power_confirm(self) -> None:
        self._draw_modal_backdrop(legacy_alpha=150)

        panel_w = min(420, self.width - 48)
        panel_h = 220
        panel = Rect((self.width - panel_w) // 2, (self.height - panel_h) // 2, panel_w, panel_h)
        self._draw_elevated_panel(panel, border_radius=16)

        action = "Shut down?" if self.power_action == "shutdown" else "Restart?"
        self.screen.blit(self.font_md.render(action, True, self.theme.danger), (panel.x + 24, panel.y + 24))

        self._confirm_no = Rect(panel.x + 24, panel.y + 100, (panel.w - 60) // 2, 52)
        self._confirm_yes = Rect(self._confirm_no.x + self._confirm_no.w + 12, panel.y + 100, (panel.w - 60) // 2, 52)
        self._draw_button(self._confirm_no, "Cancel", accent=True)
        self._draw_button(self._confirm_yes, "Confirm")
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
        self._draw_elevated_panel(panel, border_radius=16)

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
        self._draw_button(self._calibrate_confirm_no, "Cancel", accent=True)
        self._draw_button(
            self._calibrate_confirm_yes,
            "Start",
            muted=start_disabled,
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
