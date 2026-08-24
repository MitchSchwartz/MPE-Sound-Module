"""Touch patch browser — prefs mixin."""

from __future__ import annotations

import queue
import threading
import time

from patch_browser.touch_ui_constants import VOLUME_MAX, VOLUME_MIN
from patch_browser.touch_ui_enums import Screen
from patch_browser.ui_prefs import (
    load_ui_preference,
    load_volume_level,
    read_ui_prefs_file,
    save_custom_accent_colors,
    save_theme_preferences,
    save_ui_preference,
    save_volume_level,
    write_ui_prefs_file,
)
from patch_browser.ui_theme import (
    THEME_VIEW_COLORS,
    THEME_VIEW_MAIN,
    THEME_VIEW_PICKER,
    MAX_CUSTOM_ACCENT_COLORS,
    SavedAccentColor,
    ThemePreferences,
    apply_theme_preferences,
    clamp_rgb,
    default_custom_color_name,
    find_custom_accent_by_rgb,
    find_custom_accent_color,
    load_custom_accent_colors,
    load_theme_preferences,
    new_custom_color_id,
    theme_for_mode,
)


class TouchBrowserPrefsMixin:
    """Volume, brightness, theme, and UI preference helpers."""

    def _load_volume_level(self) -> float:
        return load_volume_level()

    def _save_volume_level(self) -> None:
        save_volume_level(self.volume_level)

    def _load_ui_preference(self, key: str, *, default: bool = True) -> bool:
        return load_ui_preference(key, default=default)

    def _save_ui_preference(self, key: str, value: bool) -> None:
        save_ui_preference(key, value)

    def _read_ui_prefs_file(self) -> dict:
        return read_ui_prefs_file()

    def _write_ui_prefs_file(self, data: dict) -> None:
        write_ui_prefs_file(data)

    def _load_theme_preferences(self) -> ThemePreferences:
        return load_theme_preferences()

    def _apply_theme_preferences(self, prefs: ThemePreferences, *, persist: bool = True) -> None:
        apply_theme_preferences(prefs)
        self.theme_mode = prefs.theme_mode
        self.theme = theme_for_mode(prefs.theme_mode)
        if persist:
            save_theme_preferences(
                theme_mode=prefs.theme_mode,
                accent_rgb=prefs.accent_rgb,
                accent_style=prefs.accent_style,
            )

    def _theme_draft(self) -> ThemePreferences:
        draft = getattr(self, "_theme_draft_prefs", None)
        if draft is not None:
            return draft
        return self._load_theme_preferences()

    def _theme_view(self) -> str:
        return getattr(self, "_theme_view_state", THEME_VIEW_MAIN)

    def _reload_custom_accent_colors(self) -> list[SavedAccentColor]:
        colors = load_custom_accent_colors()
        self._custom_accent_colors = colors
        return colors

    def _persist_custom_accent_colors(self) -> None:
        colors = getattr(self, "_custom_accent_colors", [])
        save_custom_accent_colors(colors)

    def _open_theme_modal(self) -> None:
        saved = self._load_theme_preferences()
        self._theme_saved_prefs = saved
        self._theme_draft_prefs = saved
        self._theme_view_state = THEME_VIEW_MAIN
        self._reload_custom_accent_colors()
        self._picker_rgb = saved.accent_rgb
        self._picker_editing_id = None
        self._picker_slider_channel = None
        self.screen_state = Screen.THEME

    def _open_theme_color_palette(self) -> None:
        self._theme_view_state = THEME_VIEW_COLORS
        self._reload_custom_accent_colors()
        self._theme_colors_scroll.reset()

    def _open_theme_color_picker(self, *, editing_id: str | None = None) -> None:
        draft = self._theme_draft()
        if editing_id is not None:
            existing = find_custom_accent_color(self._custom_accent_colors, editing_id)
            if existing is not None:
                self._picker_rgb = existing.rgb
                self._picker_editing_id = existing.color_id
            else:
                self._picker_rgb = draft.accent_rgb
                self._picker_editing_id = None
        else:
            self._picker_rgb = draft.accent_rgb
            self._picker_editing_id = None
        self._picker_slider_channel = None
        self._theme_view_state = THEME_VIEW_PICKER

    def _close_theme_color_picker(self) -> None:
        self._picker_slider_channel = None
        self._picker_editing_id = None
        self._theme_view_state = THEME_VIEW_COLORS

    def _close_theme_color_palette(self) -> None:
        self._theme_view_state = THEME_VIEW_MAIN

    def _apply_theme_draft(self, prefs: ThemePreferences) -> None:
        self._theme_draft_prefs = prefs
        self._apply_theme_preferences(prefs, persist=False)

    def _set_theme_base_mode(self, mode: str) -> None:
        draft = self._theme_draft()
        self._apply_theme_draft(
            ThemePreferences(
                theme_mode=mode,
                accent_rgb=draft.accent_rgb,
                accent_style=draft.accent_style,
            )
        )

    def _set_theme_accent_style(self, accent_style: str) -> None:
        draft = self._theme_draft()
        self._apply_theme_draft(
            ThemePreferences(
                theme_mode=draft.theme_mode,
                accent_rgb=draft.accent_rgb,
                accent_style=accent_style,
            )
        )

    def _set_theme_accent_rgb(self, accent_rgb: tuple[int, int, int]) -> None:
        draft = self._theme_draft()
        rgb = clamp_rgb(accent_rgb)
        self._picker_rgb = rgb
        self._apply_theme_draft(
            ThemePreferences(
                theme_mode=draft.theme_mode,
                accent_rgb=rgb,
                accent_style=draft.accent_style,
            )
        )

    def _set_picker_rgb(self, accent_rgb: tuple[int, int, int]) -> None:
        rgb = clamp_rgb(accent_rgb)
        self._picker_rgb = rgb
        self._set_theme_accent_rgb(rgb)

    def _save_picker_custom_color(self) -> None:
        rgb = clamp_rgb(getattr(self, "_picker_rgb", self._theme_draft().accent_rgb))
        colors = list(getattr(self, "_custom_accent_colors", []))
        editing_id = getattr(self, "_picker_editing_id", None)

        if editing_id is not None:
            updated: list[SavedAccentColor] = []
            found = False
            for color in colors:
                if color.color_id == editing_id:
                    updated.append(
                        SavedAccentColor(
                            color_id=color.color_id,
                            name=default_custom_color_name(rgb),
                            rgb=rgb,
                        )
                    )
                    found = True
                else:
                    updated.append(color)
            if found:
                colors = updated
            else:
                editing_id = None

        if editing_id is None:
            existing = find_custom_accent_by_rgb(colors, rgb)
            if existing is not None:
                self._picker_editing_id = existing.color_id
                self._toast("Color updated", 1.2)
            elif len(colors) >= MAX_CUSTOM_ACCENT_COLORS:
                self._toast("Palette full — delete one first", 2.0)
                return
            else:
                saved = SavedAccentColor(
                    color_id=new_custom_color_id(),
                    name=default_custom_color_name(rgb),
                    rgb=rgb,
                )
                colors.append(saved)
                self._picker_editing_id = saved.color_id
                self._toast("Color saved", 1.2)

        self._custom_accent_colors = colors
        self._persist_custom_accent_colors()
        self._set_theme_accent_rgb(rgb)

    def _delete_custom_accent_color(self, color_id: str) -> None:
        colors = [color for color in getattr(self, "_custom_accent_colors", []) if color.color_id != color_id]
        if len(colors) == len(getattr(self, "_custom_accent_colors", [])):
            return
        self._custom_accent_colors = colors
        self._persist_custom_accent_colors()
        if getattr(self, "_picker_editing_id", None) == color_id:
            self._picker_editing_id = None
        self._toast("Color deleted", 1.2)

    def _delete_picker_custom_color(self) -> None:
        editing_id = getattr(self, "_picker_editing_id", None)
        if editing_id is None:
            return
        self._delete_custom_accent_color(editing_id)
        self._close_theme_color_picker()

    def _commit_theme_modal(self) -> None:
        draft = self._theme_draft()
        self._apply_theme_preferences(draft, persist=True)
        self._theme_saved_prefs = None
        self._theme_draft_prefs = None
        self._theme_view_state = THEME_VIEW_MAIN
        self._picker_slider_channel = None
        self.screen_state = Screen.SETTINGS
        self._toast("Theme saved", 1.5)

    def _cancel_theme_modal(self) -> None:
        saved = getattr(self, "_theme_saved_prefs", None)
        if saved is not None:
            self._apply_theme_preferences(saved, persist=False)
        self._theme_saved_prefs = None
        self._theme_draft_prefs = None
        self._theme_view_state = THEME_VIEW_MAIN
        self._picker_slider_channel = None
        self.screen_state = Screen.SETTINGS

    def _toggle_cpu_meter_visibility(self) -> None:
        self.show_cpu_meter = not self.show_cpu_meter
        self._save_ui_preference("show_cpu_meter", self.show_cpu_meter)
        self._layout()
        if self.show_cpu_meter:
            self._toast("CPU meter on", 1.2)
        else:
            self._toast("CPU meter off", 1.2)

    def _toggle_peak_meter_visibility(self) -> None:
        self.show_peak_meter = not self.show_peak_meter
        self._save_ui_preference("show_peak_meter", self.show_peak_meter)
        self._layout()
        if self.show_peak_meter:
            self._toast("Output meter on", 1.2)
        else:
            self._toast("Output meter off", 1.2)

    def _toggle_looper_hud_visibility(self) -> None:
        self.show_looper_hud = not self.show_looper_hud
        self._save_ui_preference("show_looper_hud", self.show_looper_hud)
        self._layout()
        if self.show_looper_hud:
            self._toast("Looper tempo on", 1.2)
        else:
            self._toast("Looper tempo off", 1.2)

    def _toggle_poly_governor(self) -> None:
        self.poly_governor_enabled = not self.poly_governor_enabled
        self._save_ui_preference("poly_governor_enabled", self.poly_governor_enabled)
        self._layout()
        if self.poly_governor_enabled:
            self._toast("Dynamic voice limit on", 1.8)
        else:
            self._toast("Dynamic voice limit off", 1.8)

    def _refresh_audio_switch_progress(self) -> None:
        """Update overlay hint + toast while buffer/rate/profile switches run."""
        monitor = getattr(self, "engine_monitor", None)
        if monitor is None:
            return
        from patch_browser.audio_engine import (
            audio_switch_progress_message,
            read_jack_state,
            read_reconcile_state,
        )

        hint, toast, toast_sec = audio_switch_progress_message(
            monitor.snapshot(),
            read_reconcile_state(),
            jack=read_jack_state(),
        )
        if hint:
            self._audio_switch_progress_hint = hint
            if getattr(self, "_surge_audio_switching", False):
                self._surge_audio_switch_hint = hint
        if toast:
            now = time.monotonic()
            if now - getattr(self, "_last_audio_switch_toast_at", 0.0) >= 2.0:
                self._toast(toast, toast_sec)
                self._last_audio_switch_toast_at = now

    def _poll_engine_recovery_toast(self) -> None:
        """Surface unplanned recovery / cooldown while the user is not in a settings overlay."""
        if getattr(self, "_surge_audio_switching", False) or getattr(
            self, "_audio_profile_switching", False
        ):
            return

        from patch_browser.midi_connect_progress import connecting_toast

        midi_toast = connecting_toast()
        if midi_toast:
            now = time.monotonic()
            last_midi = getattr(self, "_last_midi_connect_toast_at", 0.0)
            first_midi = not getattr(self, "_midi_connect_toast_active", False)
            if first_midi or now - last_midi >= 2.0:
                self._toast(midi_toast, 3.0)
                self._last_midi_connect_toast_at = now
                self._midi_connect_toast_active = True
            return

        self._midi_connect_toast_active = False

        monitor = getattr(self, "engine_monitor", None)
        if monitor is None:
            return
        snap = monitor.snapshot()
        state = snap.get("state") or ""
        prev = getattr(self, "_last_engine_recovery_state", "ok")
        self._last_engine_recovery_state = state

        if state not in {"recovering", "failed"}:
            return
        from patch_browser.audio_engine import audio_switch_progress_message, read_jack_state, read_reconcile_state

        _, toast, toast_sec = audio_switch_progress_message(
            snap,
            read_reconcile_state(),
            jack=read_jack_state(),
        )
        if not toast:
            return
        now = time.monotonic()
        entered_recovery = prev not in {"recovering", "failed"}
        last_toast = getattr(self, "_last_audio_switch_toast_at", 0.0)
        if entered_recovery or now - last_toast >= 2.0:
            self._toast(toast, toast_sec)
            self._last_audio_switch_toast_at = now

    def _finish_audio_profile_switch(self, ok: bool, message: str) -> None:
        self._audio_profile_switching = False
        self._audio_profile_switch_target = None
        self._audio_switch_progress_hint = ""
        if ok:
            self._toast(message, 3.0)
            patch = self.loaded_patch_info or self._pending_last_patch
            if patch:
                self._last_known_surge_pid = None
                self._surge_was_healthy = False
                self._surge_liveness_initialized = False
                self.surge_monitor.last_check_time = 0.0
                self.surge_monitor._find_surge_process()
                self._queue_patch_reload(patch, delay_s=4.0)
            self._layout_settings_content()
            self._layout()
        else:
            self._profile_switch_reload_active = False
            self._profile_switch_sent_once = False
            self._toast(f"Audio profile: {message}", 4.0)

    def _poll_audio_profile_switch(self) -> None:
        if not self._audio_profile_switching:
            return
        try:
            ok, message = self._audio_profile_result_queue.get_nowait()
        except queue.Empty:
            from patch_browser.audio_profile import PROFILE_SWITCH_TIMEOUT_S

            self._refresh_audio_switch_progress()
            elapsed = time.monotonic() - self._audio_profile_switch_started
            if elapsed > PROFILE_SWITCH_TIMEOUT_S + 5.0:
                self._finish_audio_profile_switch(
                    False,
                    f"Switch timed out ({int(PROFILE_SWITCH_TIMEOUT_S)}s)",
                )
            return
        self._finish_audio_profile_switch(ok, message)

    def _begin_audio_profile_switch(self, profile: str) -> None:
        if getattr(self, "_audio_profile_switching", False) or getattr(
            self, "_surge_audio_switching", False
        ):
            return
        from patch_browser.audio_profile import apply_profile, current_profile, normalize_profile, profile_option_label

        profile = normalize_profile(profile)
        if profile == current_profile():
            return

        patch = self.loaded_patch_info
        if patch:
            self.scanner.save_last_patch(patch["category"], patch["path"])
            self._pending_last_patch = dict(patch)
        self._profile_switch_reload_active = True
        self._profile_switch_sent_once = False

        self._audio_profile_switching = True
        self._audio_profile_switch_target = profile
        self._audio_profile_switch_started = time.monotonic()
        self._audio_switch_progress_hint = ""
        self._last_audio_switch_toast_at = 0.0
        label = profile_option_label(profile)
        self._toast(f"Switching to {label}…", 2.0)

        def _worker() -> None:
            ok, message = apply_profile(profile)
            self._audio_profile_result_queue.put((ok, message))

        threading.Thread(
            target=_worker,
            daemon=True,
            name="AudioProfileSwitch",
        ).start()

    def _finish_surge_audio_switch(self, ok: bool, message: str) -> None:
        self._surge_audio_switching = False
        self._surge_audio_switch_hint = ""
        self._audio_switch_progress_hint = ""
        if ok:
            self._toast(message, 3.0)
            patch = self.loaded_patch_info or self._pending_last_patch
            if patch:
                self._last_known_surge_pid = None
                self._surge_was_healthy = False
                self._surge_liveness_initialized = False
                self.surge_monitor.last_check_time = 0.0
                self.surge_monitor._find_surge_process()
                self._queue_patch_reload(patch, delay_s=4.0)
            self._layout_settings_content()
            self._layout()
        else:
            self._toast(f"Audio settings: {message}", 4.0)

    def _poll_surge_audio_switch(self) -> None:
        if not self._surge_audio_switching:
            return
        try:
            ok, message = self._surge_audio_result_queue.get_nowait()
        except queue.Empty:
            from patch_browser.surge_audio import AUDIO_SWITCH_TIMEOUT_S

            self._refresh_audio_switch_progress()
            elapsed = time.monotonic() - self._surge_audio_switch_started
            if elapsed > AUDIO_SWITCH_TIMEOUT_S + 5.0:
                self._finish_surge_audio_switch(
                    False,
                    f"Switch timed out ({int(AUDIO_SWITCH_TIMEOUT_S)}s)",
                )
            return
        self._finish_surge_audio_switch(ok, message)

    def _begin_surge_audio_switch(self, hint: str, worker) -> None:
        if getattr(self, "_audio_profile_switching", False) or getattr(
            self, "_surge_audio_switching", False
        ):
            return
        patch = self.loaded_patch_info
        if patch:
            self.scanner.save_last_patch(patch["category"], patch["path"])
            self._pending_last_patch = dict(patch)

        self._surge_audio_switching = True
        self._surge_audio_switch_hint = hint
        self._surge_audio_switch_started = time.monotonic()
        self._audio_switch_progress_hint = hint
        self._last_audio_switch_toast_at = 0.0
        self._toast(hint, 2.0)

        def _worker_wrapper() -> None:
            ok, message = worker()
            self._surge_audio_result_queue.put((ok, message))

        threading.Thread(
            target=_worker_wrapper,
            daemon=True,
            name="SurgeAudioSwitch",
        ).start()

    def _finish_midi_sync_switch(self, ok: bool, message: str) -> None:
        self._midi_sync_switching = False
        if ok:
            self._toast(message, 3.0)
            self._layout_settings_content()
            self._layout()
        else:
            self._toast(f"Looper sync: {message}", 4.0)

    def _poll_midi_sync_switch(self) -> None:
        if not self._midi_sync_switching:
            return
        try:
            ok, message = self._midi_sync_result_queue.get_nowait()
        except queue.Empty:
            from patch_browser.midi_sync_settings import APPLY_TIMEOUT_S

            elapsed = time.monotonic() - self._midi_sync_switch_started
            if elapsed > APPLY_TIMEOUT_S + 5.0:
                self._finish_midi_sync_switch(
                    False,
                    f"Switch timed out ({int(APPLY_TIMEOUT_S)}s)",
                )
            return
        self._finish_midi_sync_switch(ok, message)

    def _begin_midi_sync_switch(self, hint: str, worker) -> None:
        if getattr(self, "_midi_sync_switching", False):
            return
        self._midi_sync_switching = True
        self._midi_sync_switch_started = time.monotonic()
        self._toast(hint, 1.5)

        def _worker_wrapper() -> None:
            ok, message = worker()
            self._midi_sync_result_queue.put((ok, message))

        threading.Thread(
            target=_worker_wrapper,
            daemon=True,
            name="MidiSyncSwitch",
        ).start()

    def _apply_volume(self, level: float, persist: bool = True) -> None:
        self.volume_level = max(VOLUME_MIN, min(VOLUME_MAX, level))
        if self.loader.osc_enabled:
            self.loader.set_volume(self.volume_level)
        if persist:
            self._save_volume_level()

    def _apply_brightness(self, percent: int) -> None:
        self.brightness_percent = percent
        if not self.backlight.set_percent(percent):
            self._toast("Brightness control unavailable", 2.5)
