"""Wi‑Fi scan/connect modals for touch System settings."""

from __future__ import annotations

import queue
import threading
import time

import pygame

from patch_browser.draw_primitives import draw_lock_icon
from patch_browser.geometry import Rect
from patch_browser.scroll_widgets import ContentScrollArea, draw_vertical_scroll_edge_hints
from patch_browser.touch_keyboard import TouchKeyboardLayout, wifi_password_char_visible
from patch_browser.touch_ui_constants import SETTINGS_ROW_GAP, SETTINGS_ROW_H, TAP_MOVE_THRESHOLD_PX
from patch_browser.touch_ui_enums import Screen
from patch_browser.wifi_manager import WifiNetwork, connect_wifi, scan_wifi, wifi_settings_row_label


WIFI_VIEW_LIST = "list"
WIFI_VIEW_PASSWORD = "password"
WIFI_KEY_FLASH_S = 0.15


def _wifi_password_char_visible(ch: str) -> str:
    return wifi_password_char_visible(ch)


class TouchBrowserWifiModalMixin:
    """Mixin — expects TouchPatchBrowser host attributes."""

    def wifi_settings_row_label(self) -> str:
        return wifi_settings_row_label()

    def _open_wifi_modal(self) -> None:
        if getattr(self, "_wifi_busy", False):
            return
        self._wifi_view = WIFI_VIEW_LIST
        self._wifi_networks: list[WifiNetwork] = []
        self._wifi_scan_error: str | None = None
        self._wifi_selected_ssid: str | None = None
        self._wifi_selected_bssid: str | None = None
        self._wifi_selected_saved = False
        self._wifi_password = ""
        self._wifi_network_rows: list[tuple[Rect, WifiNetwork]] = []
        self._wifi_scroll = ContentScrollArea(Rect(0, 0, 1, 1))
        self._wifi_scroll.reset()
        self._wifi_result_queue: queue.SimpleQueue[
            tuple[list[WifiNetwork], str | None]
        ] = queue.SimpleQueue()
        self._wifi_connect_queue: queue.SimpleQueue[tuple[bool, str]] = queue.SimpleQueue()
        self.screen_state = Screen.WIFI_MODAL
        self._wifi_busy_started = time.monotonic()
        self._start_wifi_scan()

    def _close_wifi_modal(self) -> None:
        self.screen_state = Screen.SETTINGS
        self._wifi_view = WIFI_VIEW_LIST
        self._wifi_selected_ssid = None
        self._wifi_selected_bssid = None
        self._wifi_selected_saved = False
        self._wifi_password = ""

    def _start_wifi_scan(self) -> None:
        self._wifi_busy = True
        self._wifi_busy_hint = "Scanning Wi‑Fi…"

        def _worker() -> None:
            networks, error = scan_wifi()
            self._wifi_result_queue.put((networks, error))

        threading.Thread(target=_worker, daemon=True, name="WifiScan").start()

    def _poll_wifi_work(self) -> None:
        if getattr(self, "_wifi_connecting", False):
            try:
                ok, message = self._wifi_connect_queue.get_nowait()
            except queue.Empty:
                if time.monotonic() - self._wifi_connect_started > 50.0:
                    self._finish_wifi_connect(False, "Connect timed out")
                return
            self._finish_wifi_connect(ok, message)
            return

        if not getattr(self, "_wifi_busy", False):
            return
        try:
            networks, error = self._wifi_result_queue.get_nowait()
        except queue.Empty:
            if time.monotonic() - getattr(self, "_wifi_busy_started", time.monotonic()) > 30.0:
                self._wifi_busy = False
                self._wifi_scan_error = "Scan timed out"
                self._layout_settings_content()
            return
        self._wifi_busy = False
        self._wifi_networks = networks
        self._wifi_scan_error = error
        self._layout_settings_content()

    def _finish_wifi_connect(self, ok: bool, message: str) -> None:
        self._wifi_connecting = False
        self._wifi_busy = False
        if ok:
            self._toast(message, 3.0)
            self._close_wifi_modal()
            self._layout_settings_content()
            self._layout()
        else:
            self._toast(f"Wi‑Fi: {message}", 4.0)

    def _begin_wifi_connect(
        self,
        ssid: str,
        password: str | None,
        *,
        bssid: str | None = None,
    ) -> None:
        self._wifi_connecting = True
        self._wifi_connect_started = time.monotonic()
        self._wifi_busy = True
        self._wifi_busy_hint = f"Connecting to {ssid}…"

        def _worker() -> None:
            ok, message = connect_wifi(ssid, password, bssid=bssid)
            self._wifi_connect_queue.put((ok, message))

        threading.Thread(target=_worker, daemon=True, name="WifiConnect").start()

    def _select_wifi_network(self, network: WifiNetwork) -> None:
        if network.in_use:
            self._close_wifi_modal()
            return
        if network.secured:
            self._wifi_selected_ssid = network.ssid
            self._wifi_selected_bssid = network.bssid
            self._wifi_selected_saved = network.saved
            self._wifi_password = ""
            self._wifi_key_pressed = None
            self._wifi_view = WIFI_VIEW_PASSWORD
            return
        self._begin_wifi_connect(network.ssid, None, bssid=network.bssid)

    def _submit_wifi_password(self) -> None:
        ssid = self._wifi_selected_ssid
        if not ssid:
            return
        if not self._wifi_password:
            if getattr(self, "_wifi_selected_saved", False):
                self._begin_wifi_connect(ssid, None, bssid=self._wifi_selected_bssid)
                return
            self._toast("Enter the network password", 2.0)
            return
        self._begin_wifi_connect(ssid, self._wifi_password, bssid=self._wifi_selected_bssid)

    def _wifi_append_password(self, text: str) -> None:
        if len(self._wifi_password) >= 63:
            return
        self._wifi_password += text

    def _wifi_backspace_password(self) -> None:
        self._wifi_password = self._wifi_password[:-1]

    def _draw_wifi_password_field(self, inner_x: int, y: int, inner_w: int) -> None:
        field_h = 40
        pygame.draw.rect(self.screen, self.theme.surface_alt, (inner_x, y, inner_w, field_h), border_radius=8)
        pad_x = inner_x + 12
        text_y = y + 8
        password = self._wifi_password
        if not password:
            placeholder = self.font_md.render("Password", True, self.theme.muted)
            self.screen.blit(placeholder, (pad_x, text_y))
            return
        if len(password) == 1:
            visible = _wifi_password_char_visible(password)
            last_surf = self.font_md.render(visible, True, self.theme.accent)
            self.screen.blit(last_surf, (pad_x, text_y))
            return
        masked = "•" * (len(password) - 1)
        visible = _wifi_password_char_visible(password[-1])
        masked_surf = self.font_md.render(masked, True, self.theme.text)
        last_surf = self.font_md.render(visible, True, self.theme.accent)
        self.screen.blit(masked_surf, (pad_x, text_y))
        self.screen.blit(last_surf, (pad_x + masked_surf.get_width(), text_y))

    def _draw_wifi_modal(self) -> None:
        if getattr(self, "_wifi_view", WIFI_VIEW_LIST) == WIFI_VIEW_PASSWORD:
            self._draw_wifi_password_modal()
        else:
            self._draw_wifi_list_modal()

    def _draw_wifi_busy_overlay(self) -> None:
        if not getattr(self, "_wifi_busy", False) and not getattr(self, "_wifi_connecting", False):
            return
        self._draw_modal_backdrop(legacy_alpha=120)
        hint = getattr(self, "_wifi_busy_hint", "Working…")
        surf = self.font_md.render(hint, True, self.theme.text)
        self.screen.blit(surf, ((self.width - surf.get_width()) // 2, self.height // 2 - 12))

    def _draw_wifi_list_modal(self) -> None:
        self._draw_modal_backdrop(legacy_alpha=150)
        panel_w = min(560, self.width - 32)
        panel_h = min(420, self.height - 24)
        panel = Rect((self.width - panel_w) // 2, (self.height - panel_h) // 2, panel_w, panel_h)
        self._draw_modal_shell(panel, border_radius=16)

        inner_x = panel.x + 20
        inner_w = panel.w - 40
        y = panel.y + 18
        self.screen.blit(self.font_md.render("Wi‑Fi networks", True, self.theme.text), (inner_x, y))

        refresh_rect = Rect(panel.right - 20 - 96, panel.y + 14, 96, 36)
        self._wifi_refresh_rect = refresh_rect
        self._draw_button(refresh_rect, "Refresh", small=True)

        list_top = y + self.font_md.get_height() + 12
        footer_h = SETTINGS_ROW_H + 16
        list_h = panel.bottom - footer_h - list_top
        scroll_vp = Rect(inner_x, list_top, inner_w, max(80, list_h))
        self._wifi_scroll.viewport = scroll_vp
        self._wifi_scroll.content_height = self._layout_wifi_network_rows(inner_x, list_top, inner_w)

        clip = self.screen.get_clip()
        self.screen.set_clip(scroll_vp.pygame_rect)
        scroll = int(self._wifi_scroll.scroll_pixels)
        if self._wifi_scan_error:
            msg = self.font_sm.render(self._wifi_scan_error, True, self.theme.danger)
            self.screen.blit(msg, (inner_x, list_top + 8 - scroll))
        elif not self._wifi_networks and not getattr(self, "_wifi_busy", False):
            msg = self.font_sm.render("No networks found — try Refresh", True, self.theme.muted)
            self.screen.blit(msg, (inner_x, list_top + 8 - scroll))
        else:
            for rect, network in self._wifi_network_rows:
                screen_rect = Rect(rect.x, rect.y - scroll, rect.w, rect.h)
                if screen_rect.bottom < scroll_vp.y or screen_rect.y > scroll_vp.bottom:
                    continue
                self._draw_wifi_network_row(screen_rect, network)
        self.screen.set_clip(clip)

        draw_vertical_scroll_edge_hints(
            self.screen,
            scroll_vp,
            self._wifi_scroll,
            self.theme,
        )

        cancel_rect = Rect(inner_x, panel.bottom - footer_h + 4, inner_w, SETTINGS_ROW_H)
        self._wifi_cancel_rect = cancel_rect
        self._draw_button(cancel_rect, "Cancel")

    def _layout_wifi_network_rows(self, inner_x: int, list_top: int, inner_w: int) -> int:
        self._wifi_network_rows = []
        y = list_top
        for network in getattr(self, "_wifi_networks", []):
            rect = Rect(inner_x, y, inner_w, SETTINGS_ROW_H)
            self._wifi_network_rows.append((rect, network))
            y += SETTINGS_ROW_H + SETTINGS_ROW_GAP
        return max(0, y - list_top)

    def _draw_wifi_network_row(self, rect: Rect, network: WifiNetwork) -> None:
        bg = self.theme.surface_alt
        if network.in_use:
            pygame.draw.rect(self.screen, bg, rect.pygame_rect, border_radius=10)
            pygame.draw.rect(self.screen, self.theme.accent, rect.pygame_rect, width=2, border_radius=10)
        else:
            pygame.draw.rect(self.screen, bg, rect.pygame_rect, border_radius=10)

        label = network.ssid
        if network.saved:
            label += "  *"
        text_color = self.theme.accent if network.in_use else self.theme.text
        text = self.font_md.render(label, True, text_color)
        text_x = rect.x + 14
        text_y = rect.y + (rect.h - text.get_height()) // 2
        self.screen.blit(text, (text_x, text_y))
        if network.secured:
            lock_x = text_x + text.get_width() + 6
            lock_rect = Rect(lock_x, rect.y + (rect.h - 16) // 2, 14, 16)
            draw_lock_icon(self.screen, lock_rect, self.theme.muted)
        suffix_surf = self.font_sm.render(f"{network.signal}%", True, self.theme.muted)
        self.screen.blit(
            suffix_surf,
            (rect.right - suffix_surf.get_width() - 14, rect.y + (rect.h - suffix_surf.get_height()) // 2),
        )

    def _draw_wifi_password_modal(self) -> None:
        self._draw_modal_backdrop(legacy_alpha=150)
        panel_w = min(560, self.width - 24)
        panel_h = min(self.height - 16, 430)
        panel = Rect((self.width - panel_w) // 2, (self.height - panel_h) // 2, panel_w, panel_h)
        self._draw_modal_shell(panel, border_radius=16)

        inner_x = panel.x + 16
        inner_w = panel.w - 32
        ssid = self._wifi_selected_ssid or "Network"
        y = panel.y + 14
        self.screen.blit(self.font_md.render(ssid, True, self.theme.text), (inner_x, y))
        if getattr(self, "_wifi_selected_saved", False):
            y += self.font_md.get_height() + 2
            hint = self.font_sm.render(
                "Saved — tap Connect to retry, or enter password",
                True,
                self.theme.muted,
            )
            self.screen.blit(hint, (inner_x, y))
        y += self.font_md.get_height() + 8

        self._draw_wifi_password_field(inner_x, y, inner_w)
        y += 48

        btn_h = 44
        btn_gap = 10
        btn_w = (inner_w - btn_gap) // 2
        btn_y = panel.bottom - 16 - btn_h
        self._wifi_password_back_rect = Rect(inner_x, btn_y, btn_w, btn_h)
        self._wifi_password_connect_rect = Rect(inner_x + btn_w + btn_gap, btn_y, btn_w, btn_h)
        pressed_key = getattr(self, "_wifi_key_pressed", None)
        if pressed_key is None and time.monotonic() < getattr(self, "_wifi_key_flash_until", 0.0):
            pressed_key = getattr(self, "_wifi_key_flash_key", None)
        self._draw_button(
            self._wifi_password_back_rect,
            "Back",
            pressed=pressed_key == "back",
        )
        self._draw_button(
            self._wifi_password_connect_rect,
            "Connect",
            accent=True,
            pressed=pressed_key == "connect",
        )

        keyboard_panel = Rect(inner_x, y, inner_w, btn_y - y - 8)
        self._wifi_keyboard = TouchKeyboardLayout(keyboard_panel)
        for rect, label in self._wifi_keyboard.keys:
            self._draw_button(rect, label, small=True, pressed=pressed_key == label)
        if self._wifi_keyboard.backspace_rect:
            self._draw_button(
                self._wifi_keyboard.backspace_rect,
                "⌫",
                small=True,
                pressed=pressed_key == "backspace",
            )
        if self._wifi_keyboard.space_rect:
            self._draw_button(
                self._wifi_keyboard.space_rect,
                "space",
                small=True,
                pressed=pressed_key == " ",
            )

    def _wifi_list_hit_at(self, pos: tuple[int, int]) -> str | None:
        refresh = getattr(self, "_wifi_refresh_rect", None)
        if refresh is not None and refresh.contains(*pos):
            return "refresh"
        cancel = getattr(self, "_wifi_cancel_rect", None)
        if cancel is not None and cancel.contains(*pos):
            return "cancel"
        scroll_vp = getattr(self, "_wifi_scroll", None)
        if scroll_vp is None or not scroll_vp.viewport.contains(*pos):
            return None
        scroll = int(scroll_vp.scroll_pixels)
        for index, (rect, _network) in enumerate(getattr(self, "_wifi_network_rows", [])):
            screen_rect = Rect(rect.x, rect.y - scroll, rect.w, rect.h)
            if screen_rect.contains(*pos):
                return f"net:{index}"
        return None

    def _wifi_password_hit_at(self, pos: tuple[int, int]) -> str | None:
        back = getattr(self, "_wifi_password_back_rect", None)
        if back is not None and back.contains(*pos):
            return "back"
        connect = getattr(self, "_wifi_password_connect_rect", None)
        if connect is not None and connect.contains(*pos):
            return "connect"
        keyboard = getattr(self, "_wifi_keyboard", None)
        if keyboard is not None:
            key = keyboard.hit(pos)
            if key is not None:
                return f"key:{key}"
        return None

    def _handle_wifi_modal_pointer_down(self, pos: tuple[int, int]) -> None:
        self._clear_modal_pointer()
        self._modal_pointer_down_pos = pos
        self._modal_pending_key = None
        if getattr(self, "_wifi_view", WIFI_VIEW_LIST) == WIFI_VIEW_PASSWORD:
            hit = self._wifi_password_hit_at(pos)
            if hit is not None:
                if hit == "back":
                    self._wifi_key_pressed = "back"
                elif hit == "connect":
                    self._wifi_key_pressed = "connect"
                elif hit.startswith("key:"):
                    self._wifi_key_pressed = hit.split(":", 1)[1]
                self._modal_pending_key = hit
            return

        hit = self._wifi_list_hit_at(pos)
        if hit in ("refresh", "cancel"):
            self._modal_pending_key = hit
        scroll_vp = getattr(self, "_wifi_scroll", None)
        if scroll_vp is not None and scroll_vp.viewport.contains(*pos):
            scroll_vp.pointer_down(pos)

    def _handle_wifi_modal_pointer_move(self, pos: tuple[int, int]) -> None:
        if getattr(self, "_wifi_view", WIFI_VIEW_LIST) != WIFI_VIEW_LIST:
            return
        scroll_vp = getattr(self, "_wifi_scroll", None)
        if scroll_vp is not None:
            scroll_vp.pointer_move(pos)

    def _handle_wifi_modal_pointer_up(self, pos: tuple[int, int]) -> None:
        try:
            scroll_vp = getattr(self, "_wifi_scroll", None)
            scrolled = scroll_vp.pointer_up(pos) if scroll_vp is not None else False
            if scrolled:
                self._clear_modal_pointer()
                return
            if self._pointer_move_distance(self._modal_pointer_down_pos, pos) > TAP_MOVE_THRESHOLD_PX:
                self._clear_modal_pointer()
                return

            hit = self._modal_pending_key
            if hit is None and getattr(self, "_wifi_view", WIFI_VIEW_LIST) == WIFI_VIEW_LIST:
                hit = self._wifi_list_hit_at(pos)
            self._clear_modal_pointer()
            if hit == "cancel":
                self._close_wifi_modal()
                return
            if hit == "refresh" and not getattr(self, "_wifi_busy", False):
                self._wifi_busy_started = time.monotonic()
                self._start_wifi_scan()
                return
            if hit.startswith("net:"):
                try:
                    index = int(hit.split(":", 1)[1])
                except ValueError:
                    return
                if 0 <= index < len(self._wifi_networks):
                    self._select_wifi_network(self._wifi_networks[index])
                return
            if hit == "back":
                self._wifi_key_flash_key = "back"
                self._wifi_key_flash_until = time.monotonic() + WIFI_KEY_FLASH_S
                self._wifi_view = WIFI_VIEW_LIST
                self._wifi_selected_ssid = None
                self._wifi_selected_bssid = None
                self._wifi_password = ""
                return
            if hit == "connect":
                self._wifi_key_flash_key = "connect"
                self._wifi_key_flash_until = time.monotonic() + WIFI_KEY_FLASH_S
                self._submit_wifi_password()
                return
            if hit.startswith("key:"):
                key = hit.split(":", 1)[1]
                self._wifi_key_flash_key = key
                self._wifi_key_flash_until = time.monotonic() + WIFI_KEY_FLASH_S
                if key == "backspace":
                    self._wifi_backspace_password()
                else:
                    self._wifi_append_password(key)
        finally:
            if getattr(self, "_wifi_view", WIFI_VIEW_LIST) == WIFI_VIEW_PASSWORD:
                self._wifi_key_pressed = None
