#!/usr/bin/env python3
"""
Pi-Surge-MPE Touch Patch Browser

Fullscreen touch UI for ~5" landscape displays (SmartiPi case + panel, DSI or HDMI).
Default layout target: 800×480 landscape — most common 5" panel size.
"""

from __future__ import annotations

import json
import math
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

try:
    import pygame
except ImportError as exc:
    print("FATAL: pygame is required for the touch patch browser.")
    print("On the Pi: sudo apt install python3-pygame")
    print("Or: pip3 install pygame")
    raise SystemExit(1) from exc

from patch_browser.backlight import BacklightController
from patch_browser.touch_evdev import TouchEvdevBridge, evdev_bridge_enabled
from patch_browser.patch_loader import PatchLoader
from patch_browser.patch_scanner import (
    FAVORITES_NAME,
    PatchScanner,
    SURGE_PATCH_DIRS,
    favorites_display_name,
)
from patch_browser.surge_monitor import SurgeMonitor
from patch_browser.surge_cpu_monitor import SurgeCpuMonitor

TAP_MOVE_THRESHOLD_PX = 14
VOLUME_STATE_FILE = Path.home() / ".patch_browser_volume.json"
VOLUME_MIN = 0.25
VOLUME_MAX = 1.5
LEFT_NAV_WIDTH = 268
LEFT_NAV_COLLAPSED_WIDTH = 36
FADER_COLUMN_W = 54
FADER_TRACK_W = 10
FADER_HANDLE_W = 46
FADER_HANDLE_H = 24
FADER_TRACK_H = 168
NORM_ROW_W = 128
NORM_ROW_H = 40
NORM_CHECKBOX_SIZE = 22
NAV_FOLDER_TITLE_H = 34
SCROLL_DRAG_THRESHOLD_PX = 10
SCROLL_DRAG_THRESHOLD_CATCH_PX = 5  # lower bar when finger lands during momentum coast
SCROLL_VELOCITY_DRAG_PX_S = 220.0  # skip distance threshold when finger is already moving
SCROLL_FRICTION = 2.8  # lower = longer coast (1/s)
SCROLL_MIN_VELOCITY = 12.0  # px/s
SCROLL_VELOCITY_CAP = 3200.0
SCROLL_SAMPLE_WINDOW_S = 0.12
MIXER_DOUBLE_TAP_MS = 400
MIXER_DRAG_THRESHOLD_PX = 10
DEFAULT_VOLUME = 1.0
DEFAULT_BRIGHTNESS_PERCENT = 100
CPU_METER_W = 56
CPU_METER_H = 20
CPU_METER_BAR_H = 6


class Screen(Enum):
    BROWSER = auto()
    SETTINGS = auto()
    CALIBRATE_CONFIRM = auto()
    POWER_MENU = auto()
    POWER_CONFIRM = auto()


class CalibrateMode(Enum):
    """Which normalization calibration scope to run from System settings."""

    MISSING_ONLY = auto()
    FORCE_FULL = auto()


class LeftNavMode(Enum):
    FOLDERS = auto()
    PATCHES = auto()


from patch_browser.ui_theme import Theme
@dataclass
class Rect:
    x: int
    y: int
    w: int
    h: int

    @property
    def left(self) -> int:
        return self.x

    @property
    def top(self) -> int:
        return self.y

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h

    @property
    def centerx(self) -> int:
        return self.x + self.w // 2

    @property
    def centery(self) -> int:
        return self.y + self.h // 2

    @property
    def pygame_rect(self) -> pygame.Rect:
        return pygame.Rect(self.x, self.y, self.w, self.h)

    def contains(self, px: int, py: int) -> bool:
        return self.x <= px < self.x + self.w and self.y <= py < self.y + self.h


