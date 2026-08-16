"""Touch patch browser — patches mixin."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pygame

from patch_browser.all_patches_index import build_flat_patch_list
from patch_browser.dsi_splash import boot_animation_phase
from patch_browser.instrument_filter import (
    filter_patches_by_instrument,
    patches_in_browse_subtree,
)
from patch_browser.patch_scanner import favorites_display_name
from patch_browser.touch_ui_constants import (
    ALL_PATCHES_ROW_HEIGHT,
    ALL_PATCHES_SCROLL_ANIM_S,
    AZ_RAIL_FEEDBACK_S,
    PATCHES_ROW_HEIGHT,
)
from patch_browser.touch_ui_enums import LeftNavMode


class TouchBrowserPatchesMixin:
    """Mixin — expects TouchPatchBrowser host attributes."""

    def _surge_ready_for_patch_load(self) -> bool:
        if not self.loader.osc_enabled:
            return False
        healthy, _ = self.surge_monitor.check_health()
        if not healthy:
            return False
        return bool(self.surge_monitor.osc_port_in_use())

    def _queue_patch_reload(
        self, patch: dict, *, delay_s: float = 2.0, toast: bool = True
    ) -> None:
        path = patch.get("path")
        if (
            path
            and self._pending_last_patch
            and self._pending_last_patch.get("path") == path
            and time.time() < self._pending_load_next
        ):
            return
        self._pending_last_patch = dict(patch)
        self._pending_load_toast = toast
        next_at = time.time() + delay_s
        if next_at > self._pending_load_next:
            self._pending_load_next = next_at

    def _note_surge_patch_load_success(self) -> None:
        self._last_known_surge_pid = self.surge_monitor.surge_pid
        self._surge_was_healthy = True

    def _maybe_requeue_patch_after_surge_change(self) -> None:
        if getattr(self, "_profile_switch_reload_active", False):
            return

        healthy, _ = self.surge_monitor.check_health()
        pid = self.surge_monitor.surge_pid if healthy else None
        prev_pid = self._last_known_surge_pid
        was_healthy = self._surge_was_healthy

        if not self._surge_liveness_initialized:
            self._surge_liveness_initialized = True
            self._surge_was_healthy = healthy
            if healthy and pid is not None:
                self._last_known_surge_pid = pid
            return

        pid_changed = (
            healthy
            and pid is not None
            and prev_pid is not None
            and pid != prev_pid
        )
        recovered = (not was_healthy) and healthy

        self._surge_was_healthy = healthy
        if healthy and pid is not None:
            self._last_known_surge_pid = pid

        if not (pid_changed or recovered):
            return

        patch = self.loaded_patch_info or self._pending_last_patch
        if patch:
            self._queue_patch_reload(patch, delay_s=1.0, toast=False)

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
                    self._sync_pressure_live()
                    self._note_surge_patch_load_success()
                    self._layout()
                else:
                    self._queue_patch_reload(self.detail_patch)
                    self._layout()
        self._refresh_lists(scroll_to_selection=True)

    def _try_load_patch_path(
        self,
        patch_path: str,
        category: str,
        *,
        stable_key: str | None = None,
    ) -> bool:
        if not self._surge_ready_for_patch_load():
            return False
        return bool(self.loader.load_patch(patch_path, stable_key=stable_key))

    def _reload_loaded_patch_after_norm_change(self) -> bool:
        """Re-load the current patch so Surge + normalization baseline stay in sync."""
        loaded = self.loaded_patch_info
        if not loaded or not self.loader.osc_enabled:
            return False
        if not self._try_load_patch_path(
            loaded["path"],
            loaded["category"],
            stable_key=loaded.get("stable_key"),
        ):
            return False
        self._apply_volume(self.volume_level, persist=False)
        self._sync_pressure_live()
        self._note_surge_patch_load_success()
        return True

    def _retry_pending_load(self) -> None:
        self._maybe_requeue_patch_after_surge_change()
        if not self._pending_last_patch or time.time() < self._pending_load_next:
            return
        patch = self._pending_last_patch
        if self._try_load_patch_path(
            patch["path"],
            patch["category"],
            stable_key=patch.get("stable_key"),
        ):
            self.loaded_patch_info = dict(patch)
            self.detail_patch = dict(patch)
            if getattr(self, "_profile_switch_reload_active", False):
                if not getattr(self, "_profile_switch_sent_once", False):
                    self._profile_switch_sent_once = True
                    self._pending_last_patch = dict(patch)
                    self._pending_load_next = time.time() + 2.0
                    self._apply_volume(self.volume_level, persist=False)
                    self._sync_pressure_live()
                    self._note_surge_patch_load_success()
                    return
                self._profile_switch_reload_active = False
                self._profile_switch_sent_once = False
            self._pending_last_patch = None
            self._apply_volume(self.volume_level, persist=False)
            self._sync_pressure_live()
            self._refresh_lists(scroll_to_selection=True)
            self._layout()
            self._note_surge_patch_load_success()
            if getattr(self, "_pending_load_toast", True):
                self._toast("Patch loaded", 1.5)
            self._pending_load_toast = True
        else:
            self._pending_load_next = time.time() + 2.0
    def _rebuild_all_patches_index(self) -> None:
        self.all_patches_flat, self.all_patches_letter_index = build_flat_patch_list(
            self.scanner
        )

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
                self.loaded_inner_segments = self._patch_inner_segments(
                    self.loaded_patch_info
                )
            self._rebuild_all_patches_index()
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
        max_wait = 8.0
        while not self.scanner.scan_complete.is_set():
            elapsed = time.monotonic() - start
            boot_elapsed = time.monotonic() - getattr(
                self, "_boot_splash_started", start
            )
            self._paint_boot_splash_frame(
                animation_phase=boot_animation_phase(boot_elapsed),
            )
            if elapsed >= max_wait:
                break
            if self.scanner.wait_for_scan(timeout=0.15):
                break
            clock.tick(30)

        if self.scanner.scan_complete.is_set():
            self._apply_scan_results()
        else:
            print("Patch scan still running — list will update when complete")
    def _browse_category_name(self) -> str:
        if not self.categories:
            return "(No patches)"
        return self.categories[self.browse_folder_index]

    def _browse_inner_segments(self) -> tuple[str, ...]:
        return tuple(getattr(self, "browse_inner_segments", ()) or ())

    @staticmethod
    def _patch_inner_segments(patch: dict | None) -> tuple[str, ...]:
        if not patch:
            return ()
        return tuple(patch.get("inner_segments") or ())

    def _patch_in_browse_location(self, patch: dict | None) -> bool:
        if not patch:
            return False
        if patch.get("category") != self._browse_category_name():
            return False
        return self._patch_inner_segments(patch) == self._browse_inner_segments()

    def _patch_in_browse_subtree(self, patch: dict | None) -> bool:
        """True when patch lives at or below the current browse folder."""
        if not patch:
            return False
        if patch.get("category") != self._browse_category_name():
            return False
        here = self._browse_inner_segments()
        there = self._patch_inner_segments(patch)
        return there[: len(here)] == here

    def _patch_visible_in_browse_list(self, patch: dict | None) -> bool:
        if not patch:
            return False
        if self._instrument_filter_active():
            return self._patch_in_browse_subtree(patch) and self._patch_passes_instrument_filter(
                patch
            )
        return self._patch_in_browse_location(patch)

    def _rebuild_browse_nav_entries(self) -> list[dict]:
        category = self._browse_category_name()
        inner = self._browse_inner_segments()
        entries: list[dict] = []
        if self._instrument_filter_active():
            patches = filter_patches_by_instrument(
                patches_in_browse_subtree(self.scanner, category, inner),
                self.instrument_filter,
            )
            patches.sort(
                key=lambda patch: (
                    self._patch_inner_segments(patch),
                    (patch.get("name") or "").lower(),
                )
            )
            for patch in patches:
                entries.append({"kind": "patch", "patch": patch, "label": patch["name"]})
            return entries
        for name in self.scanner.get_subfolders(category, inner):
            entries.append({"kind": "folder", "name": name, "label": f"{name}  >"})
        for patch in self.scanner.get_patches_in_folder(category, inner):
            if not self._patch_passes_instrument_filter(patch):
                continue
            entries.append({"kind": "patch", "patch": patch, "label": patch["name"]})
        return entries

    def _patches_in_browse_folder(self) -> list[dict]:
        if not self.categories:
            return []
        return self.scanner.get_patches_in_folder(
            self._browse_category_name(),
            self._browse_inner_segments(),
        )

    def _browse_folder_title(self) -> str:
        name = self._browse_category_name()
        inner = self._browse_inner_segments()
        if inner:
            return f"{name} / {' / '.join(inner)}"
        return name

    def _select_nav_index(self, idx: int) -> None:
        if self.left_nav_mode == LeftNavMode.ALL_PATCHES:
            if idx < len(self._all_patches_display_flat):
                self._select_patch(self._all_patches_display_flat[idx])
            return
        if self.left_nav_mode == LeftNavMode.FOLDERS:
            self._enter_folder(idx)
            return
        if idx < 0 or idx >= len(self._browse_nav_entries):
            return
        entry = self._browse_nav_entries[idx]
        if entry["kind"] == "folder":
            self._enter_subfolder(entry["name"])
        else:
            self._select_patch(entry["patch"])
    def _show_current_folder_button(self) -> bool:
        return (
            self.loaded_patch_info is not None
            and (
                self.browse_folder_index != self.loaded_folder_index
                or self._browse_inner_segments() != self.loaded_inner_segments
            )
        )
    def _patch_list_index(self, patches: list[dict], patch: dict | None) -> int | None:
        if not patch:
            return None
        target_path = patch.get("path")
        target_key = patch.get("stable_key")
        for i, candidate in enumerate(patches):
            if target_path and candidate.get("path") == target_path:
                return i
            if target_key and candidate.get("stable_key") == target_key:
                return i
            if (
                candidate.get("name") == patch.get("name")
                and candidate.get("category") == patch.get("category")
                and self._patch_inner_segments(candidate) == self._patch_inner_segments(patch)
            ):
                return i
        return None

    def _browse_entry_index_for_patch(self, patch: dict | None) -> int | None:
        if not patch:
            return None
        for i, entry in enumerate(self._browse_nav_entries):
            if entry["kind"] != "patch":
                continue
            candidate = entry["patch"]
            if candidate.get("path") and candidate.get("path") == patch.get("path"):
                return i
            if candidate.get("stable_key") and candidate.get("stable_key") == patch.get("stable_key"):
                return i
        return None

    def _refresh_lists(self, *, scroll_to_selection: bool = False) -> None:
        if self.left_nav_collapsed:
            return

        saved_scroll = self.nav_list._scroll_pixels
        saved_velocity = self.nav_list._velocity
        saved_momentum = self.nav_list._momentum_active

        if self.left_nav_mode == LeftNavMode.ALL_PATCHES:
            self.nav_list.row_height = ALL_PATCHES_ROW_HEIGHT
        else:
            self.nav_list.row_height = (
                PATCHES_ROW_HEIGHT if self.left_nav_mode == LeftNavMode.PATCHES else 44
            )

        if self.left_nav_mode == LeftNavMode.ALL_PATCHES:
            display_patches = self._display_all_patches()
            names = [p["name"] for p in display_patches]
            loaded_idx = self._patch_list_index(display_patches, self.loaded_patch_info)
            self.nav_list.set_items(
                names,
                highlight_index=None,
                loaded_marker_index=loaded_idx,
                preserve_scroll=not scroll_to_selection,
            )
            if scroll_to_selection and loaded_idx is not None:
                self.nav_list.scroll_to_index(loaded_idx)
            elif not scroll_to_selection:
                self.nav_list._scroll_pixels = min(saved_scroll, self.nav_list._max_scroll_pixels())
                self.nav_list._sync_scroll_offset()
                if saved_momentum:
                    self.nav_list._velocity = saved_velocity
                    self.nav_list._momentum_active = True
        elif self.left_nav_mode == LeftNavMode.FOLDERS:
            loaded_idx = self.loaded_folder_index if self.loaded_patch_info else None
            folder_labels = [f"{name}  >" for name in self.categories]
            self.nav_list.set_items(
                folder_labels,
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
            self._browse_nav_entries = self._rebuild_browse_nav_entries()
            labels = [entry["label"] for entry in self._browse_nav_entries]
            highlight = None
            loaded_idx = None
            if self.detail_patch and self._patch_visible_in_browse_list(self.detail_patch):
                highlight = self._browse_entry_index_for_patch(self.detail_patch)
            if self.loaded_patch_info and self._patch_visible_in_browse_list(
                self.loaded_patch_info
            ):
                loaded_idx = self._browse_entry_index_for_patch(self.loaded_patch_info)
            self.nav_list.set_items(labels, highlight_index=highlight, loaded_marker_index=loaded_idx)
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
        self._refresh_instrument_chips()
    def _load_patch(self, patch: dict) -> None:
        if not self.loader.osc_enabled:
            self._toast("OSC unavailable", 3)
            return
        if self.loader.load_patch(
            patch["path"],
            stable_key=patch.get("stable_key"),
        ):
            self.loaded_patch_info = {
                "name": patch["name"],
                "category": patch["category"],
                "path": patch["path"],
                "stable_key": patch.get("stable_key"),
                "inner_segments": list(self._patch_inner_segments(patch)),
            }
            self.detail_patch = dict(self.loaded_patch_info)
            self._pending_last_patch = None
            self.scanner.save_last_patch(patch["category"], patch["path"])
            self._note_surge_patch_load_success()
            try:
                self.loaded_folder_index = self.categories.index(patch["category"])
            except ValueError:
                pass
            self.loaded_inner_segments = self._patch_inner_segments(patch)
            self._apply_volume(self.volume_level, persist=False)
            self._sync_pressure_live()
            self._layout()
        else:
            self._toast("Load failed", 3)
    def _enter_folder(self, index: int) -> None:
        if not self.categories:
            return
        self._enter_nav_mode(
            LeftNavMode.PATCHES,
            browse_folder_index=index,
            browse_inner_segments=(),
            reset_list_scroll=True,
        )

    def _enter_subfolder(self, segment: str) -> None:
        inner = self._browse_inner_segments() + (segment,)
        self._enter_nav_mode(
            LeftNavMode.PATCHES,
            browse_inner_segments=inner,
            reset_list_scroll=True,
        )

    def _enter_all_patches(self) -> None:
        self._enter_nav_mode(
            LeftNavMode.ALL_PATCHES,
            left_nav_collapsed=False,
            rebuild_all_patches=True,
            restore_all_scroll=True,
        )

    def _go_back_from_all_patches(self) -> None:
        self._enter_nav_mode(
            LeftNavMode.FOLDERS,
            snapshot_all_scroll=True,
            scroll_to_selection=True,
        )

    def _go_up_to_folders(self) -> None:
        if self.left_nav_mode == LeftNavMode.ALL_PATCHES:
            self._go_back_from_all_patches()
            return
        inner = self._browse_inner_segments()
        if inner:
            self._enter_nav_mode(
                LeftNavMode.PATCHES,
                browse_inner_segments=inner[:-1],
                reset_list_scroll=True,
                scroll_to_selection=True,
            )
            return
        self._enter_nav_mode(
            LeftNavMode.FOLDERS,
            browse_inner_segments=(),
            scroll_to_selection=True,
        )

    def _jump_all_patches_to_letter(self, letter: str) -> None:
        letter_index = self._all_patches_display_letter_index
        index = letter_index.get(letter)
        if index is None:
            for bucket in sorted(letter_index):
                if bucket >= letter:
                    index = letter_index[bucket]
                    break
        if index is None and letter_index:
            index = max(letter_index.values())
        if index is not None:
            self.nav_list.animate_scroll_to_index(
                index,
                align="top",
                duration=ALL_PATCHES_SCROLL_ANIM_S,
            )
            self._az_rail_active_letter = letter
            self._az_rail_active_until = time.time() + AZ_RAIL_FEEDBACK_S

    def _handle_az_rail_touch(self, kind: str, pos: tuple[int, int]) -> bool:
        if self.left_nav_mode != LeftNavMode.ALL_PATCHES:
            return False
        if kind == "down":
            letter = self._az_rail_letter_at(pos)
            if letter is None:
                return False
            self._az_rail_capture = True
            self._az_rail_scrub_letter = letter
            self._jump_all_patches_to_letter(letter)
            return True
        if kind == "motion" and self._az_rail_capture:
            letter = self._az_rail_letter_at(pos)
            if letter is not None and letter != self._az_rail_scrub_letter:
                self._az_rail_scrub_letter = letter
                self._jump_all_patches_to_letter(letter)
            return True
        if kind == "up" and self._az_rail_capture:
            self._az_rail_capture = False
            self._az_rail_scrub_letter = None
            return True
        return False
    def _go_to_loaded_folder(self) -> None:
        if not self.loaded_patch_info:
            return
        from_all = self.left_nav_mode == LeftNavMode.ALL_PATCHES
        try:
            idx = self.categories.index(self.loaded_patch_info["category"])
        except ValueError:
            return
        self._enter_nav_mode(
            LeftNavMode.PATCHES,
            browse_folder_index=idx,
            browse_inner_segments=self._patch_inner_segments(self.loaded_patch_info),
            snapshot_all_scroll=from_all,
            scroll_to_selection=not from_all,
            relayout=from_all,
        )
        if from_all:
            self._refresh_lists(scroll_to_selection=True)
    def _toggle_nav_collapsed(self) -> None:
        self.left_nav_collapsed = not self.left_nav_collapsed
        self._relayout()
    def _select_patch(self, patch: dict) -> None:
        from_all = self.left_nav_mode == LeftNavMode.ALL_PATCHES
        folder_idx = None
        inner: tuple[str, ...] = ()
        if patch.get("category") in self.categories:
            folder_idx = self.categories.index(patch["category"])
            inner = self._patch_inner_segments(patch)
        self._enter_nav_mode(
            LeftNavMode.PATCHES,
            browse_folder_index=folder_idx,
            browse_inner_segments=inner,
            left_nav_collapsed=False if from_all else None,
            snapshot_all_scroll=from_all,
            relayout=from_all,
        )
        self._load_patch(patch)
        self._refresh_lists(scroll_to_selection=True)
    def _patch_is_favorited(self, patch: dict) -> bool:
        return self.scanner.is_patch_in_favorites(patch)
    def _sync_categories_after_favorites_change(self) -> None:
        with self._scan_lock:
            self.scanner.rescan_favorites_category()
            self.categories = self.scanner.get_categories()
            self._rebuild_all_patches_index()
        self._refresh_lists()

    def _pop_browse_after_folder_delete(self, folder_key: str) -> None:
        inner = self._browse_inner_segments()
        if not inner:
            return
        prefix = tuple(part for part in folder_key.split("/") if part)
        if not prefix or inner[: len(prefix)] != prefix:
            return
        self.browse_inner_segments = inner[: max(0, len(prefix) - 1)]
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

        if self.scanner.add_patch_to_favorites(patch):
            self._sync_categories_after_favorites_change()
            self._toast("Added to Quick Select", 2.0)
        else:
            if self._patch_is_favorited(patch):
                self._toast(f"Already in {quick_label}", 2.0)
            else:
                self._toast(f"Could not add to {quick_label}", 2.5)
