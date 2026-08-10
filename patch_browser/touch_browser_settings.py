"""Grouped System settings panel — sections, drill-ins, collapsible Advanced."""

from __future__ import annotations

import pygame

from patch_browser.audio_profile import profile_settings_label
from patch_browser.draw_primitives import draw_chevron
from patch_browser.geometry import Rect
from patch_browser.scroll_widgets import draw_vertical_scroll_edge_hints
from patch_browser.midi_sync_settings import settings_summary_lines
from patch_browser.surge_audio import (
    buffer_option_label,
    current_buffer_size,
    current_sample_rate,
    sample_rate_option_label,
)
from patch_browser.touch_ui_constants import (
    SETTINGS_DRILL_SUBTITLE_GAP,
    SETTINGS_PANEL_FOOTER_H,
    SETTINGS_PANEL_HEADER_H,
    SETTINGS_ROW_GAP,
    SETTINGS_ROW_H,
    SETTINGS_SECTION_GAP,
    SETTINGS_SECTION_HEADER_H,
)
from patch_browser.ui_text import (
    blit_text_block,
    draw_wrapped_text_in_rect,
    normalize_settings_detail,
    settings_detail_height,
    settings_detail_lines,
    wrapped_row_height,
)


class TouchBrowserSettingsMixin:
    """Mixin — layout/draw for grouped System settings (Sound first)."""

    def _reset_settings_navigation(self) -> None:
        self._settings_view = "root"
        self._settings_advanced_open = False

    def _audio_settings_summary_lines(self) -> list[str]:
        return settings_detail_lines(
            profile_settings_label(),
            buffer_option_label(current_buffer_size(), current_sample_rate()),
            sample_rate_option_label(current_sample_rate()),
        )

    def _settings_drill_row_height(self, label: str, subtitle: str | list[str], inner_w: int) -> int:
        label_w = max(1, inner_w - 16 - 34)
        detail_lines = normalize_settings_detail(subtitle)
        label_h = wrapped_row_height(self.font_md, label, label_w)
        sub_h = settings_detail_height(self.font_sm, detail_lines, line_spacing=SETTINGS_DRILL_SUBTITLE_GAP)
        content = 12 + label_h + (SETTINGS_DRILL_SUBTITLE_GAP if detail_lines else 0) + sub_h + 12
        return max(SETTINGS_ROW_H, content)

    def _settings_chevron_row_height(
        self,
        label: str,
        value: str | None,
        inner_w: int,
    ) -> int:
        if not value:
            return self._settings_row_height(label, inner_w)
        return self._settings_drill_row_height(label, value, inner_w)

    def _layout_settings_section_header(
        self,
        pad: int,
        inner_w: int,
        y: int,
        label: str,
        *,
        expandable: bool = False,
    ) -> int:
        rect = Rect(pad, y, inner_w, SETTINGS_SECTION_HEADER_H)
        self._settings_section_headers.append((rect, label, expandable))
        return y + SETTINGS_SECTION_HEADER_H + SETTINGS_SECTION_GAP

    def _layout_settings_root_content(self, pad: int, inner_w: int) -> int:
        y = SETTINGS_ROW_GAP
        self._settings_section_headers = []

        y = self._layout_settings_section_header(pad, inner_w, y, "Sound")
        audio_h = self._settings_drill_row_height("Audio", self._audio_settings_summary_lines(), inner_w)
        self.settings_audio_drill_rect = Rect(pad, y, inner_w, audio_h)
        y += audio_h + SETTINGS_ROW_GAP

        y = self._layout_settings_section_header(pad, inner_w, y, "Display")
        brightness_h = self._settings_chevron_row_height(
            "Brightness",
            f"{self.brightness_percent}%",
            inner_w,
        )
        self.brightness_row_rect = Rect(pad, y, inner_w, brightness_h)
        y += brightness_h + SETTINGS_ROW_GAP
        theme_h = self._settings_row_height("Theme", inner_w)
        self.theme_btn_rect = Rect(pad, y, inner_w, theme_h)
        y += theme_h + SETTINGS_ROW_GAP

        y = self._layout_settings_section_header(pad, inner_w, y, "Network")
        wifi_subtitle = self.wifi_settings_row_label().removeprefix("Wi‑Fi — ")
        wifi_h = self._settings_drill_row_height("Wi‑Fi", wifi_subtitle, inner_w)
        self.wifi_row_rect = Rect(pad, y, inner_w, wifi_h)
        y += wifi_h + SETTINGS_ROW_GAP

        y = self._layout_settings_section_header(pad, inner_w, y, "Looper")
        sync_h = self._settings_drill_row_height("Sync & timing", settings_summary_lines(), inner_w)
        self.looper_sync_row_rect = Rect(pad, y, inner_w, sync_h)
        y += sync_h + SETTINGS_ROW_GAP

        tempo_h = self._settings_row_height("Show tempo badge", inner_w, toggle=True)
        self.looper_hud_toggle_rect = Rect(pad, y, inner_w, tempo_h)
        y += tempo_h + SETTINGS_ROW_GAP

        self.settings_advanced_header_rect = Rect(pad, y, inner_w, SETTINGS_ROW_H)
        y += SETTINGS_ROW_H + SETTINGS_ROW_GAP

        self.cpu_meter_toggle_rect = Rect(pad, y, 0, 0)
        self.norm_global_toggle_rect = Rect(pad, y, 0, 0)
        self._surge_restart_btn = None
        self._calibrate_missing_btn = Rect(pad, y, 0, 0)
        self._calibrate_force_btn = Rect(pad, y, 0, 0)

        if getattr(self, "_settings_advanced_open", False):
            cpu_h = self._settings_row_height("CPU meter", inner_w, toggle=True)
            self.cpu_meter_toggle_rect = Rect(pad, y, inner_w, cpu_h)
            y += cpu_h + SETTINGS_ROW_GAP

            status = self.surge_monitor.get_status_summary()
            if status.get("can_restart"):
                restart_h = self._settings_row_height("Restart Surge", inner_w)
                self._surge_restart_btn = Rect(pad, y, inner_w, restart_h)
                y += restart_h + SETTINGS_ROW_GAP

        self.audio_profile_row_rect = Rect(pad, y, 0, 0)
        self.poly_governor_toggle_rect = Rect(pad, y, 0, 0)
        self.surge_buffer_row_rect = Rect(pad, y, 0, 0)
        self.surge_sample_rate_row_rect = Rect(pad, y, 0, 0)
        return y

    def _layout_settings_audio_content(self, pad: int, inner_w: int) -> int:
        y = SETTINGS_ROW_GAP
        self._settings_section_headers = []
        self.settings_audio_drill_rect = Rect(pad, y, 0, 0)
        self.settings_advanced_header_rect = Rect(pad, y, 0, 0)
        self.brightness_row_rect = Rect(pad, y, 0, 0)
        self.theme_btn_rect = Rect(pad, y, 0, 0)
        self.wifi_row_rect = Rect(pad, y, 0, 0)
        self.cpu_meter_toggle_rect = Rect(pad, y, 0, 0)
        self._surge_restart_btn = None

        from patch_browser.audio_profile import profile_settings_label
        from patch_browser.surge_audio import buffer_settings_label, sample_rate_settings_label

        audio_h = self._settings_chevron_row_height(
            "Audio output",
            profile_settings_label(),
            inner_w,
        )
        self.audio_profile_row_rect = Rect(pad, y, inner_w, audio_h)
        y += audio_h + SETTINGS_ROW_GAP

        buffer_h = self._settings_chevron_row_height(
            "Buffer",
            buffer_settings_label().split(" — ", 1)[-1],
            inner_w,
        )
        self.surge_buffer_row_rect = Rect(pad, y, inner_w, buffer_h)
        y += buffer_h + SETTINGS_ROW_GAP

        rate_h = self._settings_chevron_row_height(
            "Sample rate",
            sample_rate_settings_label().split(" — ", 1)[-1],
            inner_w,
        )
        self.surge_sample_rate_row_rect = Rect(pad, y, inner_w, rate_h)
        y += rate_h + SETTINGS_ROW_GAP

        poly_h = self._settings_row_height("Dynamic voice limit", inner_w, toggle=True)
        self.poly_governor_toggle_rect = Rect(pad, y, inner_w, poly_h)
        y += poly_h + SETTINGS_ROW_GAP

        norm_h = self._settings_row_height("Patch normalization", inner_w, toggle=True)
        self.norm_global_toggle_rect = Rect(pad, y, inner_w, norm_h)
        y += norm_h + SETTINGS_ROW_GAP

        cal_missing_h = self._settings_row_height("Calibrate missing patches", inner_w)
        self._calibrate_missing_btn = Rect(pad, y, inner_w, cal_missing_h)
        y += cal_missing_h + SETTINGS_ROW_GAP

        cal_force_h = self._settings_row_height("Force full re-calibration", inner_w)
        self._calibrate_force_btn = Rect(pad, y, inner_w, cal_force_h)
        y += cal_force_h + SETTINGS_ROW_GAP
        return y

    def _layout_settings_content(self) -> None:
        """Compute scrollable settings rows and fixed footer hit targets (panel-local coords)."""
        pad = 20
        inner_w = self.settings_panel_rect.w - pad * 2

        if getattr(self, "_settings_view", "root") == "audio":
            y = self._layout_settings_audio_content(pad, inner_w)
        else:
            y = self._layout_settings_root_content(pad, inner_w)

        self._settings_content_height = y

        header_bottom = SETTINGS_PANEL_HEADER_H
        footer_top = self.settings_panel_rect.h - SETTINGS_PANEL_FOOTER_H
        scroll_h = max(80, footer_top - header_bottom)
        self._settings_scroll_viewport = Rect(0, header_bottom, self.settings_panel_rect.w, scroll_h)
        self._settings_content_scroll.viewport = Rect(
            self.settings_panel_rect.x,
            self.settings_panel_rect.y + header_bottom,
            self.settings_panel_rect.w,
            scroll_h,
        )
        self._settings_content_scroll.content_height = self._settings_content_height

        self._power_btn = Rect(pad, footer_top + 12, inner_w, SETTINGS_ROW_H)
        self._close_settings_btn = Rect(self.settings_panel_rect.w - 48, 10, 40, 40)
        self._settings_back_btn = Rect(0, 0, 120, SETTINGS_PANEL_HEADER_H)

    def _draw_settings_drill_row(
        self,
        rect: Rect,
        label: str,
        subtitle: str | list[str],
        *,
        muted: bool = False,
        pressed: bool = False,
    ) -> None:
        if pressed:
            bg = self.theme.accent
            text_color = self.theme.bg
            sub_color = self.theme.bg
        else:
            bg = self.theme.surface_alt if muted else self.theme.surface
            text_color = self.theme.muted if muted else self.theme.text
            sub_color = self.theme.muted
        pygame.draw.rect(self.screen, bg, rect.pygame_rect, border_radius=10)
        label_surf = self.font_md.render(label, True, text_color)
        self.screen.blit(label_surf, (rect.x + 16, rect.y + 12))
        detail_lines = normalize_settings_detail(subtitle)
        if detail_lines:
            blit_text_block(
                self.screen,
                self.font_sm,
                detail_lines,
                rect.x + 16,
                rect.y + 12 + label_surf.get_height() + SETTINGS_DRILL_SUBTITLE_GAP,
                sub_color,
                line_spacing=SETTINGS_DRILL_SUBTITLE_GAP,
            )
        chevron_rect = Rect(rect.right - 34, rect.y + (rect.h - 22) // 2, 22, 22)
        draw_chevron(self.screen, chevron_rect, sub_color if pressed else self.theme.muted, direction="right")

    def _draw_settings_section_header(
        self,
        rect: Rect,
        label: str,
        *,
        expandable: bool = False,
        expanded: bool = False,
    ) -> None:
        surf = self.font_sm.render(label.upper(), True, self.theme.muted)
        self.screen.blit(
            surf,
            (rect.x + 4, rect.y + (rect.h - surf.get_height()) // 2),
        )
        if expandable:
            mark = "▾" if expanded else "▸"
            mark_surf = self.font_sm.render(mark, True, self.theme.muted)
            self.screen.blit(
                mark_surf,
                (rect.right - mark_surf.get_width() - 8, rect.y + (rect.h - mark_surf.get_height()) // 2),
            )

    def _draw_settings_chevron_row(
        self,
        rect: Rect,
        label: str,
        value: str | list[str] | None = None,
        *,
        muted: bool = False,
        pressed: bool = False,
    ) -> None:
        if pressed:
            bg = self.theme.accent
            text_color = self.theme.bg
            chevron_color = self.theme.bg
            value_color = self.theme.bg
        else:
            bg = self.theme.surface_alt if muted else self.theme.surface
            text_color = self.theme.muted if muted else self.theme.text
            chevron_color = self.theme.muted
            value_color = self.theme.muted
        pygame.draw.rect(self.screen, bg, rect.pygame_rect, border_radius=10)
        label_surf = self.font_md.render(label, True, text_color)
        detail_lines = normalize_settings_detail(value) if value else []
        if detail_lines:
            self.screen.blit(label_surf, (rect.x + 16, rect.y + 12))
            blit_text_block(
                self.screen,
                self.font_sm,
                detail_lines,
                rect.x + 16,
                rect.y + 12 + label_surf.get_height() + SETTINGS_DRILL_SUBTITLE_GAP,
                value_color,
                line_spacing=SETTINGS_DRILL_SUBTITLE_GAP,
            )
        else:
            self.screen.blit(
                label_surf,
                (rect.x + 16, rect.y + (rect.h - label_surf.get_height()) // 2),
            )
        chevron_rect = Rect(rect.right - 34, rect.y + (rect.h - 22) // 2, 22, 22)
        draw_chevron(self.screen, chevron_rect, chevron_color, direction="right")

    def _draw_settings_expand_row(
        self,
        rect: Rect,
        label: str,
        *,
        expanded: bool,
        pressed: bool = False,
    ) -> None:
        """Full-height tappable row for collapsible sections (e.g. Advanced)."""
        if pressed:
            bg = self.theme.accent
            text_color = self.theme.bg
            mark_color = self.theme.bg
        else:
            bg = self.theme.surface
            text_color = self.theme.text
            mark_color = self.theme.muted
        pygame.draw.rect(self.screen, bg, rect.pygame_rect, border_radius=10)
        label_surf = self.font_md.render(label, True, text_color)
        self.screen.blit(
            label_surf,
            (rect.x + 16, rect.y + (rect.h - label_surf.get_height()) // 2),
        )
        mark = "▾" if expanded else "▸"
        mark_surf = self.font_md.render(mark, True, mark_color)
        self.screen.blit(
            mark_surf,
            (rect.right - mark_surf.get_width() - 16, rect.y + (rect.h - mark_surf.get_height()) // 2),
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

        audio_view = getattr(self, "_settings_view", "root") == "audio"
        title = "Audio" if audio_view else "System"

        scroll_vp = self._settings_scroll_viewport_screen()
        clip = self.screen.get_clip()
        self.screen.set_clip(scroll_vp.pygame_rect)

        service_busy = getattr(self, "_audio_profile_switching", False) or getattr(
            self, "_surge_audio_switching", False
        )

        if audio_view:
            profile_row = self._panel_local_to_screen(self.audio_profile_row_rect, scrolled=True)
            self._draw_settings_chevron_row(
                profile_row,
                "Audio output",
                profile_settings_label(),
                muted=service_busy,
                pressed=self._pressed("settings:audio_profile"),
            )

            from patch_browser.surge_audio import buffer_settings_label, sample_rate_settings_label

            buffer_row = self._panel_local_to_screen(self.surge_buffer_row_rect, scrolled=True)
            self._draw_settings_chevron_row(
                buffer_row,
                "Buffer",
                buffer_settings_label().split(" — ", 1)[-1],
                muted=service_busy,
                pressed=self._pressed("settings:surge_buffer"),
            )

            rate_row = self._panel_local_to_screen(self.surge_sample_rate_row_rect, scrolled=True)
            self._draw_settings_chevron_row(
                rate_row,
                "Sample rate",
                sample_rate_settings_label().split(" — ", 1)[-1],
                muted=service_busy,
                pressed=self._pressed("settings:surge_sample_rate"),
            )

            poly_toggle = self._panel_local_to_screen(self.poly_governor_toggle_rect, scrolled=True)
            self._draw_normalize_toggle(
                poly_toggle,
                self.poly_governor_enabled,
                has_gain=True,
                label="Dynamic voice limit",
            )

            norm_toggle = self._panel_local_to_screen(self.norm_global_toggle_rect, scrolled=True)
            self._draw_normalize_toggle(
                norm_toggle,
                self.loader.normalization.is_globally_enabled(),
                has_gain=True,
                label="Patch normalization",
            )

            cal_missing = self._panel_local_to_screen(self._calibrate_missing_btn, scrolled=True)
            self._draw_settings_action_row(
                cal_missing,
                "Calibrate missing patches",
                pressed=self._pressed("settings:cal_missing"),
            )

            cal_force = self._panel_local_to_screen(self._calibrate_force_btn, scrolled=True)
            self._draw_settings_action_row(
                cal_force,
                "Force full re-calibration",
                muted=True,
                pressed=self._pressed("settings:cal_force"),
            )
        else:
            for section_rect, label, expandable in getattr(self, "_settings_section_headers", []):
                screen_rect = self._panel_local_to_screen(section_rect, scrolled=True)
                self._draw_settings_section_header(
                    screen_rect,
                    label,
                    expandable=expandable,
                    expanded=False,
                )

            audio_row = self._panel_local_to_screen(self.settings_audio_drill_rect, scrolled=True)
            self._draw_settings_drill_row(
                audio_row,
                "Audio",
                self._audio_settings_summary_lines(),
                pressed=self._pressed("settings:audio_drill"),
            )

            brightness_row = self._panel_local_to_screen(self.brightness_row_rect, scrolled=True)
            self._draw_settings_chevron_row(
                brightness_row,
                "Brightness",
                f"{self.brightness_percent}%",
                pressed=self._pressed("settings:brightness"),
            )

            theme_row = self._panel_local_to_screen(self.theme_btn_rect, scrolled=True)
            self._draw_settings_chevron_row(
                theme_row,
                "Theme",
                pressed=self._pressed("settings:theme"),
            )

            wifi_row = self._panel_local_to_screen(self.wifi_row_rect, scrolled=True)
            self._draw_settings_drill_row(
                wifi_row,
                "Wi‑Fi",
                self.wifi_settings_row_label().removeprefix("Wi‑Fi — "),
                muted=getattr(self, "_wifi_busy", False),
                pressed=self._pressed("settings:wifi"),
            )

            looper_sync_row = self._panel_local_to_screen(self.looper_sync_row_rect, scrolled=True)
            self._draw_settings_drill_row(
                looper_sync_row,
                "Sync & timing",
                settings_summary_lines(),
                pressed=self._pressed("settings:looper_sync"),
            )

            looper_toggle = self._panel_local_to_screen(self.looper_hud_toggle_rect, scrolled=True)
            self._draw_normalize_toggle(
                looper_toggle,
                getattr(self, "show_looper_hud", True),
                has_gain=True,
                label="Show tempo badge",
            )

            advanced_row = self._panel_local_to_screen(
                self.settings_advanced_header_rect,
                scrolled=True,
            )
            self._draw_settings_expand_row(
                advanced_row,
                "Advanced",
                expanded=getattr(self, "_settings_advanced_open", False),
                pressed=self._pressed("settings:advanced_toggle"),
            )

            if getattr(self, "_settings_advanced_open", False):
                cpu_toggle = self._panel_local_to_screen(self.cpu_meter_toggle_rect, scrolled=True)
                if cpu_toggle.h > 0:
                    self._draw_normalize_toggle(
                        cpu_toggle,
                        self.show_cpu_meter,
                        has_gain=True,
                        label="CPU meter",
                    )

                if self._surge_restart_btn and self._surge_restart_btn.h > 0:
                    restart = self._panel_local_to_screen(self._surge_restart_btn, scrolled=True)
                    self._draw_settings_action_row(
                        restart,
                        "Restart Surge",
                        pressed=self._pressed("settings:surge_restart"),
                    )

        self.screen.set_clip(clip)

        draw_vertical_scroll_edge_hints(
            self.screen,
            scroll_vp,
            self._settings_content_scroll,
            self.theme,
        )

        pygame.draw.rect(self.screen, self.theme.surface, header_rect.pygame_rect)
        self._draw_divider_line(
            header_rect.x + 16,
            header_rect.bottom - 1,
            header_rect.right - 16,
        )
        title_x = panel.x + 20
        if audio_view:
            back_screen = self._panel_local_to_screen(self._settings_back_btn)
            if self._pressed("settings:settings_back"):
                pygame.draw.rect(
                    self.screen,
                    self.theme.accent,
                    back_screen.pygame_rect,
                    border_radius=8,
                )
            chevron_rect = Rect(
                back_screen.x + 10,
                back_screen.y + (back_screen.h - 24) // 2,
                24,
                24,
            )
            draw_chevron(self.screen, chevron_rect, self.theme.text, direction="left")
            title_x = back_screen.x + 38
        self.screen.blit(self.font_md.render(title, True, self.theme.text), (title_x, panel.y + 16))
        close_screen = self._panel_local_to_screen(self._close_settings_btn)
        self._draw_icon_button(
            close_screen,
            "×",
            muted=True,
            pressed=self._pressed("settings:close"),
        )

        footer_y = panel.y + self.settings_panel_rect.h - SETTINGS_PANEL_FOOTER_H
        self._draw_divider_line(panel.x + 16, footer_y, panel.right - 16)
        power = self._panel_local_to_screen(self._power_btn)
        power_pressed = self._pressed("settings:power")
        power_bg = self.theme.accent if power_pressed else self.theme.surface_alt
        power_text = self.theme.bg if power_pressed else self._semantic_color("danger")
        pygame.draw.rect(self.screen, power_bg, power.pygame_rect, border_radius=10)
        draw_wrapped_text_in_rect(
            self.screen,
            self.font_md,
            "Power…",
            power.x,
            power.y,
            power.w,
            power.h,
            power_text,
            pad_x=16,
            max_lines=1,
        )
