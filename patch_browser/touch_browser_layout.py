"""Touch patch browser — layout mixin."""

from __future__ import annotations

from patch_browser.geometry import Rect
from patch_browser.mixer import MixerChannel
from patch_browser.scroll_widgets import ScrollList
from patch_browser.touch_ui_constants import (
    CPU_METER_H,
    CPU_METER_W,
    FADER_COLUMN_W,
    FADER_TRACK_H,
    FADER_TRACK_W,
    LEFT_NAV_COLLAPSED_WIDTH,
    LEFT_NAV_WIDTH,
    NAV_FOLDER_TITLE_H,
    NORM_CHECKBOX_SIZE,
    NORM_ROW_H,
    NORM_ROW_W,
    SETTINGS_PANEL_ANIM_SPEED,
    SETTINGS_PANEL_FOOTER_H,
    SETTINGS_PANEL_HEADER_H,
    SETTINGS_PANEL_W,
    SETTINGS_ROW_GAP,
    SETTINGS_ROW_H,
    VOLUME_MAX,
    VOLUME_MIN,
)
from patch_browser.touch_ui_enums import LeftNavMode, Screen, audio_profile_display
from patch_browser.ui_text import text_block_height, wrap_text_lines, wrapped_row_height


class TouchBrowserLayoutMixin:
    """Mixin — expects TouchPatchBrowser host attributes."""

    def _left_nav_width(self) -> int:
        return LEFT_NAV_COLLAPSED_WIDTH if self.left_nav_collapsed else LEFT_NAV_WIDTH
    def _layout(self) -> None:
        margin = 16
        gap = 10
        status_h = 44
        nav_header_h = 36
        footer_h = 22

        self.status_rect = Rect(margin, margin, self.width - margin * 2, status_h)
        self.system_settings_btn = Rect(self.status_rect.right - 44, self.status_rect.y + 6, 36, 32)
        cpu_left = self.system_settings_btn.x - CPU_METER_W - 8
        if self.show_cpu_meter:
            self.cpu_meter_rect = Rect(
                cpu_left,
                self.status_rect.y + 6,
                CPU_METER_W,
                CPU_METER_H,
            )
        else:
            self.cpu_meter_rect = Rect(cpu_left, self.status_rect.y + 6, 0, 0)

        content_top = self.status_rect.y + self.status_rect.h + gap
        content_bottom = self.height - footer_h - margin
        left_w = self._left_nav_width()

        self.left_panel_rect = Rect(margin, content_top, left_w, content_bottom - content_top)
        self.nav_toggle_btn = Rect(margin, content_top, left_w, content_bottom - content_top)
        self.nav_header_rect = Rect(margin, content_top, LEFT_NAV_WIDTH, nav_header_h)
        self._update_nav_list_geometry(content_top, content_bottom, nav_header_h, margin)

        main_x = margin + left_w + gap
        main_w = self.width - margin * 2 - left_w - gap
        self.main_rect = Rect(main_x, content_top, main_w, content_bottom - content_top)
        self._layout_mixer_strip()
        bottom_row_y = self.main_rect.bottom - 52
        self.favorites_btn = Rect(
            self.main_rect.right - 56,
            bottom_row_y,
            40,
            40,
        )
        self.normalize_btn = Rect(
            self.favorites_btn.x - NORM_ROW_W - 10,
            bottom_row_y,
            NORM_ROW_W,
            NORM_ROW_H,
        )

        self._layout_nav_buttons()

        panel_w = min(SETTINGS_PANEL_W, self.width - margin)
        self.settings_panel_rect = Rect(self.width - panel_w, margin, panel_w, self.height - margin * 2)
        self._layout_settings_content()
    def _settings_panel_x(self) -> int:
        """Animated X offset — panel slides in from the right edge."""
        hidden_x = self.width
        shown_x = self.settings_panel_rect.x
        return int(hidden_x + (shown_x - hidden_x) * self._settings_slide)
    def _settings_toggle_label_width(self, inner_w: int) -> int:
        return max(1, inner_w - 12 - NORM_CHECKBOX_SIZE - 16)
    def _settings_action_label_width(self, inner_w: int) -> int:
        return max(1, inner_w - 32)
    def _settings_row_height(self, label: str, inner_w: int, *, toggle: bool = False) -> int:
        max_w = (
            self._settings_toggle_label_width(inner_w)
            if toggle
            else self._settings_action_label_width(inner_w)
        )
        return wrapped_row_height(self.font_md, label, max_w)
    def _layout_settings_content(self) -> None:
        """Compute scrollable settings rows and fixed footer hit targets (panel-local coords)."""
        pad = 20
        inner_w = self.settings_panel_rect.w - pad * 2
        y = 0

        self.brightness_slider_rect = Rect(pad, y + 28, inner_w, 36)
        y += 78

        cpu_h = self._settings_row_height("CPU meter", inner_w, toggle=True)
        self.cpu_meter_toggle_rect = Rect(pad, y, inner_w, cpu_h)
        y += cpu_h + SETTINGS_ROW_GAP

        oled_h = self._settings_row_height("Theme…", inner_w)
        self.theme_btn_rect = Rect(pad, y, inner_w, oled_h)
        y += oled_h + SETTINGS_ROW_GAP

        norm_h = self._settings_row_height("Patch normalization", inner_w, toggle=True)
        self.norm_global_toggle_rect = Rect(pad, y, inner_w, norm_h)
        y += norm_h + SETTINGS_ROW_GAP

        audio_profile_lines = wrap_text_lines(
            self.font_sm,
            audio_profile_display(),
            inner_w - 8,
            max_lines=1,
        )
        self._settings_audio_profile_y = y
        y += text_block_height(self.font_sm, len(audio_profile_lines), line_spacing=2) + 8

        status = self.surge_monitor.get_status_summary()
        self._surge_restart_btn = None
        if status.get("can_restart"):
            restart_h = self._settings_row_height("Restart Surge", inner_w)
            self._surge_restart_btn = Rect(pad, y, inner_w, restart_h)
            y += restart_h + SETTINGS_ROW_GAP

        cal_missing_h = self._settings_row_height("Calibrate missing patches", inner_w)
        self._calibrate_missing_btn = Rect(pad, y, inner_w, cal_missing_h)
        y += cal_missing_h + SETTINGS_ROW_GAP
        cal_force_h = self._settings_row_height("Force full re-calibration", inner_w)
        self._calibrate_force_btn = Rect(pad, y, inner_w, cal_force_h)
        y += cal_force_h + SETTINGS_ROW_GAP

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
    def _panel_local_to_screen(self, rect: Rect, *, scrolled: bool = False) -> Rect:
        px = self._settings_panel_x()
        py = self.settings_panel_rect.y
        scroll = int(self._settings_content_scroll.scroll_pixels) if scrolled else 0
        return Rect(rect.x + px, rect.y + py - scroll, rect.w, rect.h)
    def _settings_scroll_viewport_screen(self) -> Rect:
        px = self._settings_panel_x()
        vp = self._settings_scroll_viewport
        return Rect(vp.x + px, vp.y + self.settings_panel_rect.y, vp.w, vp.h)
    def _settings_panel_screen_rect(self) -> Rect:
        return Rect(
            self._settings_panel_x(),
            self.settings_panel_rect.y,
            self.settings_panel_rect.w,
            self.settings_panel_rect.h,
        )
    def _settings_panel_contains(self, pos: tuple[int, int]) -> bool:
        return self._settings_panel_screen_rect().contains(*pos)
    def _sync_settings_scroll_viewport(self) -> None:
        self._settings_content_scroll.viewport = self._settings_scroll_viewport_screen()
    def _open_settings_panel(self) -> None:
        self._layout_settings_content()
        self._settings_content_scroll.reset()
        self._sync_settings_scroll_viewport()
        self.screen_state = Screen.SETTINGS
    def _close_settings_panel(self) -> None:
        self.screen_state = Screen.BROWSER
    def _tick_settings_animation(self, dt: float) -> None:
        target = 1.0 if self.screen_state == Screen.SETTINGS else 0.0
        if abs(self._settings_slide - target) < 0.004:
            self._settings_slide = target
        else:
            step = min(1.0, SETTINGS_PANEL_ANIM_SPEED * dt)
            self._settings_slide += (target - self._settings_slide) * step
        if self._settings_slide > 0.004 or self.screen_state == Screen.SETTINGS:
            self._sync_settings_scroll_viewport()
    def _update_nav_list_geometry(
        self,
        content_top: int | None = None,
        content_bottom: int | None = None,
        nav_header_h: int = 36,
        margin: int = 16,
    ) -> None:
        if content_top is None:
            gap = 10
            footer_h = 22
            content_top = self.status_rect.y + self.status_rect.h + gap
            content_bottom = self.height - footer_h - margin

        show_folder_title = (
            not self.left_nav_collapsed and self.left_nav_mode == LeftNavMode.PATCHES
        )
        folder_title_h = NAV_FOLDER_TITLE_H if show_folder_title else 0
        list_top = content_top + nav_header_h + 4 + folder_title_h

        if show_folder_title:
            self.nav_folder_title_rect = Rect(
                margin,
                content_top + nav_header_h + 4,
                LEFT_NAV_WIDTH,
                folder_title_h,
            )
        else:
            self.nav_folder_title_rect = None

        list_rect = Rect(margin, list_top, LEFT_NAV_WIDTH, content_bottom - list_top)
        row_height = 50 if self.left_nav_mode == LeftNavMode.PATCHES else 44
        if not hasattr(self, "nav_list"):
            self.nav_list = ScrollList(list_rect, row_height=row_height)
        else:
            self.nav_list.rect = list_rect
            self.nav_list.row_height = row_height
            self.nav_list._clamp_scroll()
    def _layout_nav_buttons(self) -> None:
        y = self.nav_header_rect.y + 4
        x = self.nav_header_rect.x + 6
        self.nav_back_btn = Rect(x, y, 36, 28)
        x += 42
        self.nav_collapse_btn = Rect(self.nav_header_rect.right - 38, y, 32, 28)
        self.nav_current_btn = Rect(x, y, 72, 28)
    def _mixer_channel_defs(self) -> list[dict]:
        return [
            {"id": "volume", "label": "Vol", "min": VOLUME_MIN, "max": VOLUME_MAX, "enabled": True},
        ]
    def _layout_mixer_strip(self) -> None:
        defs = self._mixer_channel_defs()
        count = len(defs)
        strip_w = count * FADER_COLUMN_W
        strip_x = self.main_rect.x + max(16, (self.main_rect.w - strip_w) // 2)
        strip_top = self.main_rect.y + 96

        self.mixer_channels = []
        for i, spec in enumerate(defs):
            col_x = strip_x + i * FADER_COLUMN_W
            track_x = col_x + (FADER_COLUMN_W - FADER_TRACK_W) // 2
            column_rect = Rect(col_x, strip_top, FADER_COLUMN_W, FADER_TRACK_H + 44)
            track_rect = Rect(track_x, strip_top, FADER_TRACK_W, FADER_TRACK_H)
            self.mixer_channels.append(
                MixerChannel(
                    channel_id=spec["id"],
                    label=spec["label"],
                    min_value=spec["min"],
                    max_value=spec["max"],
                    enabled=spec["enabled"],
                    column_rect=column_rect,
                    track_rect=track_rect,
                )
            )
    def _relayout(self) -> None:
        self._layout()
        self._refresh_lists()
