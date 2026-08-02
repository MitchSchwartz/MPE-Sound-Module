"""Touch patch browser — evdev mixin."""

from __future__ import annotations

import queue

import pygame

from patch_browser.touch_evdev import TouchEvdevBridge, evdev_bridge_enabled
from patch_browser.touch_ui_enums import LeftNavMode, Screen


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
            self._az_rail_capture = False
            if self._handle_az_rail_touch("down", pos):
                return
            if self.detail_patch and self.normalize_btn.contains(*pos):
                self._pending_norm_toggle = True
                return
            if self.detail_patch and self.favorites_btn.contains(*pos):
                self._pending_favorites_toggle = True
                return
            if not self.left_nav_collapsed and self.nav_list.pointer_down(pos):
                self._touch_list_capture = True
            elif self.left_nav_mode != LeftNavMode.ALL_PATCHES:
                self._handle_mixer_down(pos)
        elif kind == "motion":
            if self._handle_az_rail_touch("motion", pos):
                return
            if self._touch_list_capture or self.nav_list.is_dragging():
                self.nav_list.pointer_move(pos)
            elif self.left_nav_mode != LeftNavMode.ALL_PATCHES:
                self._handle_mixer_motion(pos)
        elif kind == "up":
            was_mixer = self._dragging_mixer_id is not None
            if was_mixer:
                if self._mixer_drag_moved:
                    self._mixer_last_tap_id = None
                self._persist_mixer_drag()
            self._dragging_mixer_id = None
            self._mixer_drag_origin = None
            self._mixer_drag_moved = False

            if getattr(self, "_pending_norm_toggle", False):
                self._pending_norm_toggle = False
                self._toggle_normalization()
                self._touch_list_capture = False
                return
            if getattr(self, "_pending_favorites_toggle", False):
                self._pending_favorites_toggle = False
                self._toggle_favorites()
                self._touch_list_capture = False
                return

            if self._handle_az_rail_touch("up", pos):
                self._touch_list_capture = False
                return

            list_gesture = self._touch_list_capture or self.nav_list.is_dragging()
            if not self.left_nav_collapsed and list_gesture:
                idx = self.nav_list.pointer_up(pos)
                if idx is not None:
                    self._select_nav_index(idx)
            elif not was_mixer:
                self._handle_browser_tap(pos)
            self._touch_list_capture = False
            self._az_rail_capture = False
    def _select_nav_index(self, idx: int) -> None:
        if self.left_nav_mode == LeftNavMode.ALL_PATCHES:
            if idx < len(self.all_patches_flat):
                self._select_patch(self.all_patches_flat[idx])
        elif self.left_nav_mode == LeftNavMode.FOLDERS:
            self._enter_folder(idx)
        else:
            patches = self._patches_in_browse_folder()
            if idx < len(patches):
                self._select_patch(patches[idx])
