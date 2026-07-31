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
    clear_browser_ready_flag,
    draw_splash_frame,
    signal_browser_ready,
)
from patch_browser.ui_theme import load_theme_mode_from_prefs, theme_for_mode


class TouchPatchBrowser(
    TouchBrowserEvdevMixin,
    TouchBrowserPrefsMixin,
    TouchBrowserLayoutMixin,
    TouchBrowserPatchesMixin,
    TouchBrowserMixerMixin,
    TouchBrowserNormalizationMixin,
    TouchBrowserDrawMixin,
    TouchBrowserInputMixin,
):
    """Fullscreen touch patch browser."""

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

    def _paint_boot_splash_frame(self, *, progress: float) -> None:
        draw_splash_frame(
            self.screen,
            mode=SplashMode.BOOT,
            theme=self.theme,
            progress=progress,
        )

    def _finish_boot_splash(self) -> None:
        """Hold branded boot frames until minimum time and first scan settle."""
        start = getattr(self, "_boot_splash_started", time.monotonic())
        clock = pygame.time.Clock()
        while True:
            elapsed = time.monotonic() - start
            progress = min(1.0, elapsed / BOOT_MIN_SECONDS)
            self._paint_boot_splash_frame(progress=progress)
            if elapsed >= BOOT_MIN_SECONDS:
                break
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self._running = False
                    return
            clock.tick(30)
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
    def _toast(self, message: str, seconds: float = 2.0) -> None:
        self.toast_message = message
        self.toast_until = time.time() + seconds
    def run(self) -> None:
        signal_browser_ready()
        clock = pygame.time.Clock()
        print("Touch patch browser running.")
        print(f"Display: {self.width}x{self.height}")
        print(f"Quick Select folder: {favorites_display_name()} ({FAVORITES_NAME.lstrip('!')})")

        while self._running:
            if self._scan_dirty and not (
                self.screen_state == Screen.BROWSER
                and not self.left_nav_collapsed
                and self.nav_list.is_interacting()
            ):
                self._scan_dirty = False
                self._apply_scan_results()
            self._retry_pending_load()
            self._drain_evdev_touch_queue()
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
    def __init__(self) -> None:
        clear_browser_ready_flag()
        windowed = os.environ.get("MPE_TOUCH_WINDOWED") == "1"
        if windowed:
            pygame.init()
            pygame.display.set_caption("Pi-Surge-MPE Touch Browser")
            self.screen = pygame.display.set_mode((800, 480))
        else:
            self.screen = acquire_browser_display()
            pygame.display.set_caption("Pi-Surge-MPE Touch Browser")
        self.width, self.height = self.screen.get_size()
        pygame.mouse.set_visible(False)
        self.theme_mode = load_theme_mode_from_prefs()
        self.theme = theme_for_mode(self.theme_mode)
        self._boot_splash_started = time.monotonic()
        self._paint_boot_splash_frame(progress=0.05)
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
        self._brightness_last_tap_time = 0.0
        self._brightness_drag_moved = False
        self.mixer_channels: list[MixerChannel] = []
        self._scan_dirty = False
        self._pending_last_patch: dict | None = None
        self._pending_load_next = 0.0
        self._surge_restart_btn: Rect | None = None
        self._settings_slide = 0.0
        self._settings_swipe_start: tuple[int, int] | None = None
        self._settings_pointer_down_pos: tuple[int, int] | None = None
        self._settings_pending_hit: str | None = None
        self._modal_pointer_down_pos: tuple[int, int] | None = None
        self._modal_pending_index: int | None = None
        self._settings_content_scroll = ContentScrollArea(Rect(0, 0, 1, 1))
        self._settings_content_height = 0
        self._running = True
        self._scan_lock = threading.Lock()
        self._evdev_touch_queue: queue.SimpleQueue[tuple[str, tuple[int, int]]] = queue.SimpleQueue()
        self._evdev_bridge: TouchEvdevBridge | None = None
        self._touch_list_capture = False

        self._layout()
        self._bootstrap_patches()
        self._start_background_scan()
        self._wait_for_initial_scan()
        self._finish_boot_splash()
        self._start_evdev_touch_bridge()


def main() -> None:
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
    TouchPatchBrowser().run()


if __name__ == "__main__":
    main()
