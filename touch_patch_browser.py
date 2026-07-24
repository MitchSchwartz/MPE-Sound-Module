#!/usr/bin/env python3
"""
Pi-Surge-MPE Touch Patch Browser

Fullscreen touch UI for ~5" landscape displays (SmartiPi case + panel, DSI or HDMI).
Default layout target: 800×480 landscape — most common 5" panel size.
"""

from __future__ import annotations

import json
import os
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
    print("Install with: pip3 install pygame")
    raise SystemExit(1) from exc

from patch_browser.backlight import BacklightController
from patch_browser_ui import (
    FAVORITES_NAME,
    PatchLoader,
    PatchScanner,
    SURGE_PATCH_DIRS,
    SurgeMonitor,
    favorites_display_name,
)

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


class Screen(Enum):
    BROWSER = auto()
    SETTINGS = auto()
    POWER_MENU = auto()
    POWER_CONFIRM = auto()


class LeftNavMode(Enum):
    FOLDERS = auto()
    PATCHES = auto()


@dataclass
class Theme:
    bg: tuple[int, int, int] = (10, 10, 12)
    surface: tuple[int, int, int] = (22, 22, 28)
    surface_alt: tuple[int, int, int] = (32, 32, 40)
    text: tuple[int, int, int] = (232, 232, 236)
    muted: tuple[int, int, int] = (130, 130, 140)
    accent: tuple[int, int, int] = (107, 159, 255)
    playing: tuple[int, int, int] = (255, 180, 90)
    danger: tuple[int, int, int] = (220, 90, 90)
    ok: tuple[int, int, int] = (90, 200, 140)


@dataclass
class Rect:
    x: int
    y: int
    w: int
    h: int

    @property
    def pygame_rect(self) -> pygame.Rect:
        return pygame.Rect(self.x, self.y, self.w, self.h)

    def contains(self, px: int, py: int) -> bool:
        return self.x <= px < self.x + self.w and self.y <= py < self.y + self.h


