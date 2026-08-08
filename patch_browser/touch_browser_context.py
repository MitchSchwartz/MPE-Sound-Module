"""Touch patch browser — long-press context menus (Phase 5)."""

from __future__ import annotations

import time

import pygame

from patch_browser.context_menu import (
    ContextTarget,
    build_context_actions,
    folder_picker_actions,
    instrument_picker_actions,
    is_qa_browse,
    qa_folder_display_name,
)
from patch_browser.favorites_index import qa_folder_key_for_library, qa_folder_key_from_target_inner
from patch_browser.geometry import Rect
from patch_browser.instrument_filter import patches_in_browse_subtree
from patch_browser.scroll_widgets import ScrollableActionList
from patch_browser.touch_keyboard import TouchKeyboardLayout
from patch_browser.touch_ui_constants import (
    BROWSER_BOTTOM_MARGIN,
    LONG_PRESS_S,
    SETTINGS_ROW_GAP,
    SETTINGS_ROW_H,
    TAP_MOVE_THRESHOLD_PX,
)
from patch_browser.touch_ui_enums import LeftNavMode, Screen


class TouchBrowserContextMixin:
    """Long-press menus, folder/instrument pickers, and QA folder management."""

    def _init_context_menu_state(self) -> None:
        self._long_press_pending: dict | None = None
        self._context_target: ContextTarget | None = None
        self._context_menu_view: str = "main"
        self._context_menu_actions: list[tuple[str, str]] = []
        self._context_action_sheet = ScrollableActionList()
        self._context_menu_panel = Rect(0, 0, 0, 0)
        self._context_menu_title = ""
        self._name_prompt_text = ""
        self._name_prompt_kind = ""
        self._name_prompt_panel = Rect(0, 0, 0, 0)
        self._name_prompt_keyboard: TouchKeyboardLayout | None = None
        self._name_prompt_ok: Rect | None = None
        self._name_prompt_cancel: Rect | None = None
        self._name_prompt_field = Rect(0, 0, 0, 0)
        self._context_menu_ignore_next_up = False

    def _cancel_long_press(self) -> None:
        self._long_press_pending = None

    def _context_target_for_nav_index(self, index: int) -> ContextTarget | None:
        if self.left_nav_mode == LeftNavMode.FOLDERS:
            if index < 0 or index >= len(self.categories):
                return None
            category = self.categories[index]
            if is_qa_browse(category):
                return ContextTarget(kind="qa_folder", category=category, inner_segments=())
            return ContextTarget(kind="library_folder", category=category, inner_segments=())
        if self.left_nav_mode == LeftNavMode.PATCHES:
            if index < 0 or index >= len(self._browse_nav_entries):
                return None
            entry = self._browse_nav_entries[index]
            category = self._browse_category_name()
            inner = self._browse_inner_segments()
            if entry["kind"] == "folder":
                name = entry["name"]
                if is_qa_browse(category):
                    return ContextTarget(
                        kind="qa_folder",
                        category=category,
                        inner_segments=inner + (name,),
                        folder_name=name,
                    )
                return ContextTarget(
                    kind="library_folder",
                    category=category,
                    inner_segments=inner + (name,),
                    folder_name=name,
                )
            return ContextTarget(
                kind="patch",
                category=category,
                inner_segments=inner,
                patch=dict(entry["patch"]),
            )
        if self.left_nav_mode == LeftNavMode.ALL_PATCHES:
            if index < 0 or index >= len(self._all_patches_display_flat):
                return None
            return ContextTarget(
                kind="patch",
                patch=dict(self._all_patches_display_flat[index]),
            )
        return None

    def _context_nav_pointer_down(self, pos: tuple[int, int]) -> bool:
        if (
            self.left_nav_collapsed
            or self.screen_state != Screen.BROWSER
            or not self.nav_list.rect.contains(*pos)
        ):
            return False
        index = self.nav_list.item_at(*pos)
        if index is None:
            return False
        target = self._context_target_for_nav_index(index)
        if target is None:
            return False
        self._long_press_pending = {
            "started": time.time(),
            "origin": pos,
            "target": target,
            "index": index,
        }
        return True

    def _context_nav_pointer_move(self, pos: tuple[int, int]) -> bool:
        pending = self._long_press_pending
        if pending is None:
            return False
        if self._pointer_move_distance(pending["origin"], pos) > TAP_MOVE_THRESHOLD_PX:
            self._cancel_long_press()
            return True
        if getattr(self.nav_list, "_pointer_scrolled", False):
            self._cancel_long_press()
            return True
        return False

    def _context_nav_pointer_up(self) -> bool:
        had = self._long_press_pending is not None
        self._cancel_long_press()
        return had and self.screen_state == Screen.CONTEXT_MENU

    def _tick_long_press(self) -> None:
        pending = self._long_press_pending
        if pending is None or self.screen_state != Screen.BROWSER:
            return
        if getattr(self.nav_list, "_pointer_scrolled", False):
            self._cancel_long_press()
            return
        if time.time() - pending["started"] >= LONG_PRESS_S:
            self._open_context_menu(pending["target"])
            self._cancel_long_press()

    def _layout_context_menu(self) -> None:
        self._context_action_sheet.layout(
            screen_w=self.width,
            screen_h=self.height,
            margin=16,
            bottom_margin=BROWSER_BOTTOM_MARGIN,
            actions=self._context_menu_actions,
            row_h=SETTINGS_ROW_H,
            section_h=28,
            gap=SETTINGS_ROW_GAP,
        )
        self._context_menu_panel = self._context_action_sheet.panel

    def _open_context_menu(self, target: ContextTarget) -> None:
        self.nav_list.stop_momentum()
        self.nav_list.cancel_active_pointer()
        self._context_target = target
        self._context_menu_view = "main"
        favorited = False
        qa_patch_count = 0
        if target.kind == "patch" and target.patch:
            favorited = self._patch_is_favorited(target.patch)
        if target.kind == "qa_folder":
            qa_patch_count = len(
                patches_in_browse_subtree(
                    self.scanner,
                    target.category,
                    target.inner_segments,
                )
            )
        self._context_menu_actions = build_context_actions(
            target,
            is_favorited=favorited,
            qa_patch_count=qa_patch_count,
        )
        if not self._context_menu_actions:
            return
        self._context_menu_title = self._context_menu_heading(target)
        self._layout_context_menu()
        self._context_menu_ignore_next_up = True
        self.screen_state = Screen.CONTEXT_MENU

    def _context_menu_heading(self, target: ContextTarget) -> str:
        if target.kind == "patch" and target.patch:
            return target.patch.get("name", "Patch")
        if target.kind == "library_folder":
            parts = [target.category, *target.inner_segments]
            return " / ".join(parts)
        return qa_folder_display_name(target)

    def _close_context_menu(self) -> None:
        self._cancel_long_press()
        self.nav_list.cancel_active_pointer()
        self._clear_modal_pointer()
        self._context_action_sheet.pressed_action_id = None
        self._context_action_sheet.scroll.reset()
        self.screen_state = Screen.BROWSER
        self._context_target = None
        self._context_menu_view = "main"
        self._context_menu_actions = []
        self._context_menu_ignore_next_up = False

    def _open_folder_picker(self, action_prefix: str) -> None:
        folders = self.scanner.favorites_index.folders
        self._context_menu_view = action_prefix
        self._context_menu_actions = folder_picker_actions(folders)
        self._context_menu_title = "Choose folder"
        self._layout_context_menu()

    def _open_instrument_picker(self) -> None:
        self._context_menu_view = "instrument_pick"
        self._context_menu_actions = instrument_picker_actions()
        self._context_menu_title = "Set instrument"
        self._layout_context_menu()

    def _patches_for_library_target(self, target: ContextTarget) -> list[dict]:
        inner = target.inner_segments
        category = target.category
        if not category and target.kind == "library_folder":
            return []
        return patches_in_browse_subtree(self.scanner, category, inner)

    def _restore_context_main_menu(self) -> None:
        target = self._context_target
        if target is None:
            self._close_context_menu()
            return
        favorited = False
        qa_patch_count = 0
        if target.kind == "patch" and target.patch:
            favorited = self._patch_is_favorited(target.patch)
        if target.kind == "qa_folder":
            qa_patch_count = len(
                patches_in_browse_subtree(
                    self.scanner,
                    target.category,
                    target.inner_segments,
                )
            )
        self._context_menu_view = "main"
        self._context_menu_actions = build_context_actions(
            target,
            is_favorited=favorited,
            qa_patch_count=qa_patch_count,
        )
        self._context_menu_title = self._context_menu_heading(target)
        self._layout_context_menu()

    def _execute_context_action(self, action_id: str) -> None:
        if action_id == "_section":
            return
        target = self._context_target
        if target is None:
            self._close_context_menu()
            return

        if action_id == "add_all_qa":
            patches = self._patches_for_library_target(target)
            folder_key = qa_folder_key_for_library(target.category, target.inner_segments)
            added, skipped = self.scanner.add_patches_to_favorites(
                patches,
                folder=folder_key,
            )
            self._sync_categories_after_favorites_change()
            self._close_context_menu()
            label = folder_key or "Quick Select"
            self._toast(f"Added {added} to {label} ({skipped} skipped)", 2.5)
            return

        if action_id == "favorite_qa":
            patch = target.patch
            if patch and self.scanner.add_patch_to_favorites(patch):
                self._sync_categories_after_favorites_change()
                self._toast("Added to Quick Select", 2.0)
            else:
                self._toast("Could not add to Quick Select", 2.5)
            self._close_context_menu()
            return

        if action_id == "unfavorite":
            patch = target.patch
            name = (patch or {}).get("name", "this patch")
            self._context_menu_view = "unfavorite_confirm"
            self._context_menu_actions = [
                ("unfavorite_confirm", f"Remove '{name}'"),
                ("qa_cancel", "Cancel"),
            ]
            self._context_menu_title = "Remove from Quick Access?"
            self._layout_context_menu()
            return

        if action_id == "unfavorite_confirm":
            patch = target.patch
            if patch and self.scanner.remove_patch_from_favorites(patch):
                self._sync_categories_after_favorites_change()
                self._toast("Removed from Quick Access", 2.0)
            else:
                self._toast("Could not remove", 2.5)
            self._close_context_menu()
            return

        if action_id == "add_pick_folder":
            self._open_folder_picker("add_patch_folder")
            return

        if action_id == "move_pick_folder":
            self._open_folder_picker("move_patch_folder")
            return

        if action_id == "set_instrument_pick":
            self._open_instrument_picker()
            return

        if action_id == "qa_new_subfolder":
            self._open_name_prompt("qa_new")
            return

        if action_id == "qa_rename":
            self._open_name_prompt("qa_rename")
            return

        if action_id == "qa_remove_all":
            count = len(
                patches_in_browse_subtree(
                    self.scanner,
                    target.category,
                    target.inner_segments,
                )
            )
            folder_label = qa_folder_display_name(target)
            self._context_menu_view = "qa_remove_all_confirm"
            self._context_menu_actions = [
                ("qa_remove_all_confirm", f"Remove {count} patches"),
                ("qa_cancel", "Cancel"),
            ]
            self._context_menu_title = f"Remove all from {folder_label}?"
            self._layout_context_menu()
            return

        if action_id == "qa_remove_all_confirm":
            patches = patches_in_browse_subtree(
                self.scanner,
                target.category,
                target.inner_segments,
            )
            removed = self.scanner.remove_patches_from_favorites_bulk(patches)
            self._sync_categories_after_favorites_change()
            self._close_context_menu()
            self._toast(f"Removed {removed} patches", 2.0)
            return

        if action_id == "qa_delete":
            name = target.folder_name.strip()
            self._context_menu_view = "qa_delete_confirm"
            self._context_menu_actions = [
                ("qa_delete_confirm", f"Delete '{name}'"),
                ("qa_cancel", "Cancel"),
            ]
            self._context_menu_title = f"Delete {name}?"
            self._layout_context_menu()
            return

        if action_id == "qa_delete_confirm":
            self._delete_qa_folder(target)
            self._close_context_menu()
            return

        if action_id == "qa_cancel":
            self._restore_context_main_menu()
            return

        if action_id.startswith("pick_folder:"):
            folder = action_id.split(":", 1)[1]
            view = self._context_menu_view
            if view == "add_patch_folder" and target.patch:
                ok = self.scanner.add_patch_to_favorites(target.patch, folder=folder)
                self._sync_categories_after_favorites_change()
                self._close_context_menu()
                self._toast(
                    f"Added to {folder}" if ok else "Could not add to Quick Access",
                    2.0,
                )
            elif view == "move_patch_folder" and target.patch:
                ok = self.scanner.move_patch_to_favorites_folder(target.patch, folder)
                self._sync_categories_after_favorites_change()
                self._close_context_menu()
                self._toast(
                    f"Moved to {folder}" if ok else "Could not move copy",
                    2.0,
                )
            return

        if action_id.startswith("pick_instrument:"):
            instrument = action_id.split(":", 1)[1]
            patch = target.patch
            if patch:
                stable_key = patch.get("stable_key") or self.scanner._stable_key_for_path(
                    patch.get("path", "")
                )
                if stable_key:
                    self.scanner.metadata_index.set_user_instrument(
                        stable_key,
                        instrument,
                        patch=patch,
                    )
                    self.scanner.metadata_index.enrich_patch(patch)
                    if self.detail_patch and self.detail_patch.get("path") == patch.get("path"):
                        self.detail_patch = dict(patch)
                    if self.loaded_patch_info and self.loaded_patch_info.get("path") == patch.get(
                        "path"
                    ):
                        self.loaded_patch_info = dict(patch)
            self._close_context_menu()
            self._refresh_lists()
            self._toast(f"Instrument → {instrument}", 2.0)
            return

    def _qa_parent_path(self, target: ContextTarget):
        qa_root = self.scanner.get_favorites_folder_path()
        return qa_root.joinpath(*target.inner_segments)

    def _delete_qa_folder(self, target: ContextTarget) -> None:
        folder_key = qa_folder_key_from_target_inner(target.inner_segments) or target.folder_name.strip()
        if not folder_key:
            self._toast("Select a subfolder to delete", 2.0)
            return
        try:
            self.scanner.favorites_index.delete_folder(
                folder_key,
                qa_root=self.scanner.get_favorites_folder_path(),
            )
            self.scanner.favorites_index.save()
            self._pop_browse_after_folder_delete(folder_key)
            self._sync_categories_after_favorites_change()
            self._toast(f"Deleted {target.folder_name or folder_key}", 2.0)
        except ValueError as exc:
            self._toast(str(exc), 3.0)

    def _open_name_prompt(self, kind: str) -> None:
        target = self._context_target
        if target is None:
            return
        self._name_prompt_kind = kind
        self._name_prompt_text = target.folder_name if kind == "qa_rename" else ""
        margin = 16
        panel_w = self.width - margin * 2
        panel_h = min(360, self.height - margin * 2)
        self._name_prompt_panel = Rect(margin, self.height - panel_h - BROWSER_BOTTOM_MARGIN, panel_w, panel_h)
        field_h = 40
        self._name_prompt_field = Rect(
            self._name_prompt_panel.x + 16,
            self._name_prompt_panel.y + 52,
            panel_w - 32,
            field_h,
        )
        kb_top = self._name_prompt_field.bottom + 12
        kb_panel = Rect(
            self._name_prompt_panel.x + 8,
            kb_top,
            panel_w - 16,
            self._name_prompt_panel.bottom - kb_top - 56,
        )
        self._name_prompt_keyboard = TouchKeyboardLayout(kb_panel, row_h=32, row_gap=4, key_gap=3)
        btn_y = self._name_prompt_panel.bottom - 48
        btn_w = (panel_w - 48) // 2
        self._name_prompt_cancel = Rect(self._name_prompt_panel.x + 16, btn_y, btn_w, 40)
        self._name_prompt_ok = Rect(self._name_prompt_cancel.right + 16, btn_y, btn_w, 40)
        title = "New subfolder" if kind == "qa_new" else "Rename folder"
        self._context_menu_title = title
        self.screen_state = Screen.NAME_PROMPT

    def _close_name_prompt(self) -> None:
        self.screen_state = Screen.CONTEXT_MENU
        self._name_prompt_text = ""
        self._name_prompt_kind = ""

    def _commit_name_prompt(self) -> None:
        name = self._name_prompt_text.strip()
        target = self._context_target
        if not name or target is None:
            self._toast("Name required", 2.0)
            return
        qa_root = self.scanner.get_favorites_folder_path()
        index = self.scanner.favorites_index
        kind = self._name_prompt_kind
        try:
            if kind == "qa_new":
                parent = self._qa_parent_path(target)
                parent.mkdir(parents=True, exist_ok=True)
                path = parent / name
                if path.exists():
                    raise ValueError(f"folder already exists: {name}")
                path.mkdir(parents=True, exist_ok=True)
                if not target.inner_segments:
                    index.ensure_folder(name)
                index.save()
                self._sync_categories_after_favorites_change()
                self._toast(f"Created {name}", 2.0)
            elif kind == "qa_rename":
                old = target.folder_name.strip()
                index.rename_folder(old, name, qa_root=qa_root)
                index.save()
                self._sync_categories_after_favorites_change()
                self._toast(f"Renamed to {name}", 2.0)
        except ValueError as exc:
            self._toast(str(exc), 3.0)
            return
        self._close_name_prompt()
        self._close_context_menu()

    def _name_prompt_hit_at(self, pos: tuple[int, int]) -> str | None:
        if self._name_prompt_ok and self._name_prompt_ok.contains(*pos):
            return "name:save"
        if self._name_prompt_cancel and self._name_prompt_cancel.contains(*pos):
            return "name:cancel"
        kb = self._name_prompt_keyboard
        if kb is None:
            return None
        hit = kb.hit(pos)
        if hit is None:
            return None
        return f"name:key:{hit}"

    def _apply_name_prompt_hit(self, hit: str) -> None:
        if hit == "name:save":
            self._commit_name_prompt()
        elif hit == "name:cancel":
            self._close_name_prompt()
        elif hit.startswith("name:key:"):
            key = hit.split(":", 2)[2]
            if key == "backspace":
                self._name_prompt_text = self._name_prompt_text[:-1]
            elif key == " ":
                self._name_prompt_text += " "
            elif key:
                self._name_prompt_text += key

    def _handle_name_prompt_pointer_down(self, pos: tuple[int, int]) -> None:
        panel = getattr(self, "_name_prompt_panel", None)
        if panel is not None and not panel.contains(*pos):
            return
        self._clear_modal_pointer()
        hit = self._name_prompt_hit_at(pos)
        self._modal_press_hit(pos, hit)

    def _handle_name_prompt_pointer_up(self, pos: tuple[int, int]) -> None:
        panel = getattr(self, "_name_prompt_panel", None)
        if panel is not None and not panel.contains(*pos):
            self._close_name_prompt()
            self._clear_modal_pointer()
            return
        if (
            self._modal_pending_key is None
            or self._pointer_move_distance(self._modal_pointer_down_pos, pos) > TAP_MOVE_THRESHOLD_PX
        ):
            self._clear_modal_pointer()
            return
        self._apply_name_prompt_hit(self._modal_pending_key)
        self._clear_modal_pointer()

    def _handle_name_prompt_tap(self, pos: tuple[int, int]) -> None:
        self._handle_name_prompt_pointer_up(pos)

    def _handle_context_menu_pointer_down(self, pos: tuple[int, int]) -> None:
        if self._context_menu_ignore_next_up:
            return
        self._context_action_sheet.pointer_down(pos)

    def _handle_context_menu_pointer_move(self, pos: tuple[int, int]) -> None:
        self._context_action_sheet.pointer_move(pos)

    def _handle_context_menu_pointer_up(self, pos: tuple[int, int]) -> None:
        if self._context_menu_ignore_next_up:
            self._context_menu_ignore_next_up = False
            return
        scrolled = self._context_action_sheet.pointer_up(pos)
        if not self._context_action_sheet.contains(pos):
            self._close_context_menu()
            return
        if scrolled:
            return
        action_id = self._context_action_sheet.action_at(pos)
        if action_id:
            self._execute_context_action(action_id)

    def _handle_context_menu_tap(self, pos: tuple[int, int]) -> None:
        self._handle_context_menu_pointer_up(pos)

    def _draw_context_menu(self) -> None:
        self._draw_modal_backdrop(legacy_alpha=140)
        self._context_action_sheet.draw(
            self.screen,
            title=self._context_menu_title,
            font_md=self.font_md,
            font_sm=self.font_sm,
            theme=self.theme,
            draw_elevated_panel=lambda panel, **kw: self._draw_elevated_panel(panel, **kw),
        )

    def _draw_name_prompt(self) -> None:
        self._draw_modal_backdrop(legacy_alpha=150)
        panel = self._name_prompt_panel
        self._draw_elevated_panel(panel, border_radius=16)
        title = self.font_md.render(self._context_menu_title, True, self.theme.text)
        self.screen.blit(title, (panel.x + 16, panel.y + 12))
        pygame.draw.rect(self.screen, self.theme.surface_alt, self._name_prompt_field.pygame_rect, border_radius=8)
        display = self._name_prompt_text or "Folder name"
        color = self.theme.text if self._name_prompt_text else self.theme.muted
        from patch_browser.ui_text import ellipsize_text

        clipped = ellipsize_text(self.font_md, display, max(1, self._name_prompt_field.w - 16))
        surf = self.font_md.render(clipped, True, color)
        self.screen.blit(
            surf,
            (self._name_prompt_field.x + 8, self._name_prompt_field.y + 8),
        )
        kb = self._name_prompt_keyboard
        if kb:
            for rect, label in kb.keys:
                key_hit = f"name:key:{label}"
                pressed = self._pressed(key_hit)
                bg = self.theme.accent if pressed else self.theme.surface_alt
                text_color = self.theme.bg if pressed else self.theme.text
                pygame.draw.rect(self.screen, bg, rect.pygame_rect, border_radius=6)
                key_label = label.upper() if len(label) == 1 else label
                ks = self.font_sm.render(key_label, True, text_color)
                self.screen.blit(
                    ks,
                    (rect.x + (rect.w - ks.get_width()) // 2, rect.y + (rect.h - ks.get_height()) // 2),
                )
            for special, rect in (
                ("backspace", kb.backspace_rect),
                (" ", kb.space_rect),
            ):
                if rect is None:
                    continue
                key_hit = f"name:key:{special}"
                pressed = self._pressed(key_hit)
                bg = self.theme.accent if pressed else self.theme.surface_alt
                text_color = self.theme.bg if pressed else self.theme.text
                pygame.draw.rect(self.screen, bg, rect.pygame_rect, border_radius=6)
                display = "⌫" if special == "backspace" else "space"
                ks = self.font_sm.render(display, True, text_color)
                self.screen.blit(
                    ks,
                    (rect.x + (rect.w - ks.get_width()) // 2, rect.y + (rect.h - ks.get_height()) // 2),
                )
        if self._name_prompt_cancel:
            self._draw_button(
                self._name_prompt_cancel,
                "Cancel",
                pressed=self._pressed("name:cancel"),
            )
        if self._name_prompt_ok:
            self._draw_button(
                self._name_prompt_ok,
                "Save",
                accent=True,
                pressed=self._pressed("name:save"),
            )
