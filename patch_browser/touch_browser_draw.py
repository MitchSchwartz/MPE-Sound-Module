"""Touch patch browser — draw mixin."""

from __future__ import annotations

import time

import pygame

from patch_browser.draw_primitives import draw_chevron, draw_sidebar_panel_icon
from patch_browser.geometry import Rect
from patch_browser.mixer import MixerChannel
from patch_browser.touch_ui_constants import (
    CPU_METER_BAR_H,
    FADER_HANDLE_H,
    FADER_HANDLE_W,
    SETTINGS_PANEL_FOOTER_H,
    SETTINGS_PANEL_HEADER_H,
    SETTINGS_ROW_GAP,
    SETTINGS_ROW_H,
)
from patch_browser.touch_ui_enums import (
    CalibrateMode,
    LeftNavMode,
    audio_profile_display,
)
from patch_browser.ui_text import (
    blit_text_block,
    draw_wrapped_text_in_rect,
    ellipsize_text,
    text_block_height,
    wrap_text_lines,
)
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
    ) -> None:
        if muted:
            color = self.theme.surface
        elif accent:
            color = self.theme.accent
        else:
            color = self.theme.surface_alt
        pygame.draw.rect(self.screen, color, rect.pygame_rect, border_radius=8)
        icon_color = self.theme.bg if accent else self.theme.text
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
    def _draw_nav_header(self) -> None:
        pygame.draw.rect(self.screen, self.theme.surface, self.nav_header_rect.pygame_rect)

        if self.left_nav_mode in (LeftNavMode.PATCHES, LeftNavMode.ALL_PATCHES):
            self._draw_icon_button(self.nav_back_btn, "back", muted=True)
        if self.left_nav_mode == LeftNavMode.FOLDERS:
            self._draw_button(self.nav_all_btn, "All", small=True, accent=True)
        if self._show_current_folder_button():
            self._draw_button(self.nav_current_btn, "Current", small=True, accent=True)
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
        folder_name = self._browse_category_name()
        if self.left_nav_mode == LeftNavMode.ALL_PATCHES:
            count = len(self.all_patches_flat)
            folder_name = f"All patches ({count})"
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
        danger: bool = False,
    ) -> None:
        """Modal/action button. accent=primary commit; danger=destructive; default=dismiss/secondary."""
        if muted:
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
    def _draw_all_patches_list(self) -> None:
        pygame.draw.rect(self.screen, self.theme.surface, self.nav_list.rect.pygame_rect, border_radius=10)
        clip = self.screen.get_clip()
        self.screen.set_clip(self.nav_list.rect.pygame_rect)

        patches = self.all_patches_flat
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
                row_h - 4,
            )
            is_highlight = self.nav_list.highlight_index == index
            is_loaded = self.nav_list.loaded_marker_index == index
            if is_highlight or is_loaded:
                pygame.draw.rect(self.screen, self.theme.surface_alt, row_rect, border_radius=8)

            heart = "♥" if self._patch_is_favorited(patch) else "♡"
            heart_color = self.theme.danger if self._patch_is_favorited(patch) else self.theme.muted
            heart_s = self.font_sm.render(heart, True, heart_color)
            self.screen.blit(heart_s, (row_rect.x + 6, row_rect.y + 6))

            name_max_w = max(1, row_rect.w - 34)
            name_clipped = ellipsize_text(self.font_md, patch["name"], name_max_w)
            name_color = self.theme.text if is_highlight or is_loaded else self.theme.muted
            name_s = self.font_md.render(name_clipped, True, name_color)
            self.screen.blit(name_s, (row_rect.x + 26, row_rect.y + 4))

            folder_clipped = ellipsize_text(
                self.font_sm,
                patch.get("category", ""),
                name_max_w,
            )
            folder_s = self.font_sm.render(folder_clipped, True, self.theme.muted)
            self.screen.blit(folder_s, (row_rect.x + 26, row_rect.y + 22))

            if is_loaded:
                pygame.draw.circle(
                    self.screen,
                    self.theme.playing,
                    (row_rect.right - 12, row_rect.centery),
                    4,
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
        for letter, rect in self.az_rail_letter_rects:
            label = "#" if letter == "#" else letter
            text = self.font_sm.render(label, True, self.theme.muted)
            tx = rect.x + (rect.w - text.get_width()) // 2
            ty = rect.y + max(0, (rect.h - text.get_height()) // 2)
            self.screen.blit(text, (tx, ty))

    def _draw_left_nav_expanded(self) -> None:
        pygame.draw.rect(self.screen, self.theme.surface, self.left_panel_rect.pygame_rect, border_radius=10)

        self._draw_nav_header()
        self._draw_folder_title_bar()

        if self.left_nav_mode == LeftNavMode.ALL_PATCHES:
            self._draw_all_patches_list()
            self._draw_az_rail()
        else:
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

        if self.left_nav_mode != LeftNavMode.ALL_PATCHES:
            self._draw_main_detail()

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

        theme_row = self._panel_local_to_screen(self.theme_btn_rect, scrolled=True)
        self._draw_settings_action_row(theme_row, "Theme…")

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
            pygame.draw.rect(self.screen, self.theme.surface_alt, rect.pygame_rect, border_radius=10)
            label_color = self.theme.text if option == "Cancel" else self.theme.danger
            self.screen.blit(
                self.font_md.render(option, True, label_color),
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
        self.screen.blit(
            self.font_md.render(action, True, self._semantic_color("danger")),
            (panel.x + 24, panel.y + 24),
        )

        self._confirm_no = Rect(panel.x + 24, panel.y + 100, (panel.w - 60) // 2, 52)
        self._confirm_yes = Rect(self._confirm_no.x + self._confirm_no.w + 12, panel.y + 100, (panel.w - 60) // 2, 52)
        self._draw_button(self._confirm_no, "Cancel")
        self._draw_button(self._confirm_yes, "Confirm", danger=True)

    def _draw_theme_section_label(self, x: int, y: int, label: str) -> None:
        self.screen.blit(self.font_sm.render(label, True, self.theme.muted), (x, y))

    def _draw_theme_choice(
        self,
        rect: Rect,
        label: str,
        *,
        selected: bool,
    ) -> None:
        bg = self.theme.surface_alt
        pygame.draw.rect(self.screen, bg, rect.pygame_rect, border_radius=10)
        if selected:
            pygame.draw.rect(self.screen, self.theme.accent, rect.pygame_rect, width=2, border_radius=10)
        label_color = self.theme.accent if selected else self.theme.text
        self.screen.blit(
            self.font_sm.render(label, True, label_color),
            (rect.x + 12, rect.y + (rect.h - self.font_sm.get_height()) // 2),
        )

    def _draw_theme_swatch(self, rect: Rect, rgb: tuple[int, int, int], *, selected: bool) -> None:
        pygame.draw.rect(self.screen, rgb, rect.pygame_rect, border_radius=8)
        if selected:
            pygame.draw.rect(self.screen, self.theme.text, rect.pygame_rect, width=2, border_radius=8)

    def _draw_theme_add_swatch(self, rect: Rect) -> None:
        pygame.draw.rect(self.screen, self.theme.surface_alt, rect.pygame_rect, border_radius=8)
        pygame.draw.rect(self.screen, self.theme.muted, rect.pygame_rect, width=1, border_radius=8)
        label = self.font_md.render("+", True, self.theme.text)
        self.screen.blit(
            label,
            (rect.x + (rect.w - label.get_width()) // 2, rect.y + (rect.h - label.get_height()) // 2),
        )

    def _layout_theme_swatches(
        self,
        *,
        inner_x: int,
        inner_w: int,
        y: int,
        entries: list[tuple[str, tuple[int, int, int], bool]],
        draft_rgb: tuple[int, int, int],
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
            row_x = inner_x + max(0, (inner_w - total_row_w) // 2)
            rect = Rect(
                row_x + col * (swatch_size + swatch_gap),
                y + row * (swatch_size + swatch_gap),
                swatch_size,
                swatch_size,
            )
            swatch_rects.append((rect, rgb, hit_id))
            selected = draft_rgb == rgb
            if hit_id == "custom_new":
                self._draw_theme_add_swatch(rect)
            else:
                self._draw_theme_swatch(rect, rgb, selected=selected)
            if deletable:
                delete_rect = Rect(rect.right - delete_size + 4, rect.y - 4, delete_size, delete_size)
                delete_rects.append((delete_rect, hit_id.removeprefix("custom:")))
                pygame.draw.rect(self.screen, self.theme.surface, delete_rect.pygame_rect, border_radius=9)
                pygame.draw.rect(self.screen, self.theme.muted, delete_rect.pygame_rect, width=1, border_radius=9)
                cross = self.font_sm.render("×", True, self.theme.text)
                self.screen.blit(
                    cross,
                    (
                        delete_rect.x + (delete_rect.w - cross.get_width()) // 2,
                        delete_rect.y + (delete_rect.h - cross.get_height()) // 2 - 1,
                    ),
                )
        rows = (len(entries) + cols - 1) // cols if entries else 0
        next_y = y + rows * swatch_size + max(0, rows - 1) * swatch_gap
        return next_y, swatch_rects, delete_rects

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
        )
        self._draw_theme_choice(
            self._theme_base_option_rects[1],
            "OLED dark",
            selected=draft.theme_mode == THEME_MODE_OLED_BLACK,
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
        )
        self._draw_theme_choice(
            self._theme_style_option_rects[1],
            "Minimal accent",
            selected=draft.accent_style == ACCENT_STYLE_MINIMAL,
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
        self._draw_button(self._theme_choose_color_btn, "Choose color…")

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
        self._draw_button(self._theme_cancel_rect, "Cancel")
        self._draw_button(self._theme_done_rect, "Done", accent=True)

    def _draw_theme_colors_panel(self, panel: Rect) -> None:
        draft = self._theme_draft()
        inner_x = panel.x + 24
        inner_w = panel.w - 48
        y = panel.y + 56

        self._draw_theme_section_label(inner_x, y, "Presets")
        y += self.font_sm.get_height() + 8
        preset_entries = [(f"preset:{i}", rgb, False) for i, (_name, rgb) in enumerate(ACCENT_PRESETS)]
        y, preset_rects, _preset_delete = self._layout_theme_swatches(
            inner_x=inner_x,
            inner_w=inner_w,
            y=y,
            entries=preset_entries,
            draft_rgb=draft.accent_rgb,
        )
        y += 12

        custom_colors = getattr(self, "_custom_accent_colors", [])
        if custom_colors:
            self._draw_theme_section_label(inner_x, y, "Saved")
            y += self.font_sm.get_height() + 8
            saved_entries = [(f"custom:{color.color_id}", color.rgb, True) for color in custom_colors]
            y, saved_rects, saved_delete = self._layout_theme_swatches(
                inner_x=inner_x,
                inner_w=inner_w,
                y=y,
                entries=saved_entries,
                draft_rgb=draft.accent_rgb,
            )
        else:
            saved_rects = []
            saved_delete = []

        y += 8
        self._draw_theme_section_label(inner_x, y, "Custom")
        y += self.font_sm.get_height() + 8
        y, custom_rects, custom_delete = self._layout_theme_swatches(
            inner_x=inner_x,
            inner_w=inner_w,
            y=y,
            entries=[("custom_new", draft.accent_rgb, False)],
            draft_rgb=draft.accent_rgb,
            cols=1,
        )

        self._theme_color_swatch_rects = preset_rects + saved_rects + custom_rects
        self._theme_color_delete_rects = saved_delete + custom_delete
        self._theme_base_option_rects = []
        self._theme_style_option_rects = []

        back_h = 52
        self._theme_colors_back_rect = Rect(inner_x, panel.bottom - back_h - 20, inner_w, back_h)
        self._draw_button(self._theme_colors_back_rect, "Back")
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
        self._draw_button(self._picker_back_rect, "Back")
        self._draw_button(self._picker_save_rect, "Save", accent=True)
        can_delete = getattr(self, "_picker_editing_id", None) is not None
        self._draw_button(self._picker_delete_rect, "Delete", danger=can_delete, muted=not can_delete)

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
        self._draw_elevated_panel(panel, border_radius=16)

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
        self._draw_button(self._calibrate_confirm_no, "Cancel")
        self._draw_button(
            self._calibrate_confirm_yes,
            "Start",
            accent=not start_disabled,
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