class ScrollList:
    """Touch-scrollable list with tap vs scroll discrimination and inertial momentum."""

    def __init__(self, rect: Rect, row_height: int = 56, padding: int = 8):
        self.rect = rect
        self.row_height = row_height
        self.padding = padding
        self.items: list[str] = []
        self.highlight_index: int | None = None
        self.loaded_marker_index: int | None = None
        self.scroll_offset = 0
        self._scroll_pixels = 0.0
        self._drag_start_y: int | None = None
        self._drag_scroll_pixels_start = 0.0
        self._pointer_down_pos: tuple[int, int] | None = None
        self._pointer_scrolled = False
        self._velocity = 0.0
        self._momentum_active = False
        self._last_motion_y: int | None = None
        self._last_motion_time = 0.0
        self._drag_start_time = 0.0
        self._scroll_samples: list[tuple[float, float]] = []
        self._pending_tap_index: int | None = None
        self._was_momentum_on_down = False

    def take_tap_index(self) -> int | None:
        idx = self._pending_tap_index
        self._pending_tap_index = None
        return idx

    def is_interacting(self) -> bool:
        return self._drag_start_y is not None or self._momentum_active

    def is_dragging(self) -> bool:
        return self._drag_start_y is not None

    def pointer_down(self, pos: tuple[int, int]) -> bool:
        """Begin tracking if touch is inside the list."""
        if not self.rect.contains(*pos):
            return False
        was_momentum = self._momentum_active
        self.stop_momentum()
        self._clear_pointer()
        self._was_momentum_on_down = was_momentum
        self._pointer_down_pos = pos
        self._drag_start_y = pos[1]
        self._drag_scroll_pixels_start = self._scroll_pixels
        self._last_motion_y = pos[1]
        now = time.time()
        self._last_motion_time = now
        self._drag_start_time = now
        self._scroll_samples = [(now, self._scroll_pixels)]
        return True

    def pointer_move(self, pos: tuple[int, int]) -> bool:
        if self._drag_start_y is None:
            return False
        if not self._pointer_scrolled:
            move = self._pointer_move_distance(pos)
            threshold = (
                SCROLL_DRAG_THRESHOLD_CATCH_PX
                if self._was_momentum_on_down
                else SCROLL_DRAG_THRESHOLD_PX
            )
            now = time.time()
            velocity_bypass = False
            if self._last_motion_y is not None:
                motion_dt = now - self._last_motion_time
                if motion_dt > 0:
                    instant_v = abs(pos[1] - self._last_motion_y) / motion_dt
                    if instant_v >= SCROLL_VELOCITY_DRAG_PX_S:
                        velocity_bypass = True
            down_dt = now - self._drag_start_time
            if down_dt > 0.008:
                avg_v = abs(pos[1] - self._drag_start_y) / down_dt
                if avg_v >= SCROLL_VELOCITY_DRAG_PX_S:
                    velocity_bypass = True
            if move <= threshold and not velocity_bypass:
                return True
            self._pointer_scrolled = True

        delta = pos[1] - self._drag_start_y
        self._scroll_pixels = self._drag_scroll_pixels_start - float(delta)
        self._clamp_scroll()
        self._record_scroll_sample()

        now = time.time()
        if self._last_motion_y is not None:
            motion_dt = now - self._last_motion_time
            if motion_dt > 0:
                instant_v = -(pos[1] - self._last_motion_y) / motion_dt
                self._velocity = max(
                    -SCROLL_VELOCITY_CAP,
                    min(
                        SCROLL_VELOCITY_CAP,
                        self._velocity * 0.55 + instant_v * 0.45,
                    ),
                )
        self._last_motion_y = pos[1]
        self._last_motion_time = now
        return True

    def pointer_up(self, pos: tuple[int, int]) -> int | None:
        """End gesture; return tapped row index or None if scroll/miss."""
        self._pending_tap_index = None
        if self._drag_start_y is None and self._pointer_down_pos is None:
            return None

        if self._drag_start_y is not None:
            release_v = self._release_velocity()
            if self._pointer_scrolled and abs(release_v) >= SCROLL_MIN_VELOCITY:
                self._velocity = release_v
                self._momentum_active = True
            else:
                self.stop_momentum()
            self._drag_start_y = None
            self._last_motion_y = None

        if self._momentum_active:
            self._clear_pointer()
            return None
        if self._pointer_down_pos is None:
            return None
        if self._pointer_scrolled:
            self._clear_pointer()
            return None
        if not self.rect.contains(*self._pointer_down_pos):
            self._clear_pointer()
            return None
        index = self.item_at(*self._pointer_down_pos)
        self._pending_tap_index = index
        self._clear_pointer()
        return index

    def set_items(
        self,
        items: list[str],
        highlight_index: int | None = None,
        loaded_marker_index: int | None = None,
        *,
        preserve_scroll: bool = True,
    ) -> None:
        self.items = items
        self.highlight_index = highlight_index
        self.loaded_marker_index = loaded_marker_index
        if preserve_scroll:
            self._scroll_pixels = max(0.0, min(self._scroll_pixels, self._max_scroll_pixels()))
            self._sync_scroll_offset()
        else:
            self._scroll_pixels = 0.0
            self.stop_momentum()
            self._sync_scroll_offset()

    def stop_momentum(self) -> None:
        self._velocity = 0.0
        self._momentum_active = False

    def _pointer_move_distance(self, pos: tuple[int, int]) -> float:
        if self._pointer_down_pos is None:
            return 0.0
        dx = pos[0] - self._pointer_down_pos[0]
        dy = pos[1] - self._pointer_down_pos[1]
        return (dx * dx + dy * dy) ** 0.5

    def _clear_pointer(self) -> None:
        self._pointer_down_pos = None
        self._pointer_scrolled = False
        self._drag_start_y = None
        self._last_motion_y = None
        self._was_momentum_on_down = False
        self._scroll_samples.clear()

    def _record_scroll_sample(self) -> None:
        now = time.time()
        self._scroll_samples.append((now, self._scroll_pixels))
        cutoff = now - SCROLL_SAMPLE_WINDOW_S
        self._scroll_samples = [(t, s) for t, s in self._scroll_samples if t >= cutoff]

    def _release_velocity(self) -> float:
        now = time.time()
        self._record_scroll_sample()
        if len(self._scroll_samples) >= 2:
            t0, s0 = self._scroll_samples[0]
            t1, s1 = self._scroll_samples[-1]
            dt = t1 - t0
            if dt > 0.008:
                return max(
                    -SCROLL_VELOCITY_CAP,
                    min(SCROLL_VELOCITY_CAP, (s1 - s0) / dt),
                )
        return self._velocity

    def tick(self, dt: float) -> bool:
        """Advance inertial scroll. Returns True if scroll position changed."""
        if not self._momentum_active:
            return False
        dt = max(dt, 1.0 / 120.0)

        before = self._scroll_pixels
        self._scroll_pixels += self._velocity * dt
        self._clamp_scroll()

        if self._scroll_pixels != before and (
            self._scroll_pixels <= 0.0 or self._scroll_pixels >= self._max_scroll_pixels()
        ):
            self._velocity *= 0.35

        self._velocity *= math.exp(-SCROLL_FRICTION * dt)
        if abs(self._velocity) < SCROLL_MIN_VELOCITY:
            self.stop_momentum()
        return self._scroll_pixels != before or self._momentum_active

    def visible_count(self) -> int:
        inner_h = self.rect.h - self.padding * 2
        return max(1, inner_h // self.row_height)

    def _max_scroll(self) -> int:
        return max(0, len(self.items) - self.visible_count())

    def _max_scroll_pixels(self) -> float:
        return float(self._max_scroll() * self.row_height)

    def _sync_scroll_offset(self) -> None:
        maximum = self._max_scroll()
        row = int(self._scroll_pixels // self.row_height)
        self.scroll_offset = max(0, min(maximum, row))

    def _clamp_scroll(self) -> None:
        max_pixels = self._max_scroll_pixels()
        self._scroll_pixels = max(0.0, min(self._scroll_pixels, max_pixels))
        self._sync_scroll_offset()

    def item_at(self, px: int, py: int) -> int | None:
        if not self.rect.contains(px, py) or not self.items:
            return None
        local_y = py - self.rect.y - self.padding + self._scroll_pixels
        index = int(local_y // self.row_height)
        if 0 <= index < len(self.items):
            return index
        return None

    def scroll_to_index(self, index: int) -> None:
        if not self.items:
            return
        index = max(0, min(index, len(self.items) - 1))
        visible = self.visible_count()
        if index < self.scroll_offset:
            self.scroll_offset = index
        elif index >= self.scroll_offset + visible:
            self.scroll_offset = index - visible + 1
        self._scroll_pixels = float(self.scroll_offset * self.row_height)
        self.stop_momentum()
        self._clamp_scroll()

    def handle_event(self, event: pygame.event.Event) -> bool:
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            return self.pointer_down(event.pos)
        if event.type == pygame.MOUSEMOTION:
            return self.pointer_move(event.pos)
        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            # Mouse path only — caller runs consume_tap separately.
            if self._drag_start_y is None and self._pointer_down_pos is None:
                return False
            self.pointer_up(event.pos)
            return True
        if event.type == pygame.MOUSEWHEEL:
            if self.rect.contains(*pygame.mouse.get_pos()):
                self.stop_momentum()
                self._scroll_pixels -= event.y * self.row_height
                self._clamp_scroll()
                return True
        return False

    def draw(self, surface: pygame.Surface, font: pygame.font.Font, theme: Theme) -> None:
        pygame.draw.rect(surface, theme.surface, self.rect.pygame_rect, border_radius=10)
        clip = surface.get_clip()
        surface.set_clip(self.rect.pygame_rect)

        if not self.items:
            surface.set_clip(clip)
            return

        start = int(self._scroll_pixels // self.row_height)
        sub_pixel = self._scroll_pixels - start * self.row_height
        end = min(len(self.items), start + self.visible_count() + 3)
        y = self.rect.y + self.padding - int(sub_pixel)

        for index in range(start, end):
            label = self.items[index]
            row_rect = pygame.Rect(self.rect.x + 4, y, self.rect.w - 8, self.row_height - 4)
            is_highlight = self.highlight_index == index
            is_loaded = self.loaded_marker_index == index
            if is_highlight or is_loaded:
                pygame.draw.rect(surface, theme.surface_alt, row_rect, border_radius=8)

            text_color = theme.text if is_highlight or is_loaded else theme.muted
            text = font.render(label[:40], True, text_color)
            surface.blit(text, (row_rect.x + 10, row_rect.y + 10))

            if is_loaded:
                pygame.draw.circle(surface, theme.playing, (row_rect.right - 16, row_rect.centery), 5)

            y += self.row_height

        surface.set_clip(clip)


@dataclass
class MixerChannel:
    """Vertical mixing-board fader column."""

    channel_id: str
    label: str
    min_value: float
    max_value: float
    enabled: bool
    column_rect: Rect
    track_rect: Rect


class TouchPatchBrowser:
    """Fullscreen touch patch browser."""

    def __init__(self) -> None:
        pygame.init()
        pygame.display.set_caption("Pi-Surge-MPE Touch Browser")
        windowed = os.environ.get("MPE_TOUCH_WINDOWED") == "1"
        if windowed:
            self.screen = pygame.display.set_mode((800, 480))
        else:
            self.screen = pygame.display.set_mode((800, 480), pygame.FULLSCREEN)
        self.width, self.height = self.screen.get_size()
        pygame.mouse.set_visible(False)
        self.theme = Theme()
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
        self._running = True
        self._scan_lock = threading.Lock()
        self._evdev_touch_queue: queue.SimpleQueue[tuple[str, tuple[int, int]]] = queue.SimpleQueue()
        self._evdev_bridge: TouchEvdevBridge | None = None
        self._touch_list_capture = False

        self._layout()
        self._bootstrap_patches()
        self._start_background_scan()
        self._wait_for_initial_scan()
        self._start_evdev_touch_bridge()

    def _start_evdev_touch_bridge(self) -> None:
        if not evdev_bridge_enabled():
            return

        def enqueue(kind: str, pos: tuple[int, int]) -> None:
            self._evdev_touch_queue.put((kind, pos))

        bridge = TouchEvdevBridge(
            self.width,
            self.height,
            on_down=lambda pos: enqueue("down", pos),
            on_up=lambda pos: enqueue("up", pos),
            on_motion=lambda pos: enqueue("motion", pos),
        )
        if bridge.start():
            self._evdev_bridge = bridge

    def _drain_evdev_touch_queue(self) -> None:
        while True:
            try:
                kind, pos = self._evdev_touch_queue.get_nowait()
            except queue.Empty:
                break
            if self.screen_state == Screen.BROWSER:
                self._handle_evdev_browser_touch(kind, pos)
            else:
                self._inject_evdev_pointer_event(kind, pos)

    def _inject_evdev_pointer_event(self, kind: str, pos: tuple[int, int]) -> None:
        if kind == "down":
            event = pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"pos": pos, "button": 1})
        elif kind == "up":
            event = pygame.event.Event(pygame.MOUSEBUTTONUP, {"pos": pos, "button": 1})
        else:
            event = pygame.event.Event(
                pygame.MOUSEMOTION,
                {"pos": pos, "rel": (0, 0), "buttons": (1, 0, 0)},
            )
        self._handle_event(event)

    def _handle_evdev_browser_touch(self, kind: str, pos: tuple[int, int]) -> None:
        if kind == "down":
            self._touch_list_capture = False
            if not self.left_nav_collapsed and self.nav_list.pointer_down(pos):
                self._touch_list_capture = True
            else:
                self._handle_mixer_down(pos)
        elif kind == "motion":
            if self._touch_list_capture or self.nav_list.is_dragging():
                self.nav_list.pointer_move(pos)
            else:
                self._handle_mixer_motion(pos)
        elif kind == "up":
            was_mixer = self._dragging_mixer_id is not None
            if was_mixer and self._mixer_drag_moved:
                self._mixer_last_tap_id = None
            self._dragging_mixer_id = None
            self._mixer_drag_origin = None
            self._mixer_drag_moved = False

            list_gesture = self._touch_list_capture or self.nav_list.is_dragging()
            if not self.left_nav_collapsed and list_gesture:
                idx = self.nav_list.pointer_up(pos)
                if idx is not None:
                    self._select_nav_index(idx)
            elif not was_mixer:
                self._handle_browser_tap(pos)
            self._touch_list_capture = False

    def _select_nav_index(self, idx: int) -> None:
        if self.left_nav_mode == LeftNavMode.FOLDERS:
            self._enter_folder(idx)
        else:
            patches = self._patches_in_browse_folder()
            if idx < len(patches):
                self._select_patch(patches[idx])

    def _load_font(self, size: int) -> pygame.font.Font:
        for name in ("dejavusans", "dejavusansmono", "liberationsans", "arial"):
            path = pygame.font.match_font(name)
            if path:
                return pygame.font.Font(path, size)
        return pygame.font.Font(None, size)

    def _load_volume_level(self) -> float:
        if VOLUME_STATE_FILE.exists():
            try:
                data = json.loads(VOLUME_STATE_FILE.read_text())
                level = float(data.get("volume", 1.0))
                return max(VOLUME_MIN, min(VOLUME_MAX, level))
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                pass
        return 1.0

    def _save_volume_level(self) -> None:
        try:
            VOLUME_STATE_FILE.write_text(json.dumps({"volume": self.volume_level}, indent=2))
        except OSError as exc:
            print(f"Warning: could not persist volume ({exc})")

    def _apply_volume(self, level: float, persist: bool = True) -> None:
        self.volume_level = max(VOLUME_MIN, min(VOLUME_MAX, level))
        if self.loader.osc_enabled:
            self.loader.set_volume(self.volume_level)
        if persist:
            self._save_volume_level()

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
        self.cpu_meter_rect = Rect(
            self.system_settings_btn.x - CPU_METER_W - 8,
            self.status_rect.y + 6,
            CPU_METER_W,
            CPU_METER_H,
        )

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

        settings_w = min(420, self.width - margin * 2)
        settings_h = min(520, self.height - margin * 2)
        self.settings_rect = Rect(
            (self.width - settings_w) // 2,
            (self.height - settings_h) // 2,
            settings_w,
            settings_h,
        )
        self.brightness_slider_rect = Rect(
            self.settings_rect.x + 24,
            self.settings_rect.y + 120,
            self.settings_rect.w - 48,
            36,
        )
        self.norm_global_toggle_rect = Rect(
            self.settings_rect.x + 24,
            self.settings_rect.y + 178,
            self.settings_rect.w - 48,
            NORM_ROW_H,
        )

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

    def _bootstrap_patches(self) -> None:
        last = self.scanner.load_last_patch()
        if last:
            category_path = Path(last["patch_path"]).parent
            quick = self.scanner.quick_scan_category(category_path)
            if quick:
                self.scanner.patches[last["category"]] = quick
                self.categories = [last["category"]]
                self.browse_folder_index = 0
                self.loaded_folder_index = 0
                self.left_nav_mode = LeftNavMode.PATCHES
                self.detail_patch = {
                    "name": Path(last["patch_path"]).stem,
                    "category": last["category"],
                    "path": last["patch_path"],
                }
                if self._try_load_patch_path(last["patch_path"], last["category"]):
                    self.loaded_patch_info = dict(self.detail_patch)
                    self._apply_volume(self.volume_level, persist=False)
                else:
                    self._pending_last_patch = dict(self.detail_patch)
                    self._pending_load_next = time.time() + 2.0
        self._refresh_lists(scroll_to_selection=True)

    def _try_load_patch_path(self, patch_path: str, category: str) -> bool:
        if not self.loader.osc_enabled:
            return False
        return bool(self.loader.load_patch(patch_path))

    def _retry_pending_load(self) -> None:
        if not self._pending_last_patch or time.time() < self._pending_load_next:
            return
        patch = self._pending_last_patch
        if self._try_load_patch_path(patch["path"], patch["category"]):
            self.loaded_patch_info = dict(patch)
            self.detail_patch = dict(patch)
            self._pending_last_patch = None
            self._apply_volume(self.volume_level, persist=False)
            self._refresh_lists(scroll_to_selection=True)
            self._toast("Patch loaded", 1.5)
        else:
            self._pending_load_next = time.time() + 2.0

    def _apply_scan_results(self) -> None:
        with self._scan_lock:
            self.categories = self.scanner.get_categories()
            if self.loaded_patch_info:
                try:
                    self.loaded_folder_index = self.categories.index(
                        self.loaded_patch_info["category"]
                    )
                except ValueError:
                    pass
        self._refresh_lists()
        if not self.categories:
            self._toast("No patches found — check Surge patch symlinks", 4.0)

    def _start_background_scan(self) -> None:
        def worker() -> None:
            self.scanner.scan_patches_background()
            self.scanner.wait_for_scan(timeout=120)
            self._scan_dirty = True

        threading.Thread(target=worker, daemon=True, name="TouchScanSync").start()

    def _wait_for_initial_scan(self) -> None:
        if self.scanner.scan_complete.is_set():
            self._apply_scan_results()
            return
        if self.scanner.wait_for_scan(timeout=5.0):
            self._apply_scan_results()
        else:
            print("Patch scan still running — list will update when complete")

    def _browse_category_name(self) -> str:
        if not self.categories:
            return "(No patches)"
        return self.categories[self.browse_folder_index]

    def _patches_in_browse_folder(self) -> list[dict]:
        if not self.categories:
            return []
        return self.scanner.get_patches_in_category(self._browse_category_name())

    def _show_current_folder_button(self) -> bool:
        return (
            self.loaded_patch_info is not None
            and self.browse_folder_index != self.loaded_folder_index
        )

    def _refresh_lists(self, *, scroll_to_selection: bool = False) -> None:
        if self.left_nav_collapsed:
            return

        saved_scroll = self.nav_list._scroll_pixels
        saved_velocity = self.nav_list._velocity
        saved_momentum = self.nav_list._momentum_active

        self.nav_list.row_height = 50 if self.left_nav_mode == LeftNavMode.PATCHES else 44

        if self.left_nav_mode == LeftNavMode.FOLDERS:
            loaded_idx = self.loaded_folder_index if self.loaded_patch_info else None
            self.nav_list.set_items(
                self.categories,
                highlight_index=self.browse_folder_index,
                loaded_marker_index=loaded_idx,
            )
            if scroll_to_selection:
                self.nav_list.scroll_to_index(self.browse_folder_index)
            else:
                self.nav_list._scroll_pixels = min(saved_scroll, self.nav_list._max_scroll_pixels())
                self.nav_list._sync_scroll_offset()
                if saved_momentum:
                    self.nav_list._velocity = saved_velocity
                    self.nav_list._momentum_active = True
        else:
            patches = self._patches_in_browse_folder()
            names = [p["name"] for p in patches]
            highlight = None
            loaded_idx = None
            if self.detail_patch and self.detail_patch.get("category") == self._browse_category_name():
                for i, patch in enumerate(patches):
                    if patch["name"] == self.detail_patch["name"]:
                        highlight = i
                        break
            if self.loaded_patch_info and self.loaded_patch_info.get("category") == self._browse_category_name():
                for i, patch in enumerate(patches):
                    if patch["name"] == self.loaded_patch_info["name"]:
                        loaded_idx = i
                        break
            self.nav_list.set_items(names, highlight_index=highlight, loaded_marker_index=loaded_idx)
            if scroll_to_selection:
                if highlight is not None:
                    self.nav_list.scroll_to_index(highlight)
                elif loaded_idx is not None:
                    self.nav_list.scroll_to_index(loaded_idx)
            else:
                self.nav_list._scroll_pixels = min(saved_scroll, self.nav_list._max_scroll_pixels())
                self.nav_list._sync_scroll_offset()
                if saved_momentum:
                    self.nav_list._velocity = saved_velocity
                    self.nav_list._momentum_active = True

    def _toast(self, message: str, seconds: float = 2.0) -> None:
        self.toast_message = message
        self.toast_until = time.time() + seconds

    def _load_patch(self, patch: dict) -> None:
        if not self.loader.osc_enabled:
            self._toast("OSC unavailable", 3)
            return
        if self.loader.load_patch(patch["path"]):
            self.loaded_patch_info = {
                "name": patch["name"],
                "category": patch["category"],
                "path": patch["path"],
            }
            self.detail_patch = dict(self.loaded_patch_info)
            self._pending_last_patch = None
            self.scanner.save_last_patch(patch["category"], patch["path"])
            try:
                self.loaded_folder_index = self.categories.index(patch["category"])
            except ValueError:
                pass
            self._apply_volume(self.volume_level, persist=False)
        else:
            self._toast("Load failed", 3)

    def _enter_folder(self, index: int) -> None:
        if not self.categories:
            return
        self.browse_folder_index = max(0, min(index, len(self.categories) - 1))
        self.left_nav_mode = LeftNavMode.PATCHES
        self._update_nav_list_geometry()
        self._refresh_lists()
        self.nav_list._scroll_pixels = 0.0
        self.nav_list.stop_momentum()
        self.nav_list._clamp_scroll()

    def _go_up_to_folders(self) -> None:
        self.left_nav_mode = LeftNavMode.FOLDERS
        self._update_nav_list_geometry()
        self._refresh_lists(scroll_to_selection=True)

    def _go_to_loaded_folder(self) -> None:
        if not self.loaded_patch_info:
            return
        try:
            idx = self.categories.index(self.loaded_patch_info["category"])
        except ValueError:
            return
        self.browse_folder_index = idx
        self.left_nav_mode = LeftNavMode.PATCHES
        self._update_nav_list_geometry()
        self._refresh_lists(scroll_to_selection=True)

    def _toggle_nav_collapsed(self) -> None:
        self.left_nav_collapsed = not self.left_nav_collapsed
        self._relayout()

    def _select_patch(self, patch: dict) -> None:
        self.left_nav_mode = LeftNavMode.PATCHES
        try:
            self.browse_folder_index = self.categories.index(patch["category"])
        except ValueError:
            pass
        self._load_patch(patch)
        self._refresh_lists(scroll_to_selection=True)

    def _brightness_from_x(self, x: int, rect: Rect) -> int:
        if rect.w <= 0:
            return self.brightness_percent
        ratio = (x - rect.x) / rect.w
        return max(0, min(100, round(ratio * 100)))

    def _mixer_default_value(self, channel: MixerChannel) -> float:
        if channel.channel_id == "volume":
            return DEFAULT_VOLUME
        return (channel.min_value + channel.max_value) / 2

    def _mixer_value(self, channel: MixerChannel) -> float:
        if channel.channel_id == "volume":
            return self.volume_level
        return self._mixer_levels.get(channel.channel_id, self._mixer_default_value(channel))

    def _value_to_handle_y(self, channel: MixerChannel, value: float) -> int:
        span = channel.max_value - channel.min_value
        ratio = 0.0 if span <= 0 else (value - channel.min_value) / span
        ratio = max(0.0, min(1.0, ratio))
        travel = channel.track_rect.h - FADER_HANDLE_H
        return int(channel.track_rect.y + travel * (1.0 - ratio))

    def _value_from_track_y(self, channel: MixerChannel, y: int) -> float:
        travel = max(1, channel.track_rect.h - FADER_HANDLE_H)
        local = y - channel.track_rect.y - FADER_HANDLE_H // 2
        ratio = 1.0 - max(0.0, min(1.0, local / travel))
        return channel.min_value + ratio * (channel.max_value - channel.min_value)

    def _set_mixer_value(self, channel: MixerChannel, value: float) -> None:
        clamped = max(channel.min_value, min(channel.max_value, value))
        if channel.channel_id == "volume":
            if channel.enabled:
                self._apply_volume(clamped)
            return
        self._mixer_levels[channel.channel_id] = clamped

    def _reset_mixer_channel(self, channel: MixerChannel) -> None:
        default = self._mixer_default_value(channel)
        self._set_mixer_value(channel, default)
        if channel.channel_id == "volume":
            self._toast("Volume reset", 1.2)
        elif channel.enabled:
            self._toast(f"{channel.label} reset", 1.2)

    def _mixer_channel_at(self, pos: tuple[int, int]) -> MixerChannel | None:
        for channel in self.mixer_channels:
            if channel.column_rect.contains(*pos):
                return channel
        return None

    def _handle_mixer_down(self, pos: tuple[int, int]) -> bool:
        channel = self._mixer_channel_at(pos)
        if channel is None:
            return False

        now = time.time()
        if (
            self._mixer_last_tap_id == channel.channel_id
            and not self._mixer_drag_moved
            and (now - self._mixer_last_tap_time) * 1000.0 <= MIXER_DOUBLE_TAP_MS
        ):
            self._reset_mixer_channel(channel)
            self._mixer_last_tap_id = None
            self._mixer_last_tap_time = 0.0
            self._mixer_drag_origin = None
            self._mixer_drag_moved = False
            self._dragging_mixer_id = None
            return True

        self._mixer_last_tap_id = channel.channel_id
        self._mixer_last_tap_time = now
        self._mixer_drag_origin = pos
        self._mixer_drag_moved = False
        self._dragging_mixer_id = channel.channel_id
        if channel.enabled:
            self._set_mixer_value(channel, self._value_from_track_y(channel, pos[1]))
        return True

    def _handle_mixer_motion(self, pos: tuple[int, int]) -> None:
        if self._mixer_drag_origin and not self._mixer_drag_moved:
            dx = pos[0] - self._mixer_drag_origin[0]
            dy = pos[1] - self._mixer_drag_origin[1]
            if (dx * dx + dy * dy) ** 0.5 > MIXER_DRAG_THRESHOLD_PX:
                self._mixer_drag_moved = True
                self._mixer_last_tap_id = None
        if not self._dragging_mixer_id:
            return
        for channel in self.mixer_channels:
            if channel.channel_id == self._dragging_mixer_id and channel.enabled:
                self._set_mixer_value(channel, self._value_from_track_y(channel, pos[1]))
                break

    def _apply_brightness(self, percent: int) -> None:
        self.brightness_percent = percent
        if not self.backlight.set_percent(percent):
            self._toast("Brightness control unavailable", 2.5)

    def _patch_is_favorited(self, patch: dict) -> bool:
        return self.scanner.is_patch_in_favorites(patch)

    def _sync_categories_after_favorites_change(self) -> None:
        with self._scan_lock:
            self.categories = self.scanner.get_categories()
        self._refresh_lists()

    def _normalization_enabled_for_detail(self) -> bool:
        if not self.detail_patch:
            return True
        return self.loader.normalization.is_enabled(self.detail_patch["name"])

    def _normalization_has_gain(self) -> bool:
        if not self.detail_patch:
            return False
        entry = self.loader.normalization.get_entry(self.detail_patch["name"])
        return bool(entry and entry.get("gain_db") is not None)

    def _normalization_patch_name(self) -> str | None:
        if not self.detail_patch:
            return None
        if self.loaded_patch_info and self.loaded_patch_info.get("name") == self.detail_patch.get("name"):
            return self.loaded_patch_info["name"]
        return self.detail_patch["name"]

    def _toggle_normalization(self) -> None:
        if not self.loader.normalization.is_globally_enabled():
            return
        name = self._normalization_patch_name()
        if not name:
            return
        store = self.loader.normalization
        new_state = not store.is_enabled(name)
        store.set_enabled(name, new_state)
        loaded_name = (
            self.loaded_patch_info.get("name") if self.loaded_patch_info else None
        )
        if self.loader.osc_enabled and loaded_name == name:
            self.loader.refresh_patch_volume(name)
        if new_state:
            if store.get_raw_gain_db(name) is not None:
                self._toast("Normalize on", 1.5)
            else:
                self._toast("Normalize on (no calibration)", 2.0)
        else:
            self._toast("Normalize off", 1.5)

    def _toggle_global_normalization(self) -> None:
        store = self.loader.normalization
        new_state = not store.is_globally_enabled()
        store.set_globally_enabled(new_state)
        loaded_name = (
            self.loaded_patch_info.get("name") if self.loaded_patch_info else None
        )
        if self.loader.osc_enabled and loaded_name:
            self.loader.refresh_patch_volume(loaded_name)
        if new_state:
            self._toast("Patch normalization on", 2.0)
        else:
            self._toast("Patch normalization off", 2.0)

    def _normalize_checkbox_rect(self, row: Rect) -> Rect:
        pad = (row.h - NORM_CHECKBOX_SIZE) // 2
        return Rect(
            row.right - pad - NORM_CHECKBOX_SIZE,
            row.y + pad,
            NORM_CHECKBOX_SIZE,
            NORM_CHECKBOX_SIZE,
        )

    def _draw_normalize_toggle(
        self,
        rect: Rect,
        enabled: bool,
        *,
        has_gain: bool,
        disabled: bool = False,
        label: str = "Norm.",
    ) -> None:
        row_bg = self.theme.surface if disabled else self.theme.surface_alt
        pygame.draw.rect(self.screen, row_bg, rect.pygame_rect, border_radius=8)

        text_color = self.theme.muted if disabled else self.theme.text
        label_surf = self.font_sm.render(label, True, text_color)
        ly = rect.y + (rect.h - label_surf.get_height()) // 2
        self.screen.blit(label_surf, (rect.x + 12, ly))

        box = self._normalize_checkbox_rect(rect)
        if disabled:
            box_bg = self.theme.surface
            border_color = self.theme.muted
            check_color = self.theme.muted if enabled else None
        elif enabled and has_gain:
            box_bg = self.theme.accent
            border_color = self.theme.accent
            check_color = (255, 255, 255)
        elif enabled:
            box_bg = self.theme.surface
            border_color = self.theme.muted
            check_color = self.theme.muted
        else:
            box_bg = self.theme.surface
            border_color = self.theme.muted
            check_color = None
        pygame.draw.rect(self.screen, box_bg, box.pygame_rect, border_radius=5)
        pygame.draw.rect(self.screen, border_color, box.pygame_rect, width=2, border_radius=5)
        if enabled and check_color is not None:
            check = self.font_sm.render("✓", True, check_color)
            cx = box.x + (box.w - check.get_width()) // 2
            cy = box.y + (box.h - check.get_height()) // 2 - 1
            self.screen.blit(check, (cx, cy))

    def _calibration_scope_stats(self, mode: CalibrateMode) -> tuple[int, int]:
        """Return (target_count, total_in_scope) for confirm modal duration hints."""
        with self._scan_lock:
            names: list[str] = []
            seen: set[str] = set()
            for patches in self.scanner.patches.values():
                for patch in patches:
                    stem = Path(patch["path"]).stem
                    if stem not in seen:
                        seen.add(stem)
                        names.append(stem)
        store = self.loader.normalization
        total = len(names)
        if mode == CalibrateMode.FORCE_FULL:
            return total, total
        missing = store.list_missing(names)
        return len(missing), total

    def _calibration_duration_hint(self, targets: int) -> str:
        if targets <= 0:
            return "Nothing to calibrate — all patches already have entries."
        seconds = targets * 4.5
        if seconds < 60:
            return f"Approx. {int(seconds)} sec ({targets} patch(es))."
        return f"Approx. {seconds / 60.0:.0f} min ({targets} patch(es))."

    def _calibration_mode_label(self, mode: CalibrateMode) -> str:
        if mode == CalibrateMode.FORCE_FULL:
            return "Force full normalization"
        return "Normalize missing only"

    def _calibration_mode_description(self, mode: CalibrateMode, targets: int, total: int) -> str:
        if mode == CalibrateMode.FORCE_FULL:
            return (
                f"Re-measure loudness for all {total} patches in the library "
                "(overwrites existing gain_db entries)."
            )
        if targets == 0:
            return "Every scanned patch already has a gain_db entry."
        return (
            f"Calibrate {targets} patch(es) missing gain_db entries "
            f"({total - targets} already done)."
        )

    def _toggle_favorites(self) -> None:
        if not self.detail_patch:
            return

        quick_label = favorites_display_name().lstrip("!")
        patch = self.detail_patch
        if self._patch_is_favorited(patch):
            if self.scanner.remove_patch_from_favorites(patch):
                self._sync_categories_after_favorites_change()
                self._toast(f"Removed from {quick_label}", 2.0)
            else:
                self._toast("Could not remove from Quick Select", 2.5)
            return

        if self.scanner.copy_patch_to_favorites(patch["path"]):
            self._sync_categories_after_favorites_change()
            self._toast(f"Added to {quick_label}", 2.0)
        else:
            self._toast(f"Already in {quick_label}", 2.0)

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

    @staticmethod
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

    @staticmethod
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
        label = self.font_sm.render(folder_name[:34], True, self.theme.text)
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
        text = font.render(label, True, text_color)
        tx = rect.x + (rect.w - text.get_width()) // 2
        ty = rect.y + (rect.h - text.get_height()) // 2
        self.screen.blit(text, (tx, ty))

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
            title = self.loaded_patch_info["name"][:34]
            subtitle = self.loaded_patch_info["category"][:30]
        else:
            title = "No patch loaded"
            subtitle = "Select a patch from the left list"

        self.screen.blit(
            self.font_md.render(title, True, self.theme.text),
            (self.status_rect.x + 12, self.status_rect.y + 6),
        )
        self.screen.blit(
            self.font_sm.render(subtitle, True, self.theme.muted),
            (self.status_rect.x + 12, self.status_rect.y + 26),
        )
        self._draw_cpu_meter(self.cpu_meter_rect)
        self._draw_button(self.system_settings_btn, "...", small=True, muted=True)

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
        pygame.draw.rect(self.screen, self.theme.surface, self.main_rect.pygame_rect, border_radius=10)

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

        name = self.font_lg.render(self.detail_patch["name"][:26], True, self.theme.text)
        self.screen.blit(name, (self.main_rect.x + 24, self.main_rect.y + 24))

        cat = self.font_sm.render(self.detail_patch["category"][:40], True, self.theme.muted)
        self.screen.blit(cat, (self.main_rect.x + 24, self.main_rect.y + 68))

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

    def _draw_settings(self) -> None:
        self.screen.fill(self.theme.bg)
        overlay = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 160))
        self.screen.blit(overlay, (0, 0))

        pygame.draw.rect(self.screen, self.theme.surface, self.settings_rect.pygame_rect, border_radius=16)
        self.screen.blit(
            self.font_md.render("System", True, self.theme.text),
            (self.settings_rect.x + 24, self.settings_rect.y + 20),
        )

        self._draw_slider(
            self.brightness_slider_rect,
            self.brightness_percent / 100.0,
            f"Brightness  {self.brightness_percent}%",
        )

        self._draw_normalize_toggle(
            self.norm_global_toggle_rect,
            self.loader.normalization.is_globally_enabled(),
            has_gain=True,
            label="Patch normalization",
        )

        status = self.surge_monitor.get_status_summary()
        status_y = self.settings_rect.y + 230
        self.screen.blit(
            self.font_sm.render(
                f"Surge: {status['status']} — {status['details'][:36]}",
                True,
                self.theme.ok if status["status"] == "Running" else self.theme.danger,
            ),
            (self.settings_rect.x + 24, status_y),
        )

        btn_y = status_y + 36
        if status.get("can_restart"):
            self._surge_restart_btn = Rect(
                self.settings_rect.x + 24, btn_y, self.settings_rect.w - 48, 44
            )
            self._draw_button(self._surge_restart_btn, "Restart Surge")
            btn_y += 52
        else:
            self._surge_restart_btn = None

        self._power_btn = Rect(self.settings_rect.x + 24, btn_y, self.settings_rect.w - 48, 44)
        self._draw_button(self._power_btn, "Power…", muted=True)
        btn_y += 52
        self._calibrate_missing_btn = Rect(
            self.settings_rect.x + 24, btn_y, self.settings_rect.w - 48, 44
        )
        self._draw_button(self._calibrate_missing_btn, "Calibrate missing patches")
        btn_y += 52
        self._calibrate_force_btn = Rect(
            self.settings_rect.x + 24, btn_y, self.settings_rect.w - 48, 44
        )
        self._draw_button(self._calibrate_force_btn, "Force full re-calibration", muted=True)
        btn_y += 52
        self._close_settings_btn = Rect(self.settings_rect.x + 24, btn_y, self.settings_rect.w - 48, 48)
        self._draw_button(self._close_settings_btn, "Close")

    def _draw_power_menu(self) -> None:
        self.screen.fill(self.theme.bg)
        overlay = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 180))
        self.screen.blit(overlay, (0, 0))

        panel_w = min(420, self.width - 48)
        panel_h = 280
        panel = Rect((self.width - panel_w) // 2, (self.height - panel_h) // 2, panel_w, panel_h)
        pygame.draw.rect(self.screen, self.theme.surface, panel.pygame_rect, border_radius=16)

        self.screen.blit(self.font_md.render("Power", True, self.theme.text), (panel.x + 24, panel.y + 20))

        self._power_option_rects = []
        y = panel.y + 70
        for i, option in enumerate(["Shutdown", "Restart", "Cancel"]):
            rect = Rect(panel.x + 24, y, panel.w - 48, 52)
            self._power_option_rects.append(rect)
            color = self.theme.accent if i == 2 else self.theme.surface_alt
            pygame.draw.rect(self.screen, color, rect.pygame_rect, border_radius=10)
            self.screen.blit(self.font_md.render(option, True, self.theme.text), (rect.x + 16, rect.y + 12))
            y += 60

    def _draw_power_confirm(self) -> None:
        self.screen.fill(self.theme.bg)
        overlay = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 190))
        self.screen.blit(overlay, (0, 0))

        panel_w = min(420, self.width - 48)
        panel_h = 220
        panel = Rect((self.width - panel_w) // 2, (self.height - panel_h) // 2, panel_w, panel_h)
        pygame.draw.rect(self.screen, self.theme.surface, panel.pygame_rect, border_radius=16)

        action = "Shut down?" if self.power_action == "shutdown" else "Restart?"
        self.screen.blit(self.font_md.render(action, True, self.theme.danger), (panel.x + 24, panel.y + 24))

        self._confirm_no = Rect(panel.x + 24, panel.y + 100, (panel.w - 60) // 2, 52)
        self._confirm_yes = Rect(self._confirm_no.x + self._confirm_no.w + 12, panel.y + 100, (panel.w - 60) // 2, 52)
        self._draw_button(self._confirm_no, "Cancel", accent=True)
        self._draw_button(self._confirm_yes, "Confirm")

    def _draw_calibrate_confirm(self) -> None:
        self.screen.fill(self.theme.bg)
        overlay = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 190))
        self.screen.blit(overlay, (0, 0))

        mode = self._pending_calibrate_mode
        targets, total = self._calibration_scope_stats(mode)
        title = self._calibration_mode_label(mode)

        panel_w = min(520, self.width - 48)
        panel_h = 300
        panel = Rect((self.width - panel_w) // 2, (self.height - panel_h) // 2, panel_w, panel_h)
        pygame.draw.rect(self.screen, self.theme.surface, panel.pygame_rect, border_radius=16)

        self.screen.blit(
            self.font_md.render(f"{title}?", True, self.theme.text),
            (panel.x + 24, panel.y + 18),
        )

        y = panel.y + 56
        for line in (
            self._calibration_mode_description(mode, targets, total),
            "Touch browser will stop; loader takes over the display.",
            self._calibration_duration_hint(targets),
            "Do not touch the screen during measurement.",
        ):
            hint = self.font_sm.render(line[:58], True, self.theme.muted)
            self.screen.blit(hint, (panel.x + 24, y))
            y += 26

        self._calibrate_confirm_no = Rect(panel.x + 24, panel.y + 220, (panel.w - 60) // 2, 52)
        self._calibrate_confirm_yes = Rect(
            self._calibrate_confirm_no.x + self._calibrate_confirm_no.w + 12,
            panel.y + 220,
            (panel.w - 60) // 2,
            52,
        )
        start_disabled = mode == CalibrateMode.MISSING_ONLY and targets == 0
        self._draw_button(self._calibrate_confirm_no, "Cancel", accent=True)
        self._draw_button(
            self._calibrate_confirm_yes,
            "Start",
            muted=start_disabled,
        )

    def _launch_calibration_loader(self) -> None:
        repo = Path(__file__).resolve().parent
        script = repo / "scripts" / "calibrate-with-loader.sh"
        args = ["bash", str(script)]
        if self._pending_calibrate_mode == CalibrateMode.FORCE_FULL:
            args.append("--force")
        if self._evdev_bridge is not None:
            self._evdev_bridge.stop()
        os.environ["MPE_CALIB_FROM_BROWSER"] = "1"
        pygame.quit()
        os.execv("/bin/bash", args)

    def _draw_toast(self) -> None:
        if time.time() > self.toast_until or not self.toast_message:
            return
        text = self.font_sm.render(self.toast_message, True, self.theme.text)
        pad_x, pad_y = 16, 10
        w = text.get_width() + pad_x * 2
        h = text.get_height() + pad_y * 2
        rect = pygame.Rect((self.width - w) // 2, self.height - 80, w, h)
        pygame.draw.rect(self.screen, self.theme.surface_alt, rect, border_radius=10)
        self.screen.blit(text, (rect.x + pad_x, rect.y + pad_y))

    def _draw(self) -> None:
        if self.screen_state == Screen.BROWSER:
            self._draw_browser()
        elif self.screen_state == Screen.SETTINGS:
            self._draw_settings()
        elif self.screen_state == Screen.POWER_MENU:
            self._draw_power_menu()
        elif self.screen_state == Screen.POWER_CONFIRM:
            self._draw_power_confirm()
        elif self.screen_state == Screen.CALIBRATE_CONFIRM:
            self._draw_calibrate_confirm()
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
            self.screen_state = Screen.SETTINGS
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

    def _handle_settings_touch(self, pos: tuple[int, int]) -> None:
        if self._close_settings_btn.contains(*pos):
            self.screen_state = Screen.BROWSER
            return
        if self.norm_global_toggle_rect.contains(*pos):
            self._toggle_global_normalization()
            return
        if self._surge_restart_btn and self._surge_restart_btn.contains(*pos):
            ok, message = self.surge_monitor.restart_surge()
            if ok:
                self._toast(message, 2.5)
                self._pending_last_patch = None
            else:
                self._toast(f"Restart failed: {message}", 3.5)
            return
        if self._power_btn.contains(*pos):
            self.screen_state = Screen.POWER_MENU
            return
        if self._calibrate_missing_btn.contains(*pos):
            self._pending_calibrate_mode = CalibrateMode.MISSING_ONLY
            self.screen_state = Screen.CALIBRATE_CONFIRM
            return
        if self._calibrate_force_btn.contains(*pos):
            self._pending_calibrate_mode = CalibrateMode.FORCE_FULL
            self.screen_state = Screen.CALIBRATE_CONFIRM
            return
        if self.brightness_slider_rect.contains(*pos):
            now = time.time()
            if (
                not self._brightness_drag_moved
                and self._brightness_last_tap_time > 0
                and (now - self._brightness_last_tap_time) * 1000.0 <= MIXER_DOUBLE_TAP_MS
            ):
                self._apply_brightness(DEFAULT_BRIGHTNESS_PERCENT)
                self._toast("Brightness reset", 1.2)
                self._brightness_last_tap_time = 0.0
                return
            self._brightness_last_tap_time = now
            self._brightness_drag_moved = False
            self._slider_dragging = True
            self._apply_brightness(self._brightness_from_x(pos[0], self.brightness_slider_rect))

    def _handle_power_menu_touch(self, pos: tuple[int, int]) -> None:
        for i, rect in enumerate(self._power_option_rects):
            if rect.contains(*pos):
                if i == 0:
                    self.power_action = "shutdown"
                    self.screen_state = Screen.POWER_CONFIRM
                elif i == 1:
                    self.power_action = "restart"
                    self.screen_state = Screen.POWER_CONFIRM
                else:
                    self.screen_state = Screen.SETTINGS
                return

    def _handle_calibrate_confirm_touch(self, pos: tuple[int, int]) -> None:
        if self._calibrate_confirm_no.contains(*pos):
            self.screen_state = Screen.SETTINGS
        elif self._calibrate_confirm_yes.contains(*pos):
            targets, _ = self._calibration_scope_stats(self._pending_calibrate_mode)
            if self._pending_calibrate_mode == CalibrateMode.MISSING_ONLY and targets == 0:
                self._toast("All patches already calibrated", 2.5)
                self.screen_state = Screen.SETTINGS
                return
            self._launch_calibration_loader()

    def _handle_power_confirm_touch(self, pos: tuple[int, int]) -> None:
        if self._confirm_no.contains(*pos):
            self.screen_state = Screen.POWER_MENU
        elif self._confirm_yes.contains(*pos):
            cmd = ["sudo", "poweroff"] if self.power_action == "shutdown" else ["sudo", "reboot"]
            subprocess.run(cmd, check=False)

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
                self._handle_settings_touch(event.pos)
            elif self.screen_state == Screen.POWER_MENU:
                self._handle_power_menu_touch(event.pos)
            elif self.screen_state == Screen.POWER_CONFIRM:
                self._handle_power_confirm_touch(event.pos)
            elif self.screen_state == Screen.CALIBRATE_CONFIRM:
                self._handle_calibrate_confirm_touch(event.pos)
            elif self.screen_state == Screen.BROWSER:
                self._handle_mixer_down(event.pos)
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            was_mixer = self._dragging_mixer_id is not None
            if was_mixer and self._mixer_drag_moved:
                self._mixer_last_tap_id = None
            self._dragging_mixer_id = None
            self._mixer_drag_origin = None
            self._mixer_drag_moved = False
            self._slider_dragging = False
            self._brightness_drag_moved = False
            if self.screen_state == Screen.SETTINGS:
                if not self.settings_rect.contains(*event.pos):
                    self.screen_state = Screen.BROWSER
            elif self.screen_state == Screen.BROWSER:
                idx = self.nav_list.take_tap_index()
                if idx is not None:
                    self._select_nav_index(idx)
                elif not was_mixer:
                    self._handle_browser_tap(event.pos)
        elif event.type == pygame.MOUSEMOTION:
            if self._slider_dragging:
                if not self._brightness_drag_moved:
                    self._brightness_drag_moved = True
                    self._brightness_last_tap_time = 0.0
                self._apply_brightness(self._brightness_from_x(event.pos[0], self.brightness_slider_rect))
            self._handle_mixer_motion(event.pos)

    def run(self) -> None:
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
            if self.screen_state == Screen.BROWSER and not self.left_nav_collapsed:
                self.nav_list.tick(dt)
            self._draw()
            clock.tick(60)

        if self._evdev_bridge is not None:
            self._evdev_bridge.stop()
        self.cpu_monitor.stop()
        pygame.quit()


def main() -> None:
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
    TouchPatchBrowser().run()


if __name__ == "__main__":
    main()
