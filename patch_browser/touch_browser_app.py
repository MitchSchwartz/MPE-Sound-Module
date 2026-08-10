#!/usr/bin/env python3
"""Touch patch browser application orchestrator."""

from __future__ import annotations

import os
import queue
import signal
import sys
import threading
import time

try:
    import pygame
except ImportError as exc:
    print("FATAL: pygame is required for the touch patch browser.")
    print("On the Pi: sudo apt install python3-pygame")
    print("Or: pip3 install pygame")
    raise SystemExit(1) from exc

from patch_browser.shutdown_trace import log_shutdown_event
from patch_browser.backlight import BacklightController
from patch_browser.geometry import Rect
from patch_browser.mixer import MixerChannel
from patch_browser.patch_loader import PatchLoader
from patch_browser.patch_scanner import FAVORITES_NAME, SURGE_PATCH_DIRS, PatchScanner, favorites_display_name
from patch_browser.scroll_widgets import ContentScrollArea, ScrollList
from patch_browser.looper_clock_monitor import LooperClockMonitor
from patch_browser.screen_recorder import DEFAULT_ENV_FILE, ScreenRecorder
from patch_browser.surge_cpu_monitor import SurgeCpuMonitor
from patch_browser.surge_monitor import SurgeMonitor
from patch_browser.touch_evdev import TouchEvdevBridge, evdev_bridge_enabled
from patch_browser.touch_browser_context import TouchBrowserContextMixin
from patch_browser.touch_browser_draw import TouchBrowserDrawMixin
from patch_browser.touch_browser_evdev import TouchBrowserEvdevMixin
from patch_browser.touch_browser_input import TouchBrowserInputMixin
from patch_browser.touch_browser_layout import TouchBrowserLayoutMixin
from patch_browser.touch_browser_nav import TouchBrowserNavMixin
from patch_browser.touch_browser_hold import TouchBrowserHoldMixin
from patch_browser.touch_browser_instruments import TouchBrowserInstrumentsMixin
from patch_browser.touch_browser_touch import TouchBrowserTouchMixin
from patch_browser.touch_browser_mixer import TouchBrowserMixerMixin
from patch_browser.touch_browser_normalization import TouchBrowserNormalizationMixin
from patch_browser.touch_browser_patches import TouchBrowserPatchesMixin
from patch_browser.touch_browser_prefs import TouchBrowserPrefsMixin
from patch_browser.touch_browser_brightness_modal import TouchBrowserBrightnessModalMixin
from patch_browser.touch_browser_settings import TouchBrowserSettingsMixin
from patch_browser.touch_browser_audio_profile_modal import TouchBrowserAudioProfileModalMixin
from patch_browser.touch_browser_surge_audio_modal import TouchBrowserSurgeAudioModalMixin
from patch_browser.touch_browser_midi_sync_modal import TouchBrowserMidiSyncModalMixin
from patch_browser.touch_browser_wifi_modal import TouchBrowserWifiModalMixin
from patch_browser.touch_ui_constants import TAP_MOVE_THRESHOLD_PX
from patch_browser.touch_ui_enums import CalibrateMode, LeftNavMode, Screen
from patch_browser.dsi_splash import (
    BOOT_MIN_SECONDS,
    SplashMode,
    acquire_browser_display,
    boot_animation_phase,
    clear_browser_ready_flag,
    draw_splash_frame,
    signal_browser_ready,
)
from patch_browser.touch_press import TouchPressState
from patch_browser.ui_theme import DEFAULT_ACCENT_RGB, THEME_VIEW_COLORS, THEME_VIEW_MAIN, reload_theme_from_prefs, theme_for_mode


