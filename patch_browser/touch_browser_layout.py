"""Touch patch browser — layout mixin."""

from __future__ import annotations

from patch_browser.geometry import Rect
from patch_browser.mixer import MixerChannel
from patch_browser.scroll_widgets import ScrollList
from patch_browser.touch_ui_constants import (
    ALL_PATCHES_ROW_HEIGHT,
    AUDIO_BADGE_PAD_X,
    AZ_RAIL_WIDTH,
    BROWSER_BOTTOM_MARGIN,
    CPU_METER_BAR_W,
    CPU_METER_LABEL_GAP,
    DETAIL_HEADER_MIN_H,
    DETAIL_MIXER_GAP,
    DETAIL_TITLE_GAP,
    DETAIL_TITLE_PAD_TOP,
    DETAIL_TITLE_PAD_X,
    FADER_COLUMN_W,
    FADER_TRACK_H,
    FADER_TRACK_W,
    FAVORITES_BTN_SIZE,
    LEFT_NAV_COLLAPSED_WIDTH,
    LEFT_NAV_WIDTH,
    MIXER_BOTTOM_GAP,
    MIXER_LABEL_H,
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
    STATUS_BAR_ITEM_GAP,
    VOLUME_MAX,
    VOLUME_MIN,
)
from patch_browser.audio_profile import header_badge_label
from patch_browser.surge_output_limiter import LIM_LABEL, limiter_active
from patch_browser.all_patches_index import AZ_RAIL_LETTERS
from patch_browser.touch_ui_enums import LeftNavMode, Screen, audio_profile_display
from patch_browser.ui_text import text_block_height, wrap_text_lines, wrapped_row_height