class ScrollList:
    """Touch-scrollable list with tap vs scroll discrimination."""

    def __init__(self, rect: Rect, row_height: int = 56, padding: int = 8):
        self.rect = rect
        self.row_height = row_height
        self.padding = padding
        self.items: list[str] = []
        self.highlight_index: int | None = None
        self.loaded_marker_index: int | None = None
        self.scroll_offset = 0
        self._drag_start_y: int | None = None
        self._drag_scroll_start = 0
        self._pointer_down_pos: tuple[int, int] | None = None
        self._pointer_scrolled = False

    def set_items(
        self,
        items: list[str],
        highlight_index: int | None = None,
        loaded_marker_index: int | None = None,
    ) -> None:
        self.items = items
        self.highlight_index = highlight_index
        self.loaded_marker_index = loaded_marker_index
        self._clamp_scroll()

    def visible_count(self) -> int:
        inner_h = self.rect.h - self.padding * 2
        return max(1, inner_h // self.row_height)

    def _max_scroll(self) -> int:
        return max(0, len(self.items) - self.visible_count())

    def _clamp_scroll(self) -> None:
        self.scroll_offset = max(0, min(self.scroll_offset, self._max_scroll()))

    def item_at(self, px: int, py: int) -> int | None:
        if not self.rect.contains(px, py) or not self.items:
            return None
        local_y = py - self.rect.y - self.padding + self.scroll_offset * self.row_height
        index = local_y // self.row_height
        if 0 <= index < len(self.items):
            return int(index)
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
        self._clamp_scroll()

    def handle_event(self, event: pygame.event.Event) -> bool:
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.rect.contains(*event.pos):
                self._pointer_down_pos = event.pos
                self._pointer_scrolled = False
                self._drag_start_y = event.pos[1]
                self._drag_scroll_start = self.scroll_offset
                return True
        elif event.type == pygame.MOUSEMOTION and self._drag_start_y is not None:
            if self._pointer_down_pos:
                dx = event.pos[0] - self._pointer_down_pos[0]
                dy = event.pos[1] - self._pointer_down_pos[1]
                if (dx * dx + dy * dy) ** 0.5 > TAP_MOVE_THRESHOLD_PX:
                    self._pointer_scrolled = True
            delta = event.pos[1] - self._drag_start_y
            if delta != 0:
                self._pointer_scrolled = True
            self.scroll_offset = self._drag_scroll_start - delta // self.row_height
            self._clamp_scroll()
            return True
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            if self._drag_start_y is not None:
                self._drag_start_y = None
                return True
        elif event.type == pygame.MOUSEWHEEL:
            if self.rect.contains(*pygame.mouse.get_pos()):
                self.scroll_offset -= event.y
                self._clamp_scroll()
                return True
        return False

    def consume_tap(self, pos: tuple[int, int]) -> int | None:
        if self._pointer_down_pos is None or self._pointer_scrolled:
            self._pointer_down_pos = None
            self._pointer_scrolled = False
            return None
        if not self.rect.contains(*self._pointer_down_pos):
            self._pointer_down_pos = None
            return None
        index = self.item_at(*pos)
        self._pointer_down_pos = None
        self._pointer_scrolled = False
        return index

    def draw(self, surface: pygame.Surface, font: pygame.font.Font, theme: Theme) -> None:
        pygame.draw.rect(surface, theme.surface, self.rect.pygame_rect, border_radius=10)
        clip = surface.get_clip()
        surface.set_clip(self.rect.pygame_rect)

        start = self.scroll_offset
        end = min(len(self.items), start + self.visible_count() + 1)
        y = self.rect.y + self.padding

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
            self.screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        self.width, self.height = self.screen.get_size()
        self.theme = Theme()
        self.font_lg = self._load_font(34)
        self.font_md = self._load_font(22)
        self.font_sm = self._load_font(18)

        self.backlight = BacklightController()
        self.backlight.restore_saved()

        self.scanner = PatchScanner(SURGE_PATCH_DIRS)
        self.loader = PatchLoader()
        self.surge_monitor = SurgeMonitor()

        self.categories: list[str] = []
        self.browse_folder_index = 0
        self.loaded_folder_index = 0
        self.detail_patch: dict | None = None
        self.loaded_patch_info: dict | None = None
        self.left_nav_mode = LeftNavMode.PATCHES
        self.left_nav_collapsed = False
        self.screen_state = Screen.BROWSER

        self.volume_level = self._load_volume_level()
        self.brightness_percent = self.backlight.get_percent()
        self.toast_message = ""
        self.toast_until = 0.0
        self.power_action: str | None = None
        self._slider_dragging = False
        self._dragging_mixer_id: str | None = None
        self.mixer_channels: list[MixerChannel] = []
        self._running = True
        self._scan_lock = threading.Lock()

        self._layout()
        self._bootstrap_patches()
        self._start_background_scan()

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

        content_top = self.status_rect.y + self.status_rect.h + gap
        content_bottom = self.height - footer_h - margin
        left_w = self._left_nav_width()

        self.left_panel_rect = Rect(margin, content_top, left_w, content_bottom - content_top)
        self.nav_toggle_btn = Rect(margin, content_top, left_w, content_bottom - content_top)
        self.nav_header_rect = Rect(margin, content_top, LEFT_NAV_WIDTH, nav_header_h)
        list_top = content_top + nav_header_h + 4
        self.nav_list = ScrollList(
            Rect(margin, list_top, LEFT_NAV_WIDTH, content_bottom - list_top),
            row_height=50 if self.left_nav_mode == LeftNavMode.PATCHES else 44,
        )

        main_x = margin + left_w + gap
        main_w = self.width - margin * 2 - left_w - gap
        self.main_rect = Rect(main_x, content_top, main_w, content_bottom - content_top)
        self._layout_mixer_strip()

        self._layout_nav_buttons()

        settings_w = min(420, self.width - margin * 2)
        settings_h = min(360, self.height - margin * 2)
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

    def _layout_nav_buttons(self) -> None:
        y = self.nav_header_rect.y + 4
        x = self.nav_header_rect.x + 6
        self.nav_collapse_btn = Rect(self.nav_header_rect.right - 38, y, 32, 28)
        self.nav_up_btn = Rect(x, y, 52, 28)
        x += 58
        self.nav_current_btn = Rect(x, y, 72, 28)

    def _mixer_channel_defs(self) -> list[dict]:
        return [
            {"id": "volume", "label": "Vol", "min": VOLUME_MIN, "max": VOLUME_MAX, "enabled": True},
            {"id": "cutoff", "label": "Cut", "min": 0.0, "max": 1.0, "enabled": False},
            {"id": "res", "label": "Res", "min": 0.0, "max": 1.0, "enabled": False},
            {"id": "send", "label": "Snd", "min": 0.0, "max": 1.0, "enabled": False},
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
                if self.loader.osc_enabled and self.loader.load_patch(last["patch_path"]):
                    self.loaded_patch_info = {
                        "name": Path(last["patch_path"]).stem,
                        "category": last["category"],
                        "path": last["patch_path"],
                    }
                    self.detail_patch = dict(self.loaded_patch_info)
                    self._apply_volume(self.volume_level, persist=False)
        self._refresh_lists()

    def _start_background_scan(self) -> None:
        def worker() -> None:
            self.scanner.scan_patches_background()
            self.scanner.wait_for_scan(timeout=120)
            with self._scan_lock:
                self.categories = self.scanner.get_categories()
                if self.loaded_patch_info:
                    try:
                        idx = self.categories.index(self.loaded_patch_info["category"])
                        self.browse_folder_index = idx
                        self.loaded_folder_index = idx
                    except ValueError:
                        pass
                self._refresh_lists()

        threading.Thread(target=worker, daemon=True, name="TouchScanSync").start()

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

    def _refresh_lists(self) -> None:
        if self.left_nav_collapsed:
            return

        self.nav_list.row_height = 50 if self.left_nav_mode == LeftNavMode.PATCHES else 44

        if self.left_nav_mode == LeftNavMode.FOLDERS:
            loaded_idx = self.loaded_folder_index if self.loaded_patch_info else None
            self.nav_list.set_items(
                self.categories,
                highlight_index=self.browse_folder_index,
                loaded_marker_index=loaded_idx,
            )
            self.nav_list.scroll_to_index(self.browse_folder_index)
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
            if highlight is not None:
                self.nav_list.scroll_to_index(highlight)
            elif loaded_idx is not None:
                self.nav_list.scroll_to_index(loaded_idx)

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
        self._refresh_lists()

    def _go_up_to_folders(self) -> None:
        self.left_nav_mode = LeftNavMode.FOLDERS
        self._refresh_lists()

    def _go_to_loaded_folder(self) -> None:
        if not self.loaded_patch_info:
            return
        try:
            idx = self.categories.index(self.loaded_patch_info["category"])
        except ValueError:
            return
        self.browse_folder_index = idx
        self.left_nav_mode = LeftNavMode.PATCHES
        self._refresh_lists()

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
        self._refresh_lists()

    def _brightness_from_x(self, x: int, rect: Rect) -> int:
        if rect.w <= 0:
            return self.brightness_percent
        ratio = (x - rect.x) / rect.w
        return max(0, min(100, round(ratio * 100)))

    def _mixer_value(self, channel: MixerChannel) -> float:
        if channel.channel_id == "volume":
            return self.volume_level
        return (channel.min_value + channel.max_value) / 2

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
        if channel.channel_id == "volume":
            self._apply_volume(value)

    def _mixer_channel_at(self, pos: tuple[int, int]) -> MixerChannel | None:
        for channel in self.mixer_channels:
            if channel.enabled and channel.column_rect.contains(*pos):
                return channel
        return None

    def _handle_mixer_down(self, pos: tuple[int, int]) -> bool:
        channel = self._mixer_channel_at(pos)
        if channel is None:
            return False
        self._dragging_mixer_id = channel.channel_id
        self._set_mixer_value(channel, self._value_from_track_y(channel, pos[1]))
        return True

    def _handle_mixer_motion(self, pos: tuple[int, int]) -> None:
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
        self._draw_button(self.system_settings_btn, "...", small=True, muted=True)

    def _draw_left_nav_collapsed(self) -> None:
        pygame.draw.rect(self.screen, self.theme.surface, self.left_panel_rect.pygame_rect, border_radius=10)
        label = self.font_md.render(">", True, self.theme.muted)
        cx = self.left_panel_rect.x + (self.left_panel_rect.w - label.get_width()) // 2
        cy = self.left_panel_rect.y + (self.left_panel_rect.h - label.get_height()) // 2
        self.screen.blit(label, (cx, cy))

    def _draw_left_nav_expanded(self) -> None:
        pygame.draw.rect(self.screen, self.theme.surface, self.left_panel_rect.pygame_rect, border_radius=10)

        if self.left_nav_mode == LeftNavMode.PATCHES:
            self._draw_button(self.nav_up_btn, "Up", small=True)
        if self._show_current_folder_button():
            self._draw_button(self.nav_current_btn, "Current", small=True, accent=True)

        self._draw_button(self.nav_collapse_btn, "<", small=True, muted=True)

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

        status = self.surge_monitor.get_status_summary()
        self.screen.blit(
            self.font_sm.render(
                f"Surge: {status['status']} — {status['details'][:36]}",
                True,
                self.theme.ok if status["status"] == "Running" else self.theme.danger,
            ),
            (self.settings_rect.x + 24, self.settings_rect.y + 180),
        )

        self._power_btn = Rect(self.settings_rect.x + 24, self.settings_rect.y + 220, self.settings_rect.w - 48, 48)
        self._close_settings_btn = Rect(self.settings_rect.x + 24, self.settings_rect.y + 280, self.settings_rect.w - 48, 48)
        self._draw_button(self._power_btn, "Power…")
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
        self._draw_toast()
        pygame.display.flip()

    def _handle_browser_tap(self, pos: tuple[int, int]) -> None:
        if self.system_settings_btn.contains(*pos):
            self.screen_state = Screen.SETTINGS
            return

        if self.left_nav_collapsed:
            if self.nav_toggle_btn.contains(*pos):
                self._toggle_nav_collapsed()
            return

        if self.nav_collapse_btn.contains(*pos):
            self._toggle_nav_collapsed()
            return
        if self.nav_up_btn.contains(*pos) and self.left_nav_mode == LeftNavMode.PATCHES:
            self._go_up_to_folders()
            return
        if self.nav_current_btn.contains(*pos) and self._show_current_folder_button():
            self._go_to_loaded_folder()
            return

        idx = self.nav_list.consume_tap(pos)
        if idx is not None:
            if self.left_nav_mode == LeftNavMode.FOLDERS:
                self._enter_folder(idx)
            else:
                patches = self._patches_in_browse_folder()
                if idx < len(patches):
                    self._select_patch(patches[idx])

    def _handle_settings_touch(self, pos: tuple[int, int]) -> None:
        if self._close_settings_btn.contains(*pos):
            self.screen_state = Screen.BROWSER
            return
        if self._power_btn.contains(*pos):
            self.screen_state = Screen.POWER_MENU
            return
        if self.brightness_slider_rect.contains(*pos):
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

        if self.screen_state == Screen.BROWSER and not self.left_nav_collapsed:
            self.nav_list.handle_event(event)

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.screen_state == Screen.SETTINGS:
                self._handle_settings_touch(event.pos)
            elif self.screen_state == Screen.POWER_MENU:
                self._handle_power_menu_touch(event.pos)
            elif self.screen_state == Screen.POWER_CONFIRM:
                self._handle_power_confirm_touch(event.pos)
            elif self.screen_state == Screen.BROWSER:
                self._handle_mixer_down(event.pos)
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            was_mixer = self._dragging_mixer_id is not None
            self._dragging_mixer_id = None
            self._slider_dragging = False
            if self.screen_state == Screen.BROWSER and not was_mixer:
                self._handle_browser_tap(event.pos)
        elif event.type == pygame.MOUSEMOTION:
            if self._slider_dragging:
                self._apply_brightness(self._brightness_from_x(event.pos[0], self.brightness_slider_rect))
            self._handle_mixer_motion(event.pos)

    def run(self) -> None:
        clock = pygame.time.Clock()
        print("Touch patch browser running.")
        print(f"Display: {self.width}x{self.height}")
        print(f"Quick-access folder: {favorites_display_name()} ({FAVORITES_NAME})")

        while self._running:
            for event in pygame.event.get():
                self._handle_event(event)
            self._draw()
            clock.tick(30)

        pygame.quit()


def main() -> None:
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
    TouchPatchBrowser().run()


if __name__ == "__main__":
    main()
