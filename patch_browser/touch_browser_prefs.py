"""Touch patch browser — prefs mixin."""

from __future__ import annotations

from patch_browser.touch_ui_constants import VOLUME_MAX, VOLUME_MIN
from patch_browser.touch_ui_enums import Screen
from patch_browser.ui_prefs import (
    load_ui_preference,
    load_volume_level,
    read_ui_prefs_file,
    save_theme_preferences,
    save_ui_preference,
    save_volume_level,
    write_ui_prefs_file,
)
from patch_browser.ui_theme import (
    ThemePreferences,
    apply_theme_preferences,
    load_theme_preferences,
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

    def _open_theme_modal(self) -> None:
        saved = self._load_theme_preferences()
        self._theme_saved_prefs = saved
        self._theme_draft_prefs = saved
        self.screen_state = Screen.THEME

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
        self._apply_theme_draft(
            ThemePreferences(
                theme_mode=draft.theme_mode,
                accent_rgb=accent_rgb,
                accent_style=draft.accent_style,
            )
        )

    def _commit_theme_modal(self) -> None:
        draft = self._theme_draft()
        self._apply_theme_preferences(draft, persist=True)
        self._theme_saved_prefs = None
        self._theme_draft_prefs = None
        self.screen_state = Screen.SETTINGS
        self._toast("Theme saved", 1.5)

    def _cancel_theme_modal(self) -> None:
        saved = getattr(self, "_theme_saved_prefs", None)
        if saved is not None:
            self._apply_theme_preferences(saved, persist=False)
        self._theme_saved_prefs = None
        self._theme_draft_prefs = None
        self.screen_state = Screen.SETTINGS

    def _toggle_cpu_meter_visibility(self) -> None:
        self.show_cpu_meter = not self.show_cpu_meter
        self._save_ui_preference("show_cpu_meter", self.show_cpu_meter)
        self._layout()
        if self.show_cpu_meter:
            self._toast("CPU meter on", 1.5)
        else:
            self._toast("CPU meter off", 1.5)

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