class TouchPatchBrowser(
    TouchBrowserEvdevMixin,
    TouchBrowserPrefsMixin,
    TouchBrowserSettingsMixin,
    TouchBrowserBrightnessModalMixin,
    TouchBrowserAudioProfileModalMixin,
    TouchBrowserSurgeAudioModalMixin,
    TouchBrowserMidiSyncModalMixin,
    TouchBrowserWifiModalMixin,
    TouchBrowserLayoutMixin,
    TouchBrowserNavMixin,
    TouchBrowserInstrumentsMixin,
    TouchBrowserContextMixin,
    TouchBrowserPatchesMixin,
    TouchBrowserMixerMixin,
    TouchBrowserHoldMixin,
    TouchBrowserTouchMixin,
    TouchBrowserNormalizationMixin,
    TouchBrowserDrawMixin,
    TouchBrowserInputMixin,
):
    """Fullscreen touch patch browser."""

    def __init__(self) -> None:
        clear_browser_ready_flag()
        windowed = os.environ.get("MPE_TOUCH_WINDOWED") == "1"
        if windowed:
            pygame.init()
            pygame.display.set_caption("Pi-Surge-MPE Touch Browser")
            self.screen = pygame.display.set_mode((800, 480))
        else:
            try:
                self.screen = acquire_browser_display()
            except RuntimeError as exc:
                print(f"FATAL: cannot acquire DSI display: {exc}", file=sys.stderr)
                if pygame.get_init():
                    pygame.quit()
                raise SystemExit(1) from exc
            except Exception as exc:
                if type(exc).__name__ != "error":
                    raise
                print(f"FATAL: cannot acquire DSI display: {exc}", file=sys.stderr)
                if pygame.get_init():
                    pygame.quit()
                raise SystemExit(1) from exc
            pygame.display.set_caption("Pi-Surge-MPE Touch Browser")
        self.width, self.height = self.screen.get_size()
        self._screen_recorder = ScreenRecorder.from_env()
        self._recorder_reload_requested = False
        self._recorder_stop_requested = False
        pygame.mouse.set_visible(False)
        prefs = reload_theme_from_prefs()
        self.theme_mode = prefs.theme_mode
        self.theme = theme_for_mode(self.theme_mode)
        self._theme_saved_prefs = None
        self._theme_draft_prefs = None
        self._theme_view_state = THEME_VIEW_MAIN
        self._custom_accent_colors = []
        self._picker_rgb = DEFAULT_ACCENT_RGB
        self._picker_editing_id = None
        self._picker_slider_channel = None
        self._boot_splash_started = time.monotonic()
        self._boot_splash_done = False
        self._paint_boot_splash_frame(animation_phase=0.0)
        self.font_lg = self._load_font(34)
        self.font_md = self._load_font(22)
        self.font_sm = self._load_font(18)

        self.backlight = BacklightController()
        self.backlight.restore_saved()

        self.scanner = PatchScanner(SURGE_PATCH_DIRS)
        self.loader = PatchLoader()
        self.scanner.bind_sidecar_loader(self.loader)
        self.surge_monitor = SurgeMonitor()
        self.cpu_monitor = SurgeCpuMonitor(self.surge_monitor)
        self.cpu_monitor.start()
        self.looper_monitor = LooperClockMonitor()
        self.looper_monitor.start()

        self.categories: list[str] = []
        self.all_patches_flat: list[dict] = []
        self.all_patches_letter_index: dict[str, int] = {}
        self.az_rail_rect = Rect(0, 0, 0, 0)
        self.az_rail_letter_rects: list[tuple[str, Rect]] = []
        self.nav_all_btn = Rect(0, 0, 0, 0)
        self.nav_current_btn = Rect(0, 0, 0, 0)
        self._all_patches_saved_scroll = 0.0
        self.browse_folder_index = 0
        self.browse_inner_segments: tuple[str, ...] = ()
        self.loaded_folder_index = 0
        self.loaded_inner_segments: tuple[str, ...] = ()
        self._browse_nav_entries: list[dict] = []
        self.detail_patch: dict | None = None
        self.loaded_patch_info: dict | None = None
        self.left_nav_mode = LeftNavMode.PATCHES
        self.left_nav_collapsed = False
        self.screen_state = Screen.BROWSER
        self.nav_folder_title_rect: Rect | None = None
        self._init_instrument_filter_state()
        self._init_context_menu_state()

        self.volume_level = self._load_volume_level()
        self.show_cpu_meter = self._load_ui_preference("show_cpu_meter", default=True)
        self.show_looper_hud = self._load_ui_preference("show_looper_hud", default=True)
        self.poly_governor_enabled = self._load_ui_preference("poly_governor_enabled", default=True)
        self.brightness_percent = self.backlight.get_percent()
        self.toast_message = ""
        self.toast_until = 0.0
        self.power_action: str | None = None
        self._pending_calibrate_mode: CalibrateMode = CalibrateMode.MISSING_ONLY
        self._slider_dragging = False
        self._dragging_mixer_id: str | None = None
        self._mixer_levels: dict[str, float] = {}
        self._mixer_last_tap_id: str | None = None
        self._mixer_last_tap_time = 0.0
        self._mixer_drag_origin: tuple[int, int] | None = None
        self._mixer_drag_moved = False
        self._pending_norm_toggle = False
        self._pending_favorites_toggle = False
        self.mixer_channels: list[MixerChannel] = []
        self._scan_dirty = False
        self._pending_last_patch: dict | None = None
        self._pending_load_next = 0.0
        self._pending_load_toast = True
        self._last_known_surge_pid: int | None = None
        self._surge_was_healthy = False
        self._surge_liveness_initialized = False
        self._surge_restart_btn: Rect | None = None
        self._settings_slide = 0.0
        self._settings_view = "root"
        self._settings_advanced_open = False
        self._settings_section_headers: list[tuple[Rect, str, bool]] = []
        self._settings_swipe_start: tuple[int, int] | None = None
        self._settings_pointer_down_pos: tuple[int, int] | None = None
        self._settings_pending_hit: str | None = None
        self._modal_pointer_down_pos: tuple[int, int] | None = None
        self._modal_pending_index: int | None = None
        self._modal_pending_key: str | None = None
        self._modal_panel_rect: Rect | None = None
        self._settings_content_scroll = ContentScrollArea(Rect(0, 0, 1, 1))
        self._theme_colors_scroll = ContentScrollArea(Rect(0, 0, 1, 1))
        self._theme_colors_scroll_capture = False
        self._touch_press = TouchPressState()
        self._settings_content_height = 0
        self._running = True
        self._scan_lock = threading.Lock()
        self._audio_profile_switching = False
        self._audio_profile_switch_target: str | None = None
        self._audio_profile_switch_started = 0.0
        self._audio_profile_result_queue: queue.SimpleQueue[tuple[bool, str]] = queue.SimpleQueue()
        self._surge_audio_switching = False
        self._surge_audio_switch_hint = ""
        self._surge_audio_switch_started = 0.0
        self._surge_audio_result_queue: queue.SimpleQueue[tuple[bool, str]] = queue.SimpleQueue()
        self._midi_sync_switching = False
        self._midi_sync_switch_started = 0.0
        self._midi_sync_result_queue: queue.SimpleQueue[tuple[bool, str]] = queue.SimpleQueue()
        self._wifi_busy = False
        self._wifi_connecting = False
        self._wifi_busy_hint = ""
        self._wifi_view = "list"
        self._wifi_networks: list = []
        self._wifi_scan_error: str | None = None
        self._wifi_password = ""
        self._wifi_key_flash_key: str | None = None
        self._wifi_key_flash_until = 0.0
        self._profile_switch_reload_active = False
        self._profile_switch_sent_once = False
        self._evdev_touch_queue: queue.SimpleQueue[tuple[str, tuple[int, int]]] = queue.SimpleQueue()
        self._evdev_bridge: TouchEvdevBridge | None = None
        self._touch_list_capture = False
        self._az_rail_capture = False
        self._az_rail_scrub_letter: str | None = None
        self._az_rail_active_letter: str | None = None
        self._az_rail_active_until = 0.0

        self._layout()
        self._bootstrap_patches()
        self._start_background_scan()
        self._wait_for_initial_scan()
        self._start_evdev_touch_bridge()

    def _load_font(self, size: int) -> pygame.font.Font:
        for name in ("dejavusans", "dejavusansmono", "liberationsans", "arial"):
            path = pygame.font.match_font(name)
            if path:
                return pygame.font.Font(path, size)
        return pygame.font.Font(None, size)
    def _clear_display(self) -> None:
        """Paint background immediately so stale DRM frames never show."""
        self.screen.fill(self.theme.bg)
        pygame.display.flip()

    def _paint_boot_splash_frame(self, *, animation_phase: float) -> None:
        draw_splash_frame(
            self.screen,
            mode=SplashMode.BOOT,
            theme=self.theme,
            animation_phase=animation_phase,
        )

    def _boot_splash_elapsed(self) -> float:
        return time.monotonic() - getattr(self, "_boot_splash_started", time.monotonic())

    def _tick_boot_splash_until_ready(self) -> bool:
        """Animate boot splash until min time elapsed. True when ready for first UI draw."""
        if self._boot_splash_done:
            return True
        elapsed = self._boot_splash_elapsed()
        if elapsed < BOOT_MIN_SECONDS:
            self._paint_boot_splash_frame(animation_phase=boot_animation_phase(elapsed))
            return False
        return True

    def _complete_boot_splash(self) -> None:
        """End boot splash after the first full UI frame is on screen."""
        if self._boot_splash_done:
            return
        self._boot_splash_done = True
        signal_browser_ready()
    def _pointer_move_distance(
        self, start: tuple[int, int] | None, end: tuple[int, int]
    ) -> float:
        if start is None:
            return 0.0
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        return (dx * dx + dy * dy) ** 0.5
    def _clear_settings_pointer(self) -> None:
        self._settings_pointer_down_pos = None
        self._settings_pending_hit = None
        self._touch_press.clear()

    def _clear_modal_pointer(self) -> None:
        self._modal_pointer_down_pos = None
        self._modal_pending_index = None
        self._modal_pending_key = None
        self._touch_press.clear()

    def _modal_press_hit(self, pos: tuple[int, int], hit: str | None) -> None:
        self._modal_pointer_down_pos = pos
        self._modal_pending_key = hit
        self._touch_press.set(hit)

    def _pressed(self, target_id: str) -> bool:
        return self._touch_press.is_pressed(target_id)
    def _toast(self, message: str, seconds: float = 2.0) -> None:
        self.toast_message = message
        self.toast_until = time.time() + seconds
    def _handle_screen_recorder_signals(self) -> None:
        if self._recorder_stop_requested:
            self._recorder_stop_requested = False
            if self._screen_recorder is not None:
                self._screen_recorder.close()
                self._screen_recorder = None
                print("Screen record stopped (SIGUSR2)", file=sys.stderr)
        if self._recorder_reload_requested:
            self._recorder_reload_requested = False
            if self._screen_recorder is not None:
                self._screen_recorder.close()
            self._screen_recorder = ScreenRecorder.from_env_file(DEFAULT_ENV_FILE)
            if self._screen_recorder is None:
                print(f"Screen record: no active config in {DEFAULT_ENV_FILE}", file=sys.stderr)

    def _signal_start_screen_record(self, _signum: int, _frame: object) -> None:
        self._recorder_reload_requested = True

    def _signal_stop_screen_record(self, _signum: int, _frame: object) -> None:
        self._recorder_stop_requested = True

    def run(self) -> None:
        signal.signal(signal.SIGUSR1, self._signal_start_screen_record)
        signal.signal(signal.SIGUSR2, self._signal_stop_screen_record)
        clock = pygame.time.Clock()
        print("Touch patch browser running.")
        print(f"Display: {self.width}x{self.height}")
        print(f"Quick Select folder: {favorites_display_name()} ({FAVORITES_NAME.lstrip('!')})")

        while self._running:
            if not self._boot_splash_done:
                if not self._tick_boot_splash_until_ready():
                    for event in pygame.event.get():
                        if event.type == pygame.QUIT:
                            self._running = False
                            break
                    clock.tick(30)
                    continue
                self._draw()
                self._complete_boot_splash()
                clock.tick(60)
                continue
            if self._scan_dirty and not (
                self.screen_state == Screen.BROWSER
                and not self.left_nav_collapsed
                and (
                    self.nav_list.is_interacting()
                    or getattr(self, "_az_rail_capture", False)
                )
            ):
                self._scan_dirty = False
                self._apply_scan_results()
            self._retry_pending_load()
            self._drain_evdev_touch_queue()
            self._poll_audio_profile_switch()
            self._poll_surge_audio_switch()
            self._poll_midi_sync_switch()
            self._poll_wifi_work()
            self._handle_screen_recorder_signals()
            for event in pygame.event.get():
                if self._ignore_sdl_pointer_event(event):
                    continue
                self._handle_event(event)
            dt = max(clock.get_time() / 1000.0, 1.0 / 120.0)
            self._tick_settings_animation(dt)
            if self.screen_state == Screen.SETTINGS or self._settings_slide > 0.004:
                self._settings_content_scroll.tick_edge_hints(dt)
            if self.screen_state == Screen.SETTINGS:
                self._settings_content_scroll.tick(dt)
            if (
                self.screen_state == Screen.THEME
                and self._theme_view() == THEME_VIEW_COLORS
            ):
                self._theme_colors_scroll.tick_edge_hints(dt)
                self._theme_colors_scroll.tick(dt)
            if self.screen_state == Screen.WIFI_MODAL and getattr(self, "_wifi_view", "list") == "list":
                scroll = getattr(self, "_wifi_scroll", None)
                if scroll is not None:
                    scroll.tick_edge_hints(dt)
                    scroll.tick(dt)
            if self.screen_state == Screen.SURGE_BUFFER_MODAL:
                scroll = getattr(self, "_surge_buffer_scroll", None)
                if scroll is not None:
                    scroll.tick_edge_hints(dt)
                    scroll.tick(dt)
            if self.screen_state == Screen.BROWSER and not self.left_nav_collapsed:
                self.nav_list.tick(dt)
                self._tick_long_press()
            self._draw()
            clock.tick(60)

        if self._evdev_bridge is not None:
            self._evdev_bridge.stop()
        if self._screen_recorder is not None:
            self._screen_recorder.close()
        self.cpu_monitor.stop()
        pygame.quit()


def _exit_on_signal(signum: int, *_args: object) -> None:
    log_shutdown_event("browser_signal_exit", signal=signum)
    sys.exit(0)


def main() -> None:
    signal.signal(signal.SIGTERM, _exit_on_signal)
    signal.signal(signal.SIGINT, _exit_on_signal)
    TouchPatchBrowser().run()


if __name__ == "__main__":
    main()
