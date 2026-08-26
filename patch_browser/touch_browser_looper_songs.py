"""Looper song save/load pane — shares the browse filter carousel slot."""

from __future__ import annotations

import queue
import sys
import threading
from pathlib import Path

import pygame

from patch_browser.geometry import Rect
from patch_browser.scroll_widgets import ScrollList
from patch_browser.touch_keyboard import (
    KeyboardProfile,
    TouchKeyboardLayout,
    draw_touch_keyboard,
)
from patch_browser.touch_ui_constants import (
    BROWSE_EDGE_GRAB_W,
    BROWSE_FILTER_HEADER_H,
    BROWSE_FILTER_TAG_PAD_X,
    TAP_MOVE_THRESHOLD_PX,
)
from patch_browser.touch_ui_enums import Screen
from patch_browser.ui_text import ellipsize_text

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SOOPER = _REPO_ROOT / "scripts" / "sooperlooper"
if str(_SOOPER) not in sys.path:
    sys.path.insert(0, str(_SOOPER))


class TouchBrowserLooperSongsMixin:
    """Mixin — expects TouchPatchBrowser host attributes."""

    def _init_looper_songs_state(self) -> None:
        self.browse_looper_open_btn = Rect(0, 0, 0, 0)
        self._browse_left_pane_mode = "filter"
        self._looper_pane_view = "menu"
        self._looper_song_slugs: list[str] = []
        self._looper_song_labels: list[str] = []
        self._looper_selected_slug: str | None = None
        self._looper_pending_load_slug: str | None = None
        self._looper_after_save_load_slug: str | None = None
        self._looper_busy = False
        self._looper_result_queue: queue.Queue = queue.Queue()
        self._looper_save_btn = Rect(0, 0, 0, 0)
        self._looper_load_btn = Rect(0, 0, 0, 0)
        self._looper_back_btn = Rect(0, 0, 0, 0)
        self._looper_list = ScrollList(Rect(0, 0, 0, 0), row_height=52)
        self._looper_confirm_yes = Rect(0, 0, 0, 0)
        self._looper_confirm_no = Rect(0, 0, 0, 0)
        self._looper_confirm_cancel = Rect(0, 0, 0, 0)
        self._looper_confirm_panel = Rect(0, 0, 0, 0)
        self._looper_confirm_kind = ""
        self._looper_confirm_message = ""
        self._looper_confirm_yes_label = "Yes"
        self._looper_confirm_no_label = "No"
        self._looper_confirm_show_cancel = False
        self._looper_name_text = ""
        self._looper_name_panel = Rect(0, 0, 0, 0)
        self._looper_name_field = Rect(0, 0, 0, 0)
        self._looper_name_ok = Rect(0, 0, 0, 0)
        self._looper_name_cancel = Rect(0, 0, 0, 0)
        self._looper_name_keyboard: TouchKeyboardLayout | None = None
        self._looper_menu_pending: str | None = None
        self._looper_menu_down_pos: tuple[int, int] | None = None
        self._looper_list_down_pos: tuple[int, int] | None = None
        self._looper_touch_pending: str | None = None
        self._looper_touch_down_pos: tuple[int, int] | None = None

    def _open_browse_filter(self) -> None:
        self._browse_left_pane_mode = "filter"
        self._set_browse_stop("filter")

    def _open_browse_looper(self) -> None:
        self._browse_left_pane_mode = "looper"
        self._looper_pane_view = "menu"
        self._set_browse_stop("filter")

    def _toggle_browse_looper(self) -> None:
        if self._browse_left_pane_mode == "looper" and self._browse_carousel.stop == "filter":
            self._close_browse_filter()
            self._browse_left_pane_mode = "filter"
        else:
            self._open_browse_looper()

    def _toggle_browse_filter(self) -> None:
        if self._browse_carousel.stop == "filter" and self._browse_left_pane_mode == "filter":
            self._close_browse_filter()
        else:
            self._open_browse_filter()

    def _layout_looper_pane(self, *, pane: Rect) -> None:
        if pane.w <= 0 or self._browse_left_pane_mode != "looper":
            return
        pad = BROWSE_FILTER_TAG_PAD_X
        x = pane.x + BROWSE_EDGE_GRAB_W + pad
        w = max(0, pane.w - BROWSE_EDGE_GRAB_W - pad * 2)
        y = pane.y + BROWSE_FILTER_HEADER_H + 8
        btn_h = 44
        gap = 10
        back_h = 36
        self._looper_back_btn = Rect(0, 0, 0, 0)
        if self._looper_pane_view in ("save_list", "load_list"):
            self._looper_back_btn = Rect(x, pane.y + 6, 88, back_h)
            list_y = pane.y + BROWSE_FILTER_HEADER_H + 4
        else:
            list_y = y + (btn_h + gap) * 2 + 8
        self._looper_save_btn = Rect(x, y, w, btn_h)
        self._looper_load_btn = Rect(x, y + btn_h + gap, w, btn_h)
        self._looper_list.rect = Rect(x, list_y, w, max(0, pane.bottom - list_y - 8))

    def _refresh_looper_song_list(self) -> None:
        from looper_songs import list_songs

        songs = list_songs()
        self._looper_song_slugs = [s.slug for s in songs]
        self._looper_song_labels = [s.name for s in songs]
        if self._looper_pane_view == "save_list":
            self._looper_list.items = ["+ New song…"] + self._looper_song_labels
        elif self._looper_pane_view == "load_list":
            self._looper_list.items = list(self._looper_song_labels)

    def _draw_looper_pane(self) -> None:
        pane = self.browse_filter_rect
        if pane.w <= 0 or self._browse_left_pane_mode != "looper":
            return
        pygame.draw.rect(self.screen, self.theme.surface, pane.pygame_rect, border_radius=10)
        header_x = pane.x + BROWSE_EDGE_GRAB_W + BROWSE_FILTER_TAG_PAD_X
        if self._looper_pane_view in ("save_list", "load_list"):
            if self._looper_back_btn.w > 0:
                self._draw_button(
                    self._looper_back_btn,
                    "← Back",
                    small=True,
                    pressed=self._pressed("looper:back"),
                )
            title_text = "Save song" if self._looper_pane_view == "save_list" else "Load song"
            title = self.font_sm.render(title_text, True, self.theme.muted)
            title_x = max(header_x, self._looper_back_btn.right + 8)
            self.screen.blit(title, (title_x, pane.y + 14))
        else:
            title = self.font_sm.render("Looper songs", True, self.theme.muted)
            self.screen.blit(title, (header_x, pane.y + 8))

        if self._looper_busy:
            busy = self.font_md.render("Working…", True, self.theme.accent)
            self.screen.blit(
                busy,
                (header_x, pane.y + pane.h // 2 - busy.get_height() // 2),
            )
            return

        if self._looper_pane_view == "menu":
            self._draw_looper_action_btn(self._looper_save_btn, "Save song")
            self._draw_looper_action_btn(self._looper_load_btn, "Load song")
            return

        self._looper_list.draw(self.screen, self.font_md, self.theme)
        hint = "Pick a song to load" if self._looper_pane_view == "load_list" else "Overwrite, or pick New"
        hint_s = self.font_sm.render(hint, True, self.theme.muted)
        self.screen.blit(hint_s, (header_x, self._looper_list.rect.y - hint_s.get_height() - 4))

    def _draw_looper_action_btn(self, rect: Rect, label: str, *, pressed: bool = False) -> None:
        if rect.w <= 0:
            return
        bg = self.theme.accent if pressed else self.theme.surface_alt
        fg = self.theme.bg if pressed else self.theme.text
        pygame.draw.rect(self.screen, bg, rect.pygame_rect, border_radius=10)
        surf = self.font_md.render(label, True, fg)
        self.screen.blit(
            surf,
            (
                rect.x + (rect.w - surf.get_width()) // 2,
                rect.y + (rect.h - surf.get_height()) // 2,
            ),
        )

    def _looper_list_index_to_slug(self, index: int) -> str | None:
        if self._looper_pane_view == "save_list":
            if index == 0:
                return None
            idx = index - 1
        else:
            idx = index
        if idx < 0 or idx >= len(self._looper_song_slugs):
            return None
        return self._looper_song_slugs[idx]

    def _looper_pane_go_back(self) -> None:
        if self._looper_pane_view in ("save_list", "load_list"):
            self._looper_pane_view = "menu"
            self._looper_selected_slug = None
            return
        if self.screen_state == Screen.LOOPER_NAME:
            self.screen_state = Screen.BROWSER
            self._looper_pane_view = "save_list"
            self._refresh_looper_song_list()
            return
        if self.screen_state == Screen.LOOPER_CONFIRM:
            kind = self._looper_confirm_kind
            self._looper_pending_load_slug = None
            self._close_looper_confirm()
            if kind == "load_replace":
                self._looper_pane_view = "load_list"
            elif kind == "overwrite":
                self._looper_pane_view = "save_list"

    def _try_looper_back_tap_at(self, pos: tuple[int, int]) -> bool:
        if (
            self._browse_left_pane_mode != "looper"
            or self._browse_carousel.stop != "filter"
            or self._looper_pane_view not in ("save_list", "load_list")
            or self._looper_busy
        ):
            return False
        if self._looper_back_btn.w > 0 and self._looper_back_btn.contains(*pos):
            self._looper_pane_go_back()
            return True
        return False

    def _apply_looper_menu_tap(self, action: str) -> None:
        if action == "save":
            self._looper_pane_view = "save_list"
            self._refresh_looper_song_list()
            return
        if action == "load":
            self._begin_looper_load_flow()

    def _try_looper_menu_tap_at(self, pos: tuple[int, int]) -> bool:
        if (
            self._browse_left_pane_mode != "looper"
            or self._browse_carousel.stop != "filter"
            or self._looper_pane_view != "menu"
            or self._looper_busy
        ):
            return False
        if self._looper_save_btn.contains(*pos):
            self._apply_looper_menu_tap("save")
            return True
        if self._looper_load_btn.contains(*pos):
            self._apply_looper_menu_tap("load")
            return True
        return False

    def _try_looper_list_tap_at(self, pos: tuple[int, int]) -> bool:
        if (
            self._browse_left_pane_mode != "looper"
            or self._browse_carousel.stop != "filter"
            or self._looper_pane_view not in ("save_list", "load_list")
            or self._looper_busy
        ):
            return False
        idx = self._looper_list.item_at(*pos)
        if idx is None:
            return False
        self._on_looper_list_tap(idx)
        return True

    def _try_looper_confirm_tap_at(self, pos: tuple[int, int]) -> bool:
        if self.screen_state != Screen.LOOPER_CONFIRM:
            return False
        hit = self._looper_confirm_hit_at(pos)
        if hit is None:
            return False
        self._apply_looper_confirm_hit(hit)
        return True

    def _try_looper_name_tap_at(self, pos: tuple[int, int]) -> bool:
        if self.screen_state != Screen.LOOPER_NAME:
            return False
        hit = self._looper_name_hit_at(pos)
        if hit is None:
            return False
        self._apply_looper_name_hit(hit)
        return True

    def _clear_looper_touch(self) -> None:
        self._looper_touch_pending = None
        self._looper_touch_down_pos = None
        self._touch_press.clear()

    def _looper_touch_down(self, pos: tuple[int, int], hit: str | None) -> None:
        self._looper_touch_down_pos = pos
        self._looper_touch_pending = hit
        if hit:
            self._touch_press.set(hit)

    def _looper_touch_up(
        self,
        pos: tuple[int, int],
        *,
        hit_at,
        apply_hit,
        outside_panel: Rect | None = None,
        on_outside=None,
    ) -> None:
        pending = self._looper_touch_pending
        down = self._looper_touch_down_pos
        self._clear_looper_touch()
        if outside_panel is not None and outside_panel.w > 0 and not outside_panel.contains(*pos):
            if on_outside is not None:
                on_outside()
            return
        moved = (
            down is not None
            and self._pointer_move_distance(down, pos) > TAP_MOVE_THRESHOLD_PX
        )
        hit = hit_at(pos) if not moved else None
        if hit is None and not moved:
            hit = pending
        if hit is None:
            hit = hit_at(pos)
        if hit:
            apply_hit(hit)

    def _resolve_looper_list_tap_index(self, pos: tuple[int, int]) -> int | None:
        self._looper_list.pointer_up(pos)
        idx = self._looper_list.take_tap_index()
        if idx is not None:
            return idx
        down = self._looper_list_down_pos
        if down is not None:
            if self._pointer_move_distance(down, pos) > TAP_MOVE_THRESHOLD_PX:
                return None
            if self._looper_list.rect.contains(*down):
                return self._looper_list.item_at(*down)
        if self._looper_list.rect.contains(*pos):
            return self._looper_list.item_at(*pos)
        return None

    def _handle_looper_pane_pointer_down(self, pos: tuple[int, int]) -> bool:
        if self._browse_left_pane_mode != "looper":
            return False
        if self._browse_filter_rect.w <= 0 or not self._browse_filter_rect.contains(*pos):
            return False
        if self._looper_busy:
            return True
        if self._try_looper_back_tap_at(pos):
            self._touch_press.set("looper:back")
            return True
        if self._looper_pane_view == "menu":
            self._looper_menu_pending = None
            self._looper_menu_down_pos = pos
            if self._looper_save_btn.contains(*pos):
                self._looper_menu_pending = "save"
                self._touch_press.set("looper:save")
                return True
            if self._looper_load_btn.contains(*pos):
                self._looper_menu_pending = "load"
                self._touch_press.set("looper:load")
                return True
            return True
        self._looper_list_down_pos = pos
        if self._looper_list.pointer_down(pos):
            return True
        return True

    def _handle_looper_pane_pointer_up(self, pos: tuple[int, int]) -> bool:
        if self._browse_left_pane_mode != "looper":
            return False
        if self._looper_pane_view != "menu":
            pending_back = self._pressed("looper:back")
            self._touch_press.clear()
            idx = self._resolve_looper_list_tap_index(pos)
            self._looper_list_down_pos = None
            self._looper_menu_pending = None
            self._looper_menu_down_pos = None
            if pending_back and self._looper_back_btn.contains(*pos):
                self._looper_pane_go_back()
                return True
            if idx is not None:
                self._on_looper_list_tap(idx)
            return True
        pending = self._looper_menu_pending
        down_pos = self._looper_menu_down_pos
        self._looper_menu_pending = None
        self._looper_menu_down_pos = None
        self._touch_press.clear()
        moved = (
            down_pos is not None
            and self._pointer_move_distance(down_pos, pos) > TAP_MOVE_THRESHOLD_PX
        )
        if not moved:
            if pending == "save" and self._looper_save_btn.contains(*pos):
                self._apply_looper_menu_tap("save")
                return True
            if pending == "load" and self._looper_load_btn.contains(*pos):
                self._apply_looper_menu_tap("load")
                return True
        if self._looper_save_btn.contains(*pos):
            self._apply_looper_menu_tap("save")
            return True
        if self._looper_load_btn.contains(*pos):
            self._apply_looper_menu_tap("load")
            return True
        return self._browse_left_pane_mode == "looper"

    def _on_looper_list_tap(self, index: int) -> None:
        if self._looper_pane_view == "save_list":
            if index == 0:
                self._open_looper_name_prompt()
                return
            slug = self._looper_list_index_to_slug(index)
            if slug is None:
                return
            self._looper_selected_slug = slug
            self._looper_confirm_kind = "overwrite"
            self._open_looper_confirm(
                f"Overwrite '{self._looper_song_labels[index - 1]}'?",
                yes_label="Overwrite",
            )
            return
        slug = self._looper_list_index_to_slug(index)
        if slug is None:
            return
        self._begin_looper_load_slug(slug)

    def _begin_looper_load_flow(self) -> None:
        self._refresh_looper_song_list()
        if not self._looper_song_slugs:
            self._toast("No saved songs yet", 2.0)
            return
        self._looper_pane_view = "load_list"
        self._looper_list.items = list(self._looper_song_labels)

    def _begin_looper_load_slug(self, slug: str) -> None:
        if self._looper_busy:
            return

        self._looper_busy = True
        self._toast("Checking…", 1.5)

        def _worker() -> None:
            from looper_songs import SongResult, load_song, run_with_probe, session_has_content

            check = run_with_probe(
                lambda p: SongResult(ok=session_has_content(p), message="")
            )
            if check.message:
                self._looper_result_queue.put(check)
                return
            if check.ok:
                self._looper_result_queue.put(("confirm_load", slug))
            else:
                self._looper_result_queue.put(run_with_probe(lambda p: load_song(p, slug)))

        threading.Thread(
            target=_worker, daemon=True, name="LooperLoadPreflight"
        ).start()

    def _open_looper_confirm(
        self,
        message: str,
        *,
        yes_label: str = "Yes",
        no_label: str = "No",
        show_cancel: bool = False,
    ) -> None:
        self._looper_confirm_message = message
        self._looper_confirm_yes_label = yes_label
        self._looper_confirm_no_label = no_label
        self._looper_confirm_show_cancel = show_cancel
        margin = 24
        panel_w = min(480, self.width - margin * 2)
        panel_h = 220 if show_cancel else 200
        self._looper_confirm_panel = Rect(
            (self.width - panel_w) // 2,
            (self.height - panel_h) // 2,
            panel_w,
            panel_h,
        )
        btn_w = (panel_w - 48) // (3 if show_cancel else 2)
        btn_y = self._looper_confirm_panel.bottom - 52
        x0 = self._looper_confirm_panel.x + 16
        if show_cancel:
            self._looper_confirm_yes = Rect(x0, btn_y, btn_w, 40)
            self._looper_confirm_no = Rect(x0 + btn_w + 8, btn_y, btn_w, 40)
            self._looper_confirm_cancel = Rect(x0 + (btn_w + 8) * 2, btn_y, btn_w, 40)
        else:
            self._looper_confirm_cancel = Rect(0, 0, 0, 0)
            self._looper_confirm_yes = Rect(x0, btn_y, btn_w, 40)
            self._looper_confirm_no = Rect(x0 + btn_w + 16, btn_y, btn_w, 40)
        self.screen_state = Screen.LOOPER_CONFIRM

    def _draw_looper_confirm_modal(self) -> None:
        self._draw_modal_backdrop(legacy_alpha=160)
        panel = self._looper_confirm_panel
        self._draw_modal_shell(panel, border_radius=14)
        y = panel.y + 20
        for line in self._looper_confirm_message.split("\n"):
            surf = self.font_md.render(line, True, self.theme.text)
            self.screen.blit(surf, (panel.x + 20, y))
            y += surf.get_height() + 4
        self._draw_looper_action_btn(
            self._looper_confirm_yes,
            self._looper_confirm_yes_label,
            pressed=self._pressed("looper:confirm:yes"),
        )
        self._draw_looper_action_btn(
            self._looper_confirm_no,
            self._looper_confirm_no_label,
            pressed=self._pressed("looper:confirm:no"),
        )
        if self._looper_confirm_show_cancel:
            self._draw_looper_action_btn(
                self._looper_confirm_cancel,
                "Cancel",
                pressed=self._pressed("looper:confirm:cancel"),
            )

    def _looper_confirm_hit_at(self, pos: tuple[int, int]) -> str | None:
        if self._looper_confirm_yes.contains(*pos):
            return "looper:confirm:yes"
        if self._looper_confirm_no.contains(*pos):
            return "looper:confirm:no"
        if self._looper_confirm_cancel.w > 0 and self._looper_confirm_cancel.contains(*pos):
            return "looper:confirm:cancel"
        return None

    def _close_looper_confirm(self) -> None:
        self.screen_state = Screen.BROWSER
        self._looper_confirm_kind = ""

    def _apply_looper_confirm_hit(self, hit: str) -> None:
        kind = self._looper_confirm_kind
        if hit == "looper:confirm:cancel":
            self._looper_pane_go_back()
            return
        if hit == "looper:confirm:no" and kind == "overwrite":
            self._looper_pane_go_back()
            return
        if kind == "overwrite" and hit == "looper:confirm:yes":
            slug = self._looper_selected_slug
            self._close_looper_confirm()
            if slug:
                name = next(
                    (n for n, s in zip(self._looper_song_labels, self._looper_song_slugs) if s == slug),
                    slug,
                )
                self._run_looper_save(name, overwrite=True)
            return
        if kind == "load_replace":
            slug = self._looper_pending_load_slug
            if hit == "looper:confirm:yes":
                self._close_looper_confirm()
                self._looper_pane_view = "save_list"
                self._open_looper_name_prompt(after_save_load_slug=slug)
                return
            if hit == "looper:confirm:no" and slug:
                self._close_looper_confirm()
                self._run_looper_load(slug)
                return
        self._close_looper_confirm()

    def _open_looper_name_prompt(self, *, after_save_load_slug: str | None = None) -> None:
        self._looper_after_save_load_slug = after_save_load_slug
        self._looper_name_text = ""
        self.screen_state = Screen.LOOPER_NAME

    def _looper_name_active_key(self) -> str | None:
        prefix = "looper:name:"
        active = self._touch_press.active_id
        if active and active.startswith(prefix):
            return active[len(prefix) :]
        return None

    def _draw_looper_name_modal(self) -> None:
        self._draw_modal_backdrop(legacy_alpha=150)
        panel_w = min(560, self.width - 24)
        panel_h = min(self.height - 16, 430)
        panel = Rect((self.width - panel_w) // 2, (self.height - panel_h) // 2, panel_w, panel_h)
        self._looper_name_panel = panel
        self._draw_modal_shell(panel, border_radius=16)

        inner_x = panel.x + 16
        inner_w = panel.w - 32
        y = panel.y + 14
        self.screen.blit(self.font_md.render("Song name", True, self.theme.text), (inner_x, y))
        y += self.font_md.get_height() + 2
        hint = self.font_sm.render("Enter a name for this song", True, self.theme.muted)
        self.screen.blit(hint, (inner_x, y))
        y += hint.get_height() + 8

        field_h = 44
        self._looper_name_field = Rect(inner_x, y, inner_w, field_h)
        pygame.draw.rect(
            self.screen, self.theme.surface_alt, self._looper_name_field.pygame_rect, border_radius=8
        )
        shown = self._looper_name_text or "Enter name…"
        color = self.theme.muted if not self._looper_name_text else self.theme.text
        clipped = ellipsize_text(self.font_md, shown, max(1, self._looper_name_field.w - 16))
        surf = self.font_md.render(clipped, True, color)
        self.screen.blit(
            surf,
            (
                self._looper_name_field.x + 8,
                self._looper_name_field.y + (self._looper_name_field.h - surf.get_height()) // 2,
            ),
        )
        y += field_h + 8

        btn_h = 44
        btn_gap = 10
        btn_w = (inner_w - btn_gap) // 2
        btn_y = panel.bottom - 16 - btn_h
        self._looper_name_cancel = Rect(inner_x, btn_y, btn_w, btn_h)
        self._looper_name_ok = Rect(inner_x + btn_w + btn_gap, btn_y, btn_w, btn_h)
        pressed_key = self._looper_name_active_key()
        self._draw_button(
            self._looper_name_cancel,
            "Back",
            pressed=pressed_key == "cancel",
        )
        self._draw_button(
            self._looper_name_ok,
            "Save",
            accent=True,
            pressed=pressed_key == "save",
        )

        keyboard_panel = Rect(inner_x, y, inner_w, btn_y - y - 8)
        self._looper_name_keyboard = TouchKeyboardLayout(
            keyboard_panel,
            profile=KeyboardProfile.TEXT,
        )
        draw_touch_keyboard(
            self._looper_name_keyboard,
            draw_button=self._draw_button,
            pressed_key=pressed_key,
        )

    def _looper_name_hit_at(self, pos: tuple[int, int]) -> str | None:
        ok = getattr(self, "_looper_name_ok", None)
        if ok is not None and ok.contains(*pos):
            return "looper:name:save"
        cancel = getattr(self, "_looper_name_cancel", None)
        if cancel is not None and cancel.contains(*pos):
            return "looper:name:cancel"
        keyboard = getattr(self, "_looper_name_keyboard", None)
        if keyboard is not None:
            key = keyboard.hit(pos)
            if key is not None:
                return f"looper:name:{key}"
        return None

    def _apply_looper_name_hit(self, hit: str) -> None:
        if hit == "looper:name:save":
            name = self._looper_name_text.strip()
            if not name:
                self._toast("Name required", 2.0)
                return
            slug_after = self._looper_after_save_load_slug
            self.screen_state = Screen.BROWSER
            self._run_looper_save(name, overwrite=False, then_load_slug=slug_after)
            return
        if hit == "looper:name:cancel":
            self._looper_pane_go_back()
            return
        if hit.startswith("looper:name:"):
            key = hit[len("looper:name:") :]
            if key == "backspace":
                self._looper_name_text = self._looper_name_text[:-1]
            elif key == " ":
                self._looper_name_text += " "
            elif key:
                self._looper_name_text += key

    def _run_looper_save(
        self,
        name: str,
        *,
        overwrite: bool = False,
        then_load_slug: str | None = None,
    ) -> None:
        if self._looper_busy:
            return

        self._looper_busy = True
        self._toast("Saving…", 2.0)

        def _worker() -> None:
            from looper_songs import load_song, run_with_probe, save_song

            result = run_with_probe(lambda p: save_song(p, name, overwrite=overwrite))
            if result.ok and then_load_slug:
                result = run_with_probe(lambda p: load_song(p, then_load_slug))
            self._looper_result_queue.put(result)

        threading.Thread(target=_worker, daemon=True, name="LooperSave").start()

    def _run_looper_load(self, slug: str) -> None:
        if self._looper_busy:
            return

        self._looper_busy = True
        self._toast("Loading…", 2.0)

        def _worker() -> None:
            from looper_songs import load_song, run_with_probe

            self._looper_result_queue.put(run_with_probe(lambda p: load_song(p, slug)))

        threading.Thread(target=_worker, daemon=True, name="LooperLoad").start()

    def _poll_looper_song_results(self) -> None:
        if not self._looper_busy:
            return
        try:
            result = self._looper_result_queue.get_nowait()
        except queue.Empty:
            return
        self._looper_busy = False

        if isinstance(result, tuple) and result[0] == "confirm_load":
            slug = result[1]
            self._looper_confirm_kind = "load_replace"
            self._looper_pending_load_slug = slug
            self._open_looper_confirm(
                "Current loops will be replaced.\nSave them first?",
                yes_label="Save first",
                no_label="Discard & load",
                show_cancel=True,
            )
            return

        self._looper_pane_view = "menu"
        self._looper_pending_load_slug = None
        self._refresh_looper_song_list()
        if result.ok:
            self._toast(result.message, 3.0)
        else:
            self._toast(result.message, 4.0)

    def _handle_looper_confirm_pointer_down(self, pos: tuple[int, int]) -> None:
        panel = self._looper_confirm_panel
        if panel.w > 0 and not panel.contains(*pos):
            return
        self._looper_touch_down(pos, self._looper_confirm_hit_at(pos))

    def _handle_looper_confirm_pointer_up(self, pos: tuple[int, int]) -> None:
        self._looper_touch_up(
            pos,
            hit_at=self._looper_confirm_hit_at,
            apply_hit=self._apply_looper_confirm_hit,
            outside_panel=self._looper_confirm_panel,
        )

    def _handle_looper_name_pointer_down(self, pos: tuple[int, int]) -> None:
        panel = getattr(self, "_looper_name_panel", None)
        if panel is not None and panel.w > 0 and not panel.contains(*pos):
            return
        self._clear_modal_pointer()
        self._modal_press_hit(pos, self._looper_name_hit_at(pos))

    def _handle_looper_name_pointer_up(self, pos: tuple[int, int]) -> None:
        panel = getattr(self, "_looper_name_panel", None)
        if panel is not None and panel.w > 0 and not panel.contains(*pos):
            self._looper_pane_go_back()
            self._clear_modal_pointer()
            return
        hit = self._modal_release_hit(pos)
        if hit:
            self._apply_looper_name_hit(hit)

    def _handle_browse_pointer_down(self, pos: tuple[int, int]) -> bool:
        if (
            self._browse_left_pane_mode == "looper"
            and self._browse_carousel.stop == "filter"
            and self._handle_looper_pane_pointer_down(pos)
        ):
            return True
        return super()._handle_browse_pointer_down(pos)

    def _handle_browse_pointer_move(self, pos: tuple[int, int]) -> bool:
        if self._browse_left_pane_mode == "looper" and self._browse_carousel.stop == "filter":
            if self._looper_pane_view != "menu":
                self._looper_list.pointer_move(pos)
            return True
        return super()._handle_browse_pointer_move(pos)

    def _handle_browse_pointer_up(self, pos: tuple[int, int]) -> bool:
        if (
            self._browse_left_pane_mode == "looper"
            and self._browse_carousel.stop == "filter"
            and self._handle_looper_pane_pointer_up(pos)
        ):
            return True
        return super()._handle_browse_pointer_up(pos)
