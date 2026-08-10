"""Touch patch browser — input mixin."""

from __future__ import annotations

import sys
import time

import pygame

from patch_browser.dsi_splash import release_display_for_shutdown, trigger_user_shutdown
from patch_browser.shutdown_trace import begin_shutdown_session, log_shutdown_event
from patch_browser.touch_ui_constants import (
    SETTINGS_PANEL_HEADER_H,
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

    _BACKDROP_DISMISS_SCREENS = frozenset(
        {
            Screen.THEME,
            Screen.POWER_MENU,
            Screen.POWER_CONFIRM,
            Screen.CALIBRATE_CONFIRM,
            Screen.SURGE_BUFFER_MODAL,
            Screen.SURGE_SAMPLE_RATE_MODAL,
            Screen.AUDIO_PROFILE_MODAL,
            Screen.BRIGHTNESS_MODAL,
            Screen.WIFI_MODAL,
            Screen.MIDI_SYNC_MODAL,
        }
    )

    def _modal_backdrop_tap(
        self,
        start: tuple[int, int] | None,
        end: tuple[int, int],
    ) -> bool:
        panel = getattr(self, "_modal_panel_rect", None)
        if panel is None or start is None:
            return False
        if self._pointer_move_distance(start, end) > TAP_MOVE_THRESHOLD_PX:
            return False
        return not panel.contains(*start) and not panel.contains(*end)

    def _dismiss_modal_from_backdrop(self) -> None:
        state = self.screen_state
        if state == Screen.THEME:
            self._cancel_theme_modal()
        elif state == Screen.POWER_MENU:
            self.screen_state = Screen.SETTINGS
        elif state == Screen.POWER_CONFIRM:
            self.screen_state = Screen.POWER_MENU
        elif state == Screen.CALIBRATE_CONFIRM:
            self.screen_state = Screen.SETTINGS
        elif state in (Screen.SURGE_BUFFER_MODAL, Screen.SURGE_SAMPLE_RATE_MODAL):
            self._close_surge_audio_modal()
        elif state == Screen.AUDIO_PROFILE_MODAL:
            self._close_audio_profile_modal()
        elif state == Screen.BRIGHTNESS_MODAL:
            self._close_brightness_modal()
        elif state == Screen.WIFI_MODAL:
            self._close_wifi_modal()
        elif state == Screen.MIDI_SYNC_MODAL:
            self._close_midi_sync_modal()

    def _try_dismiss_modal_backdrop(self, pos: tuple[int, int]) -> bool:
        if self.screen_state not in self._BACKDROP_DISMISS_SCREENS:
            return False
        if not self._modal_backdrop_tap(self._modal_pointer_down_pos, pos):
            return False
        self._dismiss_modal_from_backdrop()
        self._clear_modal_pointer()
        self._picker_slider_channel = None
        return True

    def _draw(self) -> None:
        modal = self.screen_state in (
            Screen.THEME,
            Screen.POWER_MENU,
            Screen.POWER_CONFIRM,
            Screen.CALIBRATE_CONFIRM,
            Screen.SURGE_BUFFER_MODAL,
            Screen.SURGE_SAMPLE_RATE_MODAL,
            Screen.AUDIO_PROFILE_MODAL,
            Screen.BRIGHTNESS_MODAL,
            Screen.WIFI_MODAL,
            Screen.MIDI_SYNC_MODAL,
        )
        overlay_modal = self.screen_state in (Screen.CONTEXT_MENU, Screen.NAME_PROMPT)
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
            elif self.screen_state == Screen.SURGE_BUFFER_MODAL:
                self._draw_surge_buffer_modal()
            elif self.screen_state == Screen.SURGE_SAMPLE_RATE_MODAL:
                self._draw_surge_sample_rate_modal()
            elif self.screen_state == Screen.AUDIO_PROFILE_MODAL:
                self._draw_audio_profile_modal()
            elif self.screen_state == Screen.BRIGHTNESS_MODAL:
                self._draw_brightness_modal()
            elif self.screen_state == Screen.WIFI_MODAL:
                self._draw_wifi_modal()
            elif self.screen_state == Screen.MIDI_SYNC_MODAL:
                self._draw_midi_sync_modal()
        elif overlay_modal:
            self._draw_browser()
            if self.screen_state == Screen.CONTEXT_MENU:
                self._draw_context_menu()
            else:
                self._draw_name_prompt()
        else:
            self._draw_browser()
        if getattr(self, "_audio_profile_switching", False):
            self._draw_audio_profile_switch_overlay()
        elif getattr(self, "_surge_audio_switching", False):
            self._draw_surge_audio_switch_overlay()
        if getattr(self, "_wifi_busy", False) or getattr(self, "_wifi_connecting", False):
            self._draw_wifi_busy_overlay()
        self._draw_toast()
        pygame.display.flip()
        recorder = getattr(self, "_screen_recorder", None)
        if recorder is not None:
            recorder.write_frame(self.screen)

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
        if self.audio_profile_badge_rect.contains(*pos):
            self._open_settings_panel(focus="audio_profile")
            return

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
        if (
            self.nav_all_btn.contains(*pos)
            and self.left_nav_mode != LeftNavMode.ALL_PATCHES
        ):
            self._enter_all_patches()
            return
        if self.nav_back_btn.contains(*pos) and self.left_nav_mode in (
            LeftNavMode.PATCHES,
            LeftNavMode.ALL_PATCHES,
        ):
            if self.left_nav_mode == LeftNavMode.ALL_PATCHES:
                self._go_back_from_all_patches()
            else:
                self._go_up_to_folders()
            return
        if self.nav_current_btn.contains(*pos) and self.loaded_patch_info:
            self._go_to_loaded_folder()
            return
    def _settings_local_pos(self, pos: tuple[int, int]) -> tuple[int, int]:
        local_x = pos[0] - self._settings_panel_x()
        local_y = (
            pos[1]
            - self.settings_panel_rect.y
            - SETTINGS_PANEL_HEADER_H
            + int(self._settings_content_scroll.scroll_pixels)
        )
        return local_x, local_y

    def _settings_rect_hit(self, rect: Rect, local_pos: tuple[int, int]) -> bool:
        return rect.w > 0 and rect.h > 0 and rect.contains(*local_pos)

    def _settings_hit_at(self, pos: tuple[int, int]) -> str | None:
        close = self._panel_local_to_screen(self._close_settings_btn)
        if close.contains(*pos):
            return "close"

        if getattr(self, "_settings_view", "root") == "audio":
            back = self._panel_local_to_screen(self._settings_back_btn)
            if back.contains(*pos):
                return "settings_back"

        power = self._panel_local_to_screen(self._power_btn)
        if power.contains(*pos):
            return "power"

        local_pos = self._settings_local_pos(pos)
        audio_view = getattr(self, "_settings_view", "root") == "audio"

        if audio_view:
            if self._settings_rect_hit(self.audio_profile_row_rect, local_pos):
                return "audio_profile"
            if self._settings_rect_hit(self.poly_governor_toggle_rect, local_pos):
                return "poly_governor"
            if self._settings_rect_hit(self.norm_global_toggle_rect, local_pos):
                return "norm_global"
            if self._settings_rect_hit(self.surge_buffer_row_rect, local_pos):
                return "surge_buffer"
            if self._settings_rect_hit(self.surge_sample_rate_row_rect, local_pos):
                return "surge_sample_rate"
            if self._settings_rect_hit(self._calibrate_missing_btn, local_pos):
                return "cal_missing"
            if self._settings_rect_hit(self._calibrate_force_btn, local_pos):
                return "cal_force"
            return None

        if self._settings_rect_hit(self.settings_audio_drill_rect, local_pos):
            return "audio_drill"
        if self._settings_rect_hit(self.settings_advanced_header_rect, local_pos):
            return "advanced_toggle"
        if self._settings_rect_hit(self.cpu_meter_toggle_rect, local_pos):
            return "cpu_meter"
        if self._settings_rect_hit(self.looper_hud_toggle_rect, local_pos):
            return "looper_hud"
        if self._settings_rect_hit(self.looper_sync_row_rect, local_pos):
            return "looper_sync"
        if self._settings_rect_hit(self.wifi_row_rect, local_pos):
            return "wifi"
        if self._settings_rect_hit(self.theme_btn_rect, local_pos):
            return "theme"
        if self._surge_restart_btn and self._settings_rect_hit(self._surge_restart_btn, local_pos):
            return "surge_restart"
        if self._settings_rect_hit(self.brightness_row_rect, local_pos):
            return "brightness"
        return None
    def _execute_settings_hit(self, hit: str, pos: tuple[int, int]) -> None:
        if hit == "close":
            self._close_settings_panel()
        elif hit == "settings_back":
            self._settings_view = "root"
            self._layout_settings_content()
            self._settings_content_scroll.reset()
            self._sync_settings_scroll_viewport()
        elif hit == "audio_drill":
            self._settings_view = "audio"
            self._layout_settings_content()
            self._settings_content_scroll.reset()
            self._sync_settings_scroll_viewport()
        elif hit == "advanced_toggle":
            self._settings_advanced_open = not getattr(self, "_settings_advanced_open", False)
            self._layout_settings_content()
            self._sync_settings_scroll_viewport()
        elif hit == "power":
            self.screen_state = Screen.POWER_MENU
        elif hit == "norm_global":
            self._toggle_global_normalization()
        elif hit == "cpu_meter":
            self._toggle_cpu_meter_visibility()
        elif hit == "looper_hud":
            self._toggle_looper_hud_visibility()
        elif hit == "looper_sync":
            if not getattr(self, "_midi_sync_switching", False):
                self._open_midi_sync_modal()
        elif hit == "poly_governor":
            self._toggle_poly_governor()
        elif hit == "audio_profile":
            if not getattr(self, "_audio_profile_switching", False) and not getattr(
                self, "_surge_audio_switching", False
            ):
                self._open_audio_profile_modal()
        elif hit == "wifi":
            if not getattr(self, "_wifi_busy", False):
                self._open_wifi_modal()
        elif hit == "surge_buffer":
            if not getattr(self, "_audio_profile_switching", False) and not getattr(
                self, "_surge_audio_switching", False
            ):
                self._open_surge_buffer_modal()
        elif hit == "surge_sample_rate":
            if not getattr(self, "_audio_profile_switching", False) and not getattr(
                self, "_surge_audio_switching", False
            ):
                self._open_surge_sample_rate_modal()
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
            self._open_brightness_modal()
    def _handle_settings_pointer_down(self, pos: tuple[int, int]) -> None:
        self._clear_settings_pointer()
        self._settings_pointer_down_pos = pos
        self._settings_pending_hit = None

        hit = self._settings_hit_at(pos)
        if hit in ("close", "settings_back", "power"):
            self._settings_pending_hit = hit
            self._touch_press.set(f"settings:{hit}")
            return

        scroll_vp = self._settings_scroll_viewport_screen()
        if scroll_vp.contains(*pos):
            self._sync_settings_scroll_viewport()
            self._settings_content_scroll.pointer_down(pos)
            self._settings_swipe_start = pos
            if hit is not None:
                self._touch_press.set(f"settings:{hit}")
            return

        if hit is not None:
            self._touch_press.set(f"settings:{hit}")

    def _handle_settings_pointer_up(self, pos: tuple[int, int]) -> None:
        scrolled = self._settings_content_scroll.pointer_up(pos)
        tap_ok = (
            not scrolled
            and not self._settings_content_scroll.is_interacting()
            and self._pointer_move_distance(self._settings_pointer_down_pos, pos)
            <= TAP_MOVE_THRESHOLD_PX
        )

        if tap_ok:
            hit = self._settings_hit_at(pos)
            down_hit = self._settings_pending_hit
            if down_hit in ("close", "settings_back", "power"):
                hit = down_hit
            if hit is not None:
                self._execute_settings_hit(hit, pos)

        if (
            not scrolled
            and not self._settings_content_scroll.is_interacting()
        ):
            if not self._settings_panel_contains(pos):
                self._close_settings_panel()
            elif self._settings_swipe_start is not None:
                dx = pos[0] - self._settings_swipe_start[0]
                if dx > 56:
                    self._close_settings_panel()

        self._settings_swipe_start = None
        self._clear_settings_pointer()
    def _handle_power_menu_pointer_down(self, pos: tuple[int, int]) -> None:
        self._clear_modal_pointer()
        power_ids = ("power:shutdown", "power:restart", "power:cancel")
        for i, rect in enumerate(self._power_option_rects):
            if rect.contains(*pos):
                self._modal_press_hit(pos, power_ids[i])
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
            back = getattr(self, "_theme_colors_back_rect", None)
            if back is not None and back.contains(*pos):
                return "colors_back"
            scroll = getattr(self, "_theme_colors_scroll", None)
            if scroll is not None and scroll.viewport.contains(*pos):
                lx = pos[0] - scroll.viewport.x
                ly = pos[1] - scroll.viewport.y + int(scroll.scroll_pixels)
                for delete_rect, color_id in getattr(self, "_theme_color_delete_rects_content", []):
                    if delete_rect.contains(lx, ly):
                        return f"delete:{color_id}"
                for rect, _rgb, hit_id in getattr(self, "_theme_color_swatch_rects_content", []):
                    if rect.contains(lx, ly):
                        return hit_id
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

    def _apply_theme_modal_hit(self, hit: str) -> None:
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

    def _handle_theme_modal_pointer_down(self, pos: tuple[int, int]) -> None:
        self._clear_modal_pointer()
        if self._theme_view() == THEME_VIEW_COLORS:
            if self._theme_colors_scroll.viewport.contains(*pos):
                self._theme_colors_scroll.pointer_down(pos)
            hit = self._theme_modal_hit_at(pos)
            self._touch_press.set(hit)
            return
        hit = self._theme_modal_hit_at(pos)
        if hit is None:
            return
        if hit.startswith("picker_slider:"):
            self._picker_slider_channel = hit.split(":", 1)[1]
            self._apply_picker_slider_channel(self._picker_slider_channel, pos)
        self._modal_press_hit(pos, hit)

    def _handle_theme_modal_pointer_up(self, pos: tuple[int, int]) -> None:
        if self._theme_view() == THEME_VIEW_COLORS:
            scrolled = self._theme_colors_scroll.pointer_up(pos)
            tap_ok = (
                not scrolled
                and not self._theme_colors_scroll.is_interacting()
                and self._pointer_move_distance(self._modal_pointer_down_pos, pos)
                <= TAP_MOVE_THRESHOLD_PX
            )
            if tap_ok:
                hit = self._theme_modal_hit_at(pos)
                if hit is not None:
                    self._apply_theme_modal_hit(hit)
            self._picker_slider_channel = None
            self._clear_modal_pointer()
            return

        if (
            self._modal_pending_key is None
            or self._pointer_move_distance(self._modal_pointer_down_pos, pos)
            > TAP_MOVE_THRESHOLD_PX
        ):
            self._clear_modal_pointer()
            return

        self._apply_theme_modal_hit(self._modal_pending_key)
        self._picker_slider_channel = None
        self._clear_modal_pointer()

    def _handle_calibrate_confirm_pointer_down(self, pos: tuple[int, int]) -> None:
        self._clear_modal_pointer()
        if self._calibrate_confirm_no.contains(*pos):
            self._modal_press_hit(pos, "cal:cancel")
            self._modal_pending_index = 0
        elif self._calibrate_confirm_yes.contains(*pos):
            self._modal_press_hit(pos, "cal:start")
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
        if self._confirm_no.contains(*pos):
            self._modal_press_hit(pos, "confirm:cancel")
            self._modal_pending_index = 0
        elif self._confirm_yes.contains(*pos):
            self._modal_press_hit(pos, "confirm:yes")
            self._modal_pending_index = 1

    def _handle_power_confirm_pointer_up(self, pos: tuple[int, int]) -> None:
        if (
            self._modal_pending_index is not None
            and self._pointer_move_distance(self._modal_pointer_down_pos, pos)
            <= TAP_MOVE_THRESHOLD_PX
        ):
            if self._modal_pending_index == 0:
                self.screen_state = Screen.POWER_MENU
            elif not getattr(self, "_shutdown_in_progress", False):
                self._shutdown_in_progress = True
                begin_shutdown_session("power_menu_confirm")
                log_shutdown_event(
                    "shutdown_step_power_confirm",
                    action=self.power_action,
                )
                if self._evdev_bridge is not None:
                    log_shutdown_event("shutdown_step_evdev_stop")
                    self._evdev_bridge.stop()
                release_display_for_shutdown(self.screen, theme=self.theme)
                self._running = False
                log_shutdown_event("shutdown_step_browser_exit")
                trigger_user_shutdown(self.power_action)
                sys.exit(0)
        self._clear_modal_pointer()
    def _handle_event(self, event: pygame.event.Event) -> None:
        if getattr(self, "_audio_profile_switching", False) or getattr(
            self, "_surge_audio_switching", False
        ) or getattr(self, "_wifi_busy", False) or getattr(self, "_wifi_connecting", False):
            return
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

        if (
            self.screen_state == Screen.BROWSER
            and not self.left_nav_collapsed
            and event.type
            in (
                pygame.MOUSEBUTTONDOWN,
                pygame.MOUSEBUTTONUP,
                pygame.MOUSEMOTION,
                pygame.MOUSEWHEEL,
            )
        ):
            chip_active = self._instrument_chip_active()
            if event.type == pygame.MOUSEBUTTONDOWN:
                if self._handle_instrument_chip_pointer_down(event.pos):
                    chip_active = True
                elif not chip_active:
                    self._context_nav_pointer_down(event.pos)
            elif event.type == pygame.MOUSEMOTION:
                if self._handle_instrument_chip_pointer_move(event.pos):
                    chip_active = True
                else:
                    self._context_nav_pointer_move(event.pos)
            elif event.type == pygame.MOUSEBUTTONUP:
                if self._handle_instrument_chip_pointer_up(event.pos):
                    chip_active = True
                elif self._context_nav_pointer_up():
                    chip_active = True
            if not chip_active:
                self.nav_list.handle_event(event)

        if self.screen_state == Screen.CONTEXT_MENU and event.type in (
            pygame.MOUSEBUTTONDOWN,
            pygame.MOUSEMOTION,
            pygame.MOUSEBUTTONUP,
        ):
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                self._handle_context_menu_pointer_down(event.pos)
            elif event.type == pygame.MOUSEMOTION:
                self._handle_context_menu_pointer_move(event.pos)
            elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                self._handle_context_menu_pointer_up(event.pos)
            return

        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            if self.screen_state == Screen.NAME_PROMPT:
                self._handle_name_prompt_pointer_up(event.pos)
                return

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.screen_state == Screen.NAME_PROMPT:
                self._handle_name_prompt_pointer_down(event.pos)
                return
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
            elif self.screen_state == Screen.SURGE_BUFFER_MODAL:
                self._handle_surge_buffer_modal_pointer_down(event.pos)
            elif self.screen_state == Screen.SURGE_SAMPLE_RATE_MODAL:
                self._handle_surge_sample_rate_modal_pointer_down(event.pos)
            elif self.screen_state == Screen.AUDIO_PROFILE_MODAL:
                self._handle_audio_profile_modal_pointer_down(event.pos)
            elif self.screen_state == Screen.BRIGHTNESS_MODAL:
                self._handle_brightness_modal_pointer_down(event.pos)
            elif self.screen_state == Screen.WIFI_MODAL:
                self._handle_wifi_modal_pointer_down(event.pos)
            elif self.screen_state == Screen.MIDI_SYNC_MODAL:
                self._handle_midi_sync_modal_pointer_down(event.pos)
            elif self.screen_state == Screen.BROWSER:
                if self._handle_az_rail_touch("down", event.pos):
                    pass
                elif self.left_nav_mode != LeftNavMode.ALL_PATCHES:
                    self._handle_mixer_down(event.pos)
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            was_mixer = self._dragging_mixer_id is not None
            if was_mixer:
                if self._mixer_drag_moved:
                    self._mixer_last_tap_id = None
                self._persist_mixer_drag()
            self._dragging_mixer_id = None
            self._mixer_drag_origin = None
            self._mixer_drag_moved = False
            if self._picker_slider_channel and self.screen_state == Screen.THEME:
                self._picker_slider_channel = None
            if self._try_dismiss_modal_backdrop(event.pos):
                pass
            elif self.screen_state == Screen.SETTINGS:
                self._handle_settings_pointer_up(event.pos)
            elif self.screen_state == Screen.POWER_MENU:
                self._handle_power_menu_pointer_up(event.pos)
            elif self.screen_state == Screen.POWER_CONFIRM:
                self._handle_power_confirm_pointer_up(event.pos)
            elif self.screen_state == Screen.THEME:
                self._handle_theme_modal_pointer_up(event.pos)
            elif self.screen_state == Screen.CALIBRATE_CONFIRM:
                self._handle_calibrate_confirm_pointer_up(event.pos)
            elif self.screen_state == Screen.SURGE_BUFFER_MODAL:
                self._handle_surge_buffer_modal_pointer_up(event.pos)
            elif self.screen_state == Screen.SURGE_SAMPLE_RATE_MODAL:
                self._handle_surge_sample_rate_modal_pointer_up(event.pos)
            elif self.screen_state == Screen.AUDIO_PROFILE_MODAL:
                self._handle_audio_profile_modal_pointer_up(event.pos)
            elif self.screen_state == Screen.BRIGHTNESS_MODAL:
                self._handle_brightness_modal_pointer_up(event.pos)
            elif self.screen_state == Screen.WIFI_MODAL:
                self._handle_wifi_modal_pointer_up(event.pos)
            elif self.screen_state == Screen.MIDI_SYNC_MODAL:
                self._handle_midi_sync_modal_pointer_up(event.pos)
            elif self.screen_state == Screen.BROWSER:
                if self._handle_az_rail_touch("up", event.pos):
                    return
                if self.screen_state != Screen.BROWSER:
                    return
                idx = self.nav_list.take_tap_index()
                if idx is not None and self.screen_state == Screen.BROWSER:
                    self._select_nav_index(idx)
                elif not was_mixer:
                    self._handle_browser_tap(event.pos)
        elif event.type == pygame.MOUSEMOTION:
            if self._handle_az_rail_touch("motion", event.pos):
                pass
            elif self._picker_slider_channel and self.screen_state == Screen.THEME:
                self._apply_picker_slider_channel(self._picker_slider_channel, event.pos)
            elif (
                self.screen_state == Screen.THEME
                and self._theme_view() == THEME_VIEW_COLORS
            ):
                self._theme_colors_scroll.pointer_move(event.pos)
            elif self.screen_state == Screen.SETTINGS:
                self._sync_settings_scroll_viewport()
                self._settings_content_scroll.pointer_move(event.pos)
                if self._settings_content_scroll.scroll_gesture_active:
                    self._touch_press.clear()
            elif self.screen_state == Screen.WIFI_MODAL:
                self._handle_wifi_modal_pointer_move(event.pos)
            elif self.screen_state == Screen.SURGE_BUFFER_MODAL:
                self._handle_surge_buffer_modal_pointer_move(event.pos)
            elif self.screen_state == Screen.BRIGHTNESS_MODAL:
                self._handle_brightness_modal_pointer_move(event.pos)
            self._handle_mixer_motion(event.pos)
