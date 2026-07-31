"""Touch patch browser — prefs mixin."""

from __future__ import annotations

from patch_browser.touch_ui_constants import VOLUME_MAX, VOLUME_MIN
from patch_browser.ui_prefs import (
    load_ui_preference,
    load_volume_level,
    read_ui_prefs_file,
    save_theme_mode,
    save_ui_preference,
    save_volume_level,
    write_ui_prefs_file,
)
from patch_browser.ui_theme import THEME_MODE_OLED_BLACK, THEME_MODE_STANDARD, theme_for_mode


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

    def _save_theme_mode(self, mode: str) -> None:
        save_theme_mode(mode)

    def _apply_theme_mode(self, mode: str) -> None:
        self.theme_mode = mode
        self.theme = theme_for_mode(mode)
        self._save_theme_mode(mode)

    def _toggle_oled_black_theme(self) -> None:
        mode = (
            THEME_MODE_STANDARD
            if self.theme_mode == THEME_MODE_OLED_BLACK
            else THEME_MODE_OLED_BLACK
        )
        self._apply_theme_mode(mode)
        if mode == THEME_MODE_OLED_BLACK:
            self._toast("OLED black on", 1.5)
        else:
            self._toast("Standard theme", 1.5)

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
