"""Touch patch browser — input mixin."""

from __future__ import annotations

import sys
import time

import pygame

from patch_browser.dsi_splash import release_display_for_shutdown, trigger_user_shutdown
from patch_browser.touch_ui_constants import (
    DEFAULT_BRIGHTNESS_PERCENT,
    MIXER_DOUBLE_TAP_MS,
    TAP_MOVE_THRESHOLD_PX,
)
from patch_browser.touch_ui_enums import CalibrateMode, LeftNavMode, Screen
from patch_browser.ui_theme import (
    ACCENT_PRESETS,
    ACCENT_STYLE_MINIMAL,
    ACCENT_STYLE_MONOCHROME,
    THEME_MODE_OLED_BLACK,
    THEME_MODE_STANDARD,
    THEME_VIEW_COLORS,
    THEME_VIEW_PICKER,
)


class TouchBrowserInputMixin:
    """Mixin — expects TouchPatchBrowser host attributes."""

    def _draw(self) -> None:
        modal = self.screen_state in (
            Screen.THEME,
            Screen.POWER_MENU,
            Screen.POWER_CONFIRM,
            Screen.CALIBRATE_CONFIRM,
        )
        panel_visible = self.screen_state == Screen.SETTINGS or self._settings_slide > 0.004

        if modal or panel_visible:
            self._draw_settings()
            if self.screen_state == Screen.POWER_MENU:
                self._draw_power_menu()
            elif self.screen_state == Screen.POWER_CONFIRM:
                self._draw_power_confirm()
            elif self.screen_state == Screen.THEME:
                self._draw_theme_modal()
            elif self.screen_state == Screen.CALIBRATE_CONFIRM:
                self._draw_calibrate_confirm()
        else:
            self._draw_browser()
        self._draw_toast()
        pygame.display.flip()
    def _ignore_sdl_pointer_event(self, event: pygame.event.Event) -> bool:
        if self._evdev_bridge is None or not self._evdev_bridge.active:
            return False
        return event.type in (
            pygame.MOUSEBUTTONDOWN,
            pygame.MOUSEBUTTONUP,
            pygame.MOUSEMOTION,
            pygame.FINGERDOWN,
            pygame.FINGERUP,
            pygame.FINGERMOTION,
        )
    def _handle_browser_tap(self, pos: tuple[int, int]) -> None:
        if self.system_settings_btn.contains(*pos):
            self._open_settings_panel()
            return

        if self.detail_patch and self.normalize_btn.contains(*pos):
            self._toggle_normalization()
            return

        if self.detail_patch and self.favorites_btn.contains(*pos):
            self._toggle_favorites()
            return

        if self.left_nav_collapsed:
            if self.nav_toggle_btn.contains(*pos):
                self._toggle_nav_collapsed()
            return

        if self.nav_collapse_btn.contains(*pos):
            self._toggle_nav_collapsed()
            return
        if self.nav_back_btn.contains(*pos) and self.left_nav_mode == LeftNavMode.PATCHES:
            self._go_up_to_folders()
            return
        if self.nav_current_btn.contains(*pos) and self._show_current_folder_button():
            self._go_to_loaded_folder()
            return
    def _settings_hit_at(self, pos: tuple[int, int]) -> str | None:
        close = self._panel_local_to_screen(self._close_settings_btn)
        if close.contains(*pos):
            return "close"

        power = self._panel_local_to_screen(self._power_btn)
        if power.contains(*pos):
            return "power"

        local_x = pos[0] - self._settings_panel_x()
        local_y = pos[1] - self.settings_panel_rect.y + int(
            self._settings_content_scroll.scroll_pixels
        )
        local_pos = (local_x, local_y)

        if self.norm_global_toggle_rect.contains(*local_pos):
            return "norm_global"
        if self.cpu_meter_toggle_rect.contains(*local_pos):
            return "cpu_meter"
        if self.theme_btn_rect.contains(*local_pos):
            return "theme"
        if self._surge_restart_btn and self._surge_restart_btn.contains(*local_pos):
            return "surge_restart"
        if self._calibrate_missing_btn.contains(*local_pos):
            return "cal_missing"
        if self._calibrate_force_btn.contains(*local_pos):
            return "cal_force"
        if self.brightness_slider_rect.contains(*local_pos):
            return "brightness"
        return None
    def _execute_settings_hit(self, hit: str, pos: tuple[int, int]) -> None:
        if hit == "close":
            self._close_settings_panel()
        elif hit == "power":
            self.screen_state = Screen.POWER_MENU
        elif hit == "norm_global":
            self._toggle_global_normalization()
        elif hit == "cpu_meter":
            self._toggle_cpu_meter_visibility()
        elif hit == "theme":
            self._open_theme_modal()
        elif hit == "surge_restart":
            ok, message = self.surge_monitor.restart_surge()
            if ok:
                self._toast(message, 2.5)
                patch = self.loaded_patch_info
                if patch:
                    self._last_known_surge_pid = None
                    self._surge_was_healthy = False
                    self._surge_liveness_initialized = False
                    self._queue_patch_reload(patch, delay_s=2.0)
                self._layout_settings_content()
            else:
                self._toast(f"Restart failed: {message}", 3.5)
        elif hit == "cal_missing":
            self._pending_calibrate_mode = CalibrateMode.MISSING_ONLY
            self.screen_state = Screen.CALIBRATE_CONFIRM
        elif hit == "cal_force":
            self._pending_calibrate_mode = CalibrateMode.FORCE_FULL
            self.screen_state = Screen.CALIBRATE_CONFIRM
        elif hit == "brightness":
            screen_slider = self._panel_local_to_screen(
                self.brightness_slider_rect, scrolled=True
            )
            now = time.time()
            if (
                not self._brightness_drag_moved
                and self._brightness_last_tap_time > 0
                and (now - self._brightness_last_tap_time) * 1000.0 <= MIXER_DOUBLE_TAP_MS
            ):
                self._apply_brightness(DEFAULT_BRIGHTNESS_PERCENT)
                self._toast("Brightness reset", 1.2)
                self._brightness_last_tap_time = 0.0
            else:
                self._brightness_last_tap_time = now
                self._apply_brightness(self._brightness_from_x(pos[0], screen_slider))
    def _handle_settings_pointer_down(self, pos: tuple[int, int]) -> None:
        self._clear_settings_pointer()
        self._settings_pointer_down_pos = pos

        hit = self._settings_hit_at(pos)
        if hit == "brightness":
            self._brightness_drag_moved = False
            self._slider_dragging = True
            self._settings_pending_hit = hit
            return

        scroll_vp = self._settings_scroll_viewport_screen()
        if scroll_vp.contains(*pos):
            self._sync_settings_scroll_viewport()
            self._settings_content_scroll.pointer_down(pos)
            self._settings_swipe_start = pos
            if hit is not None:
                self._settings_pending_hit = hit
            return

        if hit is not None:
            self._settings_pending_hit = hit
    def _handle_settings_pointer_up(self, pos: tuple[int, int]) -> None:
        scrolled = self._settings_content_scroll.pointer_up(pos)
        slider_moved = self._slider_dragging and self._brightness_drag_moved
        tap_ok = (
            not scrolled
            and not slider_moved
            and not self._settings_content_scroll.is_interacting()
            and self._pointer_move_distance(self._settings_pointer_down_pos, pos)
            <= TAP_MOVE_THRESHOLD_PX
        )

        if tap_ok and self._settings_pending_hit:
            down_hit = self._settings_pending_hit
            up_hit = self._settings_hit_at(pos)
            if up_hit == down_hit:
                self._execute_settings_hit(down_hit, pos)

        if (
            not scrolled
            and not self._slider_dragging
            and not self._settings_content_scroll.is_interacting()
        ):
            if not self._settings_panel_contains(pos):
                self._close_settings_panel()
            elif self._settings_swipe_start is not None:
                dx = pos[0] - self._settings_swipe_start[0]
                if dx > 56:
                    self._close_settings_panel()

        self._settings_swipe_start = None
        self._slider_dragging = False
        self._brightness_drag_moved = False
        self._clear_settings_pointer()
    def _handle_power_menu_pointer_down(self, pos: tuple[int, int]) -> None:
        self._clear_modal_pointer()
        self._modal_pointer_down_pos = pos
        for i, rect in enumerate(self._power_option_rects):
            if rect.contains(*pos):
                self._modal_pending_index = i
                return
    def _handle_power_menu_pointer_up(self, pos: tuple[int, int]) -> None:
        if (
            self._modal_pending_index is not None
            and self._pointer_move_distance(self._modal_pointer_down_pos, pos)
            <= TAP_MOVE_THRESHOLD_PX
        ):
            i = self._modal_pending_index
            if i == 0:
                self.power_action = "shutdown"
                self.screen_state = Screen.POWER_CONFIRM
            elif i == 1:
                self.power_action = "restart"
                self.screen_state = Screen.POWER_CONFIRM
            else:
                self.screen_state = Screen.SETTINGS
        self._clear_modal_pointer()
    def _theme_modal_hit_at(self, pos: tuple[int, int]) -> str | None:
        view = self._theme_view()

        if view == THEME_VIEW_PICKER:
            for channel, rect in getattr(self, "_picker_slider_rects", {}).items():
                if rect.contains(*pos):
                    return f"picker_slider:{channel}"
            back = getattr(self, "_picker_back_rect", None)
            if back is not None and back.contains(*pos):
                return "picker_back"
            save = getattr(self, "_picker_save_rect", None)
            if save is not None and save.contains(*pos):
                return "picker_save"
            delete = getattr(self, "_picker_delete_rect", None)
            if delete is not None and delete.contains(*pos):
                return "picker_delete"
            return None

        if view == THEME_VIEW_COLORS:
            for delete_rect, color_id in getattr(self, "_theme_color_delete_rects", []):
                if delete_rect.contains(*pos):
                    return f"delete:{color_id}"
            for rect, _rgb, hit_id in getattr(self, "_theme_color_swatch_rects", []):
                if rect.contains(*pos):
                    return hit_id
            back = getattr(self, "_theme_colors_back_rect", None)
            if back is not None and back.contains(*pos):
                return "colors_back"
            return None

        for index, rect in enumerate(getattr(self, "_theme_base_option_rects", [])):
            if rect.contains(*pos):
                return f"base:{index}"
        for index, rect in enumerate(getattr(self, "_theme_style_option_rects", [])):
            if rect.contains(*pos):
                return f"style:{index}"
        choose = getattr(self, "_theme_choose_color_btn", None)
        if choose is not None and choose.contains(*pos):
            return "choose_colors"
        done = getattr(self, "_theme_done_rect", None)
        if done is not None and done.contains(*pos):
            return "done"
        cancel = getattr(self, "_theme_cancel_rect", None)
        if cancel is not None and cancel.contains(*pos):
            return "cancel"
        return None

    def _apply_picker_slider_channel(self, channel: str, pos: tuple[int, int]) -> None:
        sliders = getattr(self, "_picker_slider_rects", {})
        rect = sliders.get(channel)
        if rect is None:
            return
        current = getattr(self, "_picker_rgb", self._theme_draft().accent_rgb)
        value = self._picker_channel_from_x(pos[0], rect)
        if channel == "r":
            self._set_picker_rgb((value, current[1], current[2]))
        elif channel == "g":
            self._set_picker_rgb((current[0], value, current[2]))
        else:
            self._set_picker_rgb((current[0], current[1], value))

    def _handle_theme_modal_pointer_down(self, pos: tuple[int, int]) -> None:
        self._clear_modal_pointer()
        self._modal_pointer_down_pos = pos
        hit = self._theme_modal_hit_at(pos)
        if hit is None:
            return
        if hit.startswith("picker_slider:"):
            self._picker_slider_channel = hit.split(":", 1)[1]
            self._apply_picker_slider_channel(self._picker_slider_channel, pos)
            self._modal_pending_key = hit
            return
        self._modal_pending_key = hit

    def _handle_theme_modal_pointer_up(self, pos: tuple[int, int]) -> None:
        if (
            self._modal_pending_key is None
            or self._pointer_move_distance(self._modal_pointer_down_pos, pos)
            > TAP_MOVE_THRESHOLD_PX
        ):
            self._clear_modal_pointer()
            return

        hit = self._modal_pending_key
        if hit == "choose_colors":
            self._open_theme_color_palette()
        elif hit == "colors_back":
            self._close_theme_color_palette()
        elif hit == "picker_back":
            self._close_theme_color_picker()
        elif hit == "picker_save":
            self._save_picker_custom_color()
            self._close_theme_color_picker()
        elif hit == "picker_delete":
            self._delete_picker_custom_color()
        elif hit.startswith("delete:"):
            self._delete_custom_accent_color(hit.split(":", 1)[1])
        elif hit == "custom_new":
            self._open_theme_color_picker()
        elif hit.startswith("preset:"):
            index = int(hit.split(":", 1)[1])
            if 0 <= index < len(ACCENT_PRESETS):
                _name, rgb = ACCENT_PRESETS[index]
                self._set_theme_accent_rgb(rgb)
        elif hit.startswith("custom:"):
            color_id = hit.split(":", 1)[1]
            for color in getattr(self, "_custom_accent_colors", []):
                if color.color_id == color_id:
                    self._set_theme_accent_rgb(color.rgb)
                    break
        elif hit == "base:0":
            self._set_theme_base_mode(THEME_MODE_STANDARD)
        elif hit == "base:1":
            self._set_theme_base_mode(THEME_MODE_OLED_BLACK)
        elif hit == "style:0":
            self._set_theme_accent_style(ACCENT_STYLE_MONOCHROME)
        elif hit == "style:1":
            self._set_theme_accent_style(ACCENT_STYLE_MINIMAL)
        elif hit == "done":
            self._commit_theme_modal()
        elif hit == "cancel":
            self._cancel_theme_modal()
        self._picker_slider_channel = None
        self._clear_modal_pointer()

    def _handle_calibrate_confirm_pointer_down(self, pos: tuple[int, int]) -> None:
        self._clear_modal_pointer()
        self._modal_pointer_down_pos = pos
        if self._calibrate_confirm_no.contains(*pos):
            self._modal_pending_index = 0
        elif self._calibrate_confirm_yes.contains(*pos):
            self._modal_pending_index = 1
    def _handle_calibrate_confirm_pointer_up(self, pos: tuple[int, int]) -> None:
        if (
            self._modal_pending_index is None
            or self._pointer_move_distance(self._modal_pointer_down_pos, pos)
            > TAP_MOVE_THRESHOLD_PX
        ):
            self._clear_modal_pointer()
            return
        if self._modal_pending_index == 0:
            self.screen_state = Screen.SETTINGS
        else:
            targets, _ = self._calibration_scope_stats(self._pending_calibrate_mode)
            if (
                self._pending_calibrate_mode == CalibrateMode.MISSING_ONLY
                and targets == 0
            ):
                self._toast("All patches already calibrated", 2.5)
                self.screen_state = Screen.SETTINGS
            else:
                self._launch_calibration_loader()
        self._clear_modal_pointer()
    def _handle_power_confirm_pointer_down(self, pos: tuple[int, int]) -> None:
        self._clear_modal_pointer()
        self._modal_pointer_down_pos = pos
        if self._confirm_no.contains(*pos):
            self._modal_pending_index = 0
        elif self._confirm_yes.contains(*pos):
            self._modal_pending_index = 1
    def _handle_power_confirm_pointer_up(self, pos: tuple[int, int]) -> None:
        if (
            self._modal_pending_index is not None
            and self._pointer_move_distance(self._modal_pointer_down_pos, pos)
            <= TAP_MOVE_THRESHOLD_PX
        ):
            if self._modal_pending_index == 0:
                self.screen_state = Screen.POWER_MENU
            else:
                if self._evdev_bridge is not None:
                    self._evdev_bridge.stop()
                release_display_for_shutdown(self.screen, theme=self.theme)
                self._running = False
                trigger_user_shutdown(self.power_action)
                sys.exit(0)
        self._clear_modal_pointer()
    def _handle_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.QUIT:
            self._running = False
            return

        if event.type in (pygame.FINGERDOWN, pygame.FINGERMOTION, pygame.FINGERUP):
            x = int(event.x * self.width)
            y = int(event.y * self.height)
            pos = (x, y)
            if event.type == pygame.FINGERDOWN:
                event = pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"pos": pos, "button": 1})
            elif event.type == pygame.FINGERUP:
                event = pygame.event.Event(pygame.MOUSEBUTTONUP, {"pos": pos, "button": 1})
            else:
                event = pygame.event.Event(
                    pygame.MOUSEMOTION,
                    {"pos": pos, "rel": (0, 0), "buttons": (1, 0, 0)},
                )

        if self.screen_state == Screen.BROWSER and not self.left_nav_collapsed:
            self.nav_list.handle_event(event)

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.screen_state == Screen.SETTINGS:
                self._handle_settings_pointer_down(event.pos)
            elif self.screen_state == Screen.POWER_MENU:
                self._handle_power_menu_pointer_down(event.pos)
            elif self.screen_state == Screen.POWER_CONFIRM:
                self._handle_power_confirm_pointer_down(event.pos)
            elif self.screen_state == Screen.THEME:
                self._handle_theme_modal_pointer_down(event.pos)
            elif self.screen_state == Screen.CALIBRATE_CONFIRM:
                self._handle_calibrate_confirm_pointer_down(event.pos)
            elif self.screen_state == Screen.BROWSER:
                self._handle_mixer_down(event.pos)
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            was_mixer = self._dragging_mixer_id is not None
            if was_mixer and self._mixer_drag_moved:
                self._mixer_last_tap_id = None
            self._dragging_mixer_id = None
            self._mixer_drag_origin = None
            self._mixer_drag_moved = False
            if self._picker_slider_channel and self.screen_state == Screen.THEME:
                self._picker_slider_channel = None
            if self.screen_state == Screen.SETTINGS:
                self._handle_settings_pointer_up(event.pos)
            elif self.screen_state == Screen.POWER_MENU:
                self._handle_power_menu_pointer_up(event.pos)
            elif self.screen_state == Screen.POWER_CONFIRM:
                self._handle_power_confirm_pointer_up(event.pos)
            elif self.screen_state == Screen.THEME:
                self._handle_theme_modal_pointer_up(event.pos)
            elif self.screen_state == Screen.CALIBRATE_CONFIRM:
                self._handle_calibrate_confirm_pointer_up(event.pos)
            elif self.screen_state == Screen.BROWSER:
                idx = self.nav_list.take_tap_index()
                if idx is not None:
                    self._select_nav_index(idx)
                elif not was_mixer:
                    self._handle_browser_tap(event.pos)
        elif event.type == pygame.MOUSEMOTION:
            if self._picker_slider_channel and self.screen_state == Screen.THEME:
                self._apply_picker_slider_channel(self._picker_slider_channel, event.pos)
            elif self._slider_dragging:
                if not self._brightness_drag_moved:
                    self._brightness_drag_moved = True
                    self._brightness_last_tap_time = 0.0
                screen_slider = self._panel_local_to_screen(
                    self.brightness_slider_rect, scrolled=True
                )
                self._apply_brightness(self._brightness_from_x(event.pos[0], screen_slider))
            elif self.screen_state == Screen.SETTINGS:
                self._sync_settings_scroll_viewport()
                self._settings_content_scroll.pointer_move(event.pos)
            self._handle_mixer_motion(event.pos)
