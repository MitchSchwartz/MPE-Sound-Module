"""Touch patch browser — patches mixin."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from patch_browser.dsi_splash import BOOT_MIN_SECONDS
from patch_browser.patch_scanner import favorites_display_name
from patch_browser.touch_ui_enums import LeftNavMode


class TouchBrowserPatchesMixin:
    """Mixin — expects TouchPatchBrowser host attributes."""

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

        start = time.monotonic()
        clock = pygame.time.Clock()
        while not self.scanner.scan_complete.is_set():
            elapsed = time.monotonic() - start
            boot_elapsed = time.monotonic() - getattr(
                self, "_boot_splash_started", start
            )
            progress = min(0.92, max(0.08, boot_elapsed / BOOT_MIN_SECONDS * 0.85))
            if elapsed > 5.0:
                progress = min(0.95, progress + (elapsed - 5.0) * 0.01)
            self._paint_boot_splash_frame(progress=progress)
            if self.scanner.wait_for_scan(timeout=0.15):
                break
            clock.tick(30)

        self._apply_scan_results()
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
    def _patch_is_favorited(self, patch: dict) -> bool:
        return self.scanner.is_patch_in_favorites(patch)
    def _sync_categories_after_favorites_change(self) -> None:
        with self._scan_lock:
            self.categories = self.scanner.get_categories()
        self._refresh_lists()
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
