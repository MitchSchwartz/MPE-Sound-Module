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
from patch_browser.surge_cpu_monitor import SurgeCpuMonitor
from patch_browser.surge_monitor import SurgeMonitor
from patch_browser.touch_evdev import TouchEvdevBridge, evdev_bridge_enabled
from patch_browser.touch_browser_draw import TouchBrowserDrawMixin
from patch_browser.touch_browser_evdev import TouchBrowserEvdevMixin
from patch_browser.touch_browser_input import TouchBrowserInputMixin
from patch_browser.touch_browser_layout import TouchBrowserLayoutMixin
from patch_browser.touch_browser_hold import TouchBrowserHoldMixin
from patch_browser.touch_browser_touch import TouchBrowserTouchMixin
from patch_browser.touch_browser_mixer import TouchBrowserMixerMixin
from patch_browser.touch_browser_normalization import TouchBrowserNormalizationMixin
from patch_browser.touch_browser_patches import TouchBrowserPatchesMixin
from patch_browser.touch_browser_prefs import TouchBrowserPrefsMixin
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
from patch_browser.ui_theme import DEFAULT_ACCENT_RGB, THEME_VIEW_MAIN, reload_theme_from_prefs, theme_for_mode


class TouchPatchBrowser(
    TouchBrowserEvdevMixin,
    TouchBrowserPrefsMixin,
    TouchBrowserLayoutMixin,
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
        self.surge_monitor = SurgeMonitor()
        self.cpu_monitor = SurgeCpuMonitor(self.surge_monitor)
        self.cpu_monitor.start()

        self.categories: list[str] = []
        self.all_patches_flat: list[dict] = []
        self.all_patches_letter_index: dict[str, int] = {}
        self.az_rail_rect = Rect(0, 0, 0, 0)
        self.az_rail_letter_rects: list[tuple[str, Rect]] = []
        self.nav_all_btn = Rect(0, 0, 0, 0)
        self.nav_current_btn = Rect(0, 0, 0, 0)
        self._all_patches_saved_scroll = 0.0
        self.browse_folder_index = 0
        self.loaded_folder_index = 0
        self.detail_patch: dict | None = None
        self.loaded_patch_info: dict | None = None
        self.left_nav_mode = LeftNavMode.PATCHES
        self.left_nav_collapsed = False
        self.screen_state = Screen.BROWSER
        self.nav_folder_title_rect: Rect | None = None

        self.volume_level = self._load_volume_level()
        self.show_cpu_meter = self._load_ui_preference("show_cpu_meter", default=True)
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
        self._brightness_last_tap_time = 0.0
        self._brightness_drag_moved = False
        self.mixer_channels: list[MixerChannel] = []
        self._scan_dirty = False
        self._pending_last_patch: dict | None = None
        self._pending_load_next = 0.0
        self._last_known_surge_pid: int | None = None
        self._surge_was_healthy = False
        self._surge_liveness_initialized = False
        self._surge_restart_btn: Rect | None = None
        self._settings_slide = 0.0
        self._settings_swipe_start: tuple[int, int] | None = None
        self._settings_pointer_down_pos: tuple[int, int] | None = None
        self._settings_pending_hit: str | None = None
        self._modal_pointer_down_pos: tuple[int, int] | None = None
        self._modal_pending_index: int | None = None
        self._modal_pending_key: str | None = None
        self._settings_content_scroll = ContentScrollArea(Rect(0, 0, 1, 1))
        self._settings_content_height = 0
        self._running = True
        self._scan_lock = threading.Lock()
        self._audio_profile_switching = False
        self._audio_profile_switch_target: str | None = None
        self._audio_profile_switch_started = 0.0
        self._audio_profile_result_queue: queue.SimpleQueue[tuple[bool, str]] = queue.SimpleQueue()
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
    def _clear_modal_pointer(self) -> None:
        self._modal_pointer_down_pos = None
        self._modal_pending_index = None
        self._modal_pending_key = None
    def _toast(self, message: str, seconds: float = 2.0) -> None:
        self.toast_message = message
        self.toast_until = time.time() + seconds
    def run(self) -> None:
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
            for event in pygame.event.get():
                if self._ignore_sdl_pointer_event(event):
                    continue
                self._handle_event(event)
            dt = max(clock.get_time() / 1000.0, 1.0 / 120.0)
            self._tick_settings_animation(dt)
            if self.screen_state == Screen.SETTINGS:
                self._settings_content_scroll.tick(dt)
            if self.screen_state == Screen.BROWSER and not self.left_nav_collapsed:
                self.nav_list.tick(dt)
            self._draw()
            clock.tick(60)

        if self._evdev_bridge is not None:
            self._evdev_bridge.stop()
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
