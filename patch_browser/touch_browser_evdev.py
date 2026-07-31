"""Touch patch browser — evdev mixin."""

from __future__ import annotations

import json
import math
import os
import queue
import subprocess
import threading
import time
from pathlib import Path

import pygame

from patch_browser.calibration_constants import (
    MPE_CALIB_FROM_BROWSER,
    MPE_CALIB_FROM_BROWSER_ACTIVE,
)
from patch_browser.geometry import Rect
from patch_browser.mixer import MixerChannel
from patch_browser.patch_scanner import FAVORITES_NAME, favorites_display_name
from patch_browser.scroll_widgets import ContentScrollArea, ScrollList
from patch_browser.touch_ui_constants import *
from patch_browser.touch_ui_enums import (
    CalibrateMode,
    LeftNavMode,
    Screen,
    audio_profile_display,
)
from patch_browser.ui_prefs import (
    load_ui_preference,
    load_volume_level,
    read_ui_prefs_file,
    save_theme_mode,
    save_ui_preference,
    save_volume_level,
    write_ui_prefs_file,
)
from patch_browser.ui_text import (
    blit_text_block,
    draw_wrapped_text_in_rect,
    ellipsize_text,
    text_block_height,
    wrap_text_lines,
    wrapped_row_height,
)
from patch_browser.ui_theme import THEME_MODE_OLED_BLACK, THEME_MODE_STANDARD, Theme


class TouchBrowserEvdevMixin:
    """Mixin — expects TouchPatchBrowser host attributes."""

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