class TouchBrowserLayoutMixin:
    """Mixin — expects TouchPatchBrowser host attributes."""

    def _left_nav_width(self) -> int:
        if self.left_nav_mode == LeftNavMode.ALL_PATCHES:
            margin = 16
            gap = 10
            return self.width - margin * 2 - AZ_RAIL_WIDTH - gap
        return LEFT_NAV_COLLAPSED_WIDTH if self.left_nav_collapsed else LEFT_NAV_WIDTH

    def _cpu_meter_text_size(self) -> tuple[int, int]:
        return self.font_sm.size("CPU")

    def _cpu_meter_width(self) -> int:
        label_w, _label_h = self._cpu_meter_text_size()
        return label_w + CPU_METER_LABEL_GAP + CPU_METER_BAR_W

    def _cpu_meter_height(self) -> int:
        _label_w, label_h = self._cpu_meter_text_size()
        return label_h

    def _audio_badge_width(self) -> int:
        label_w = self.font_sm.size(header_badge_label())[0]
        return label_w + AUDIO_BADGE_PAD_X * 2

    def _limiter_badge_width(self) -> int:
        if not limiter_active():
            return 0
        label_w = self.font_sm.size(LIM_LABEL)[0]
        return label_w + AUDIO_BADGE_PAD_X * 2

    def _layout(self) -> None:
        margin = 16
        gap = 10
        status_h = 44
        nav_header_h = 36

        self.status_rect = Rect(margin, margin, self.width - margin * 2, status_h)
        self.status_title_x = self.status_rect.x + 12
        self.system_settings_btn = Rect(self.status_rect.right - 44, self.status_rect.y + 6, 36, 32)
        right_cursor = self.system_settings_btn.x - STATUS_BAR_ITEM_GAP
        if self.show_cpu_meter:
            meter_w = self._cpu_meter_width()
            right_cursor -= meter_w
            meter_h = self._cpu_meter_height()
            self.cpu_meter_rect = Rect(
                right_cursor,
                self.status_rect.y + (status_h - meter_h) // 2,
                meter_w,
                meter_h,
            )
            right_cursor -= STATUS_BAR_ITEM_GAP
        else:
            self.cpu_meter_rect = Rect(right_cursor, self.status_rect.y + 6, 0, 0)
        audio_badge_w = self._audio_badge_width()
        right_cursor -= audio_badge_w
        self.audio_profile_badge_rect = Rect(
            right_cursor,
            self.status_rect.y + 10,
            audio_badge_w,
            24,
        )
        limiter_badge_w = self._limiter_badge_width()
        if limiter_badge_w:
            right_cursor -= limiter_badge_w
            self.limiter_badge_rect = Rect(
                right_cursor,
                self.status_rect.y + 10,
                limiter_badge_w,
                24,
            )
            right_cursor -= STATUS_BAR_ITEM_GAP
        else:
            self.limiter_badge_rect = Rect(right_cursor, self.status_rect.y + 10, 0, 0)

        content_top = self.status_rect.y + self.status_rect.h + gap
        content_bottom = self.height - BROWSER_BOTTOM_MARGIN
        left_w = self._left_nav_width()

        self.left_panel_rect = Rect(margin, content_top, left_w, content_bottom - content_top)
        self.nav_toggle_btn = Rect(margin, content_top, left_w, content_bottom - content_top)
        nav_header_w = left_w if self.left_nav_mode == LeftNavMode.ALL_PATCHES else LEFT_NAV_WIDTH
        self.nav_header_rect = Rect(margin, content_top, nav_header_w, nav_header_h)
        self._update_nav_list_geometry(content_top, content_bottom, nav_header_h, margin)

        if self.left_nav_mode == LeftNavMode.ALL_PATCHES:
            self.az_rail_rect = Rect(
                margin + left_w + gap,
                content_top,
                AZ_RAIL_WIDTH,
                content_bottom - content_top,
            )
            self._layout_az_rail_letters()
            self.main_rect = Rect(margin + left_w + gap + AZ_RAIL_WIDTH, content_top, 0, 0)
        else:
            self.az_rail_rect = Rect(0, 0, 0, 0)
            self.az_rail_letter_rects = []
            main_x = margin + left_w + gap
            main_w = self.width - margin * 2 - left_w - gap
            self.main_rect = Rect(main_x, content_top, main_w, content_bottom - content_top)
        action_row_h = max(NORM_ROW_H, FAVORITES_BTN_SIZE)
        bottom_row_y = self._detail_bottom_row_y()
        self._layout_mixer_strip()
        self.favorites_btn = Rect(
            self.main_rect.right - FAVORITES_BTN_SIZE - 8,
            bottom_row_y + (action_row_h - FAVORITES_BTN_SIZE) // 2,
            FAVORITES_BTN_SIZE,
            FAVORITES_BTN_SIZE,
        )
        self.normalize_btn = Rect(
            self.favorites_btn.x - NORM_ROW_W - 10,
            bottom_row_y + (action_row_h - NORM_ROW_H) // 2,
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

        poly_h = self._settings_row_height("Dynamic voice limit", inner_w, toggle=True)
        self.poly_governor_toggle_rect = Rect(pad, y, inner_w, poly_h)
        y += poly_h + SETTINGS_ROW_GAP

        limiter_h = self._settings_row_height("Output limiter", inner_w, toggle=True)
        self.output_limiter_toggle_rect = Rect(pad, y, inner_w, limiter_h)
        y += limiter_h + SETTINGS_ROW_GAP

        oled_h = self._settings_row_height("Theme…", inner_w)
        self.theme_btn_rect = Rect(pad, y, inner_w, oled_h)
        y += oled_h + SETTINGS_ROW_GAP

        norm_h = self._settings_row_height("Patch normalization", inner_w, toggle=True)
        self.norm_global_toggle_rect = Rect(pad, y, inner_w, norm_h)
        y += norm_h + SETTINGS_ROW_GAP

        audio_h = self._settings_row_height(
            audio_profile_display(),
            inner_w,
            toggle=True,
        )
        self.audio_profile_toggle_rect = Rect(pad, y, inner_w, audio_h)
        y += audio_h + SETTINGS_ROW_GAP

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
    def _open_settings_panel(self, *, focus: str | None = None) -> None:
        self._layout_settings_content()
        self._settings_content_scroll.reset()
        if focus == "audio_profile":
            row = self.audio_profile_toggle_rect
            target = max(0, row.y - 12)
            self._settings_content_scroll._scroll_pixels = min(
                float(target),
                self._settings_content_scroll._max_scroll_pixels(),
            )
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
            content_top = self.status_rect.y + self.status_rect.h + gap
            content_bottom = self.height - BROWSER_BOTTOM_MARGIN

        show_folder_title = (
            not self.left_nav_collapsed
            and self.left_nav_mode in (LeftNavMode.PATCHES, LeftNavMode.ALL_PATCHES)
        )
        folder_title_h = NAV_FOLDER_TITLE_H if show_folder_title else 0
        list_top = content_top + nav_header_h + 4 + folder_title_h

        if show_folder_title:
            title_w = (
                self._left_nav_width()
                if self.left_nav_mode == LeftNavMode.ALL_PATCHES
                else LEFT_NAV_WIDTH
            )
            self.nav_folder_title_rect = Rect(
                margin,
                content_top + nav_header_h + 4,
                title_w,
                folder_title_h,
            )
        else:
            self.nav_folder_title_rect = None

        list_w = (
            self._left_nav_width()
            if self.left_nav_mode == LeftNavMode.ALL_PATCHES
            else LEFT_NAV_WIDTH
        )
        list_rect = Rect(margin, list_top, list_w, content_bottom - list_top)
        if self.left_nav_mode == LeftNavMode.ALL_PATCHES:
            row_height = ALL_PATCHES_ROW_HEIGHT
        elif self.left_nav_mode == LeftNavMode.PATCHES:
            row_height = 50
        else:
            row_height = 44
        if not hasattr(self, "nav_list"):
            self.nav_list = ScrollList(list_rect, row_height=row_height)
        else:
            self.nav_list.rect = list_rect
            self.nav_list.row_height = row_height
            self.nav_list._clamp_scroll()
    def _layout_az_rail_letters(self) -> None:
        letters = AZ_RAIL_LETTERS
        count = len(letters)
        if count == 0 or self.az_rail_rect.h <= 0:
            self.az_rail_letter_rects = []
            return
        cell_h = max(8, self.az_rail_rect.h // count)
        rects: list[tuple[str, Rect]] = []
        y = self.az_rail_rect.y
        for letter in letters:
            h = min(cell_h, self.az_rail_rect.bottom - y)
            if h <= 0:
                break
            rects.append(
                (
                    letter,
                    Rect(self.az_rail_rect.x, y, self.az_rail_rect.w, h),
                )
            )
            y += h
        self.az_rail_letter_rects = rects

    def _layout_nav_buttons(self) -> None:
        y = self.nav_header_rect.y + 4
        btn_h = 28
        icon_w = 32
        all_w = 40
        x = self.nav_header_rect.x + 6
        if self.left_nav_mode in (LeftNavMode.PATCHES, LeftNavMode.ALL_PATCHES):
            self.nav_back_btn = Rect(x, y, 36, btn_h)
            x += 42
        else:
            self.nav_back_btn = Rect(0, 0, 0, 0)
        self.nav_current_btn = Rect(x, y, icon_w, btn_h)
        x += icon_w + 6
        self.nav_all_btn = Rect(x, y, all_w, btn_h)
        if self.left_nav_mode != LeftNavMode.ALL_PATCHES:
            self.nav_collapse_btn = Rect(self.nav_header_rect.right - 38, y, 32, btn_h)
        else:
            self.nav_collapse_btn = Rect(0, 0, 0, 0)

    def _az_rail_letter_at(self, pos: tuple[int, int]) -> str | None:
        for letter, rect in self.az_rail_letter_rects:
            if rect.contains(*pos):
                return letter
        return None
    def _mixer_channel_defs(self) -> list[dict]:
        from patch_browser.patch_hold import HOLD_MULT_MAX, HOLD_MULT_MIN
        from patch_browser.patch_normalization import NORM_GAIN_DB_MAX, NORM_GAIN_DB_MIN

        defs: list[dict] = [
            {"id": "volume", "label": "Vol", "min": VOLUME_MIN, "max": VOLUME_MAX, "enabled": True},
        ]
        if getattr(self, "_show_tail_fader", getattr(self, "_show_hold_fader", lambda: False))():
            defs.append(
                {
                    "id": "tail",
                    "label": "Tail",
                    "min": HOLD_MULT_MIN,
                    "max": HOLD_MULT_MAX,
                    "enabled": True,
                }
            )
        if getattr(self, "_show_touch_fader", lambda: False)():
            from patch_browser.patch_pressure import PRESSURE_OFFSET_MAX, PRESSURE_OFFSET_MIN

            defs.append(
                {
                    "id": "touch",
                    "label": "Touch",
                    "min": PRESSURE_OFFSET_MIN,
                    "max": PRESSURE_OFFSET_MAX,
                    "enabled": True,
                }
            )
        if getattr(self, "_show_norm_level_fader", lambda: False)():
            defs.append(
                {
                    "id": "norm",
                    "label": "Norm",
                    "min": NORM_GAIN_DB_MIN,
                    "max": NORM_GAIN_DB_MAX,
                    "enabled": True,
                }
            )
        return defs

    def _detail_title_block(self) -> tuple[list[str], list[str], int, int, int]:
        """Patch name/category lines and y positions (name_y, cat_y, header_bottom)."""
        text_w = max(1, self.main_rect.w - DETAIL_TITLE_PAD_X * 2)
        name_y = self.main_rect.y + DETAIL_TITLE_PAD_TOP
        if not getattr(self, "detail_patch", None):
            header_bottom = self.main_rect.y + DETAIL_HEADER_MIN_H
            return [], [], name_y, name_y, header_bottom

        name_lines = wrap_text_lines(
            self.font_lg,
            self.detail_patch["name"],
            text_w,
            max_lines=2,
        )
        name_block_h = text_block_height(self.font_lg, len(name_lines), line_spacing=4)
        cat_y = name_y + name_block_h + DETAIL_TITLE_GAP
        cat_lines = wrap_text_lines(
            self.font_sm,
            self.detail_patch["category"],
            text_w,
            max_lines=2,
        )
        cat_block_h = text_block_height(self.font_sm, len(cat_lines), line_spacing=2)
        header_bottom = cat_y + cat_block_h
        return name_lines, cat_lines, name_y, cat_y, header_bottom

    def _detail_header_height(self) -> int:
        _, _, _name_y, _, header_bottom = self._detail_title_block()
        return max(DETAIL_HEADER_MIN_H, header_bottom - self.main_rect.y)

    def _detail_bottom_row_y(self) -> int:
        action_row_h = max(NORM_ROW_H, FAVORITES_BTN_SIZE)
        return self.main_rect.bottom - action_row_h - BROWSER_BOTTOM_MARGIN

    def _layout_mixer_strip(self) -> None:
        defs = self._mixer_channel_defs()
        count = len(defs)
        if count <= 0:
            self.mixer_channels = []
            return

        strip_top = self.main_rect.y + self._detail_header_height() + DETAIL_MIXER_GAP
        bottom_row_y = self._detail_bottom_row_y()
        available = bottom_row_y - strip_top - MIXER_BOTTOM_GAP
        track_h = max(FADER_TRACK_H, available - MIXER_LABEL_H)
        pad_x = DETAIL_TITLE_PAD_X
        inner_w = max(FADER_COLUMN_W, self.main_rect.w - pad_x * 2)
        columns_w = count * FADER_COLUMN_W
        gap = max(16, (inner_w - columns_w) // (count + 1))
        strip_x = self.main_rect.x + pad_x

        self.mixer_channels = []
        for i, spec in enumerate(defs):
            col_x = strip_x + gap + i * (FADER_COLUMN_W + gap)
            track_x = col_x + (FADER_COLUMN_W - FADER_TRACK_W) // 2
            column_rect = Rect(col_x, strip_top, FADER_COLUMN_W, track_h + MIXER_LABEL_H)
            track_rect = Rect(track_x, strip_top, FADER_TRACK_W, track_h)
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
