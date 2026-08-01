"""Persisted volume and UI preference helpers."""

from __future__ import annotations

import json

from patch_browser.touch_ui_constants import UI_STATE_FILE, VOLUME_MAX, VOLUME_MIN, VOLUME_STATE_FILE
from patch_browser.ui_theme import SavedAccentColor, serialize_custom_accent_colors


def load_volume_level() -> float:
    if VOLUME_STATE_FILE.exists():
        try:
            data = json.loads(VOLUME_STATE_FILE.read_text())
            level = float(data.get("volume", 1.0))
            return max(VOLUME_MIN, min(VOLUME_MAX, level))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
    return 1.0


def save_volume_level(level: float) -> None:
    try:
        VOLUME_STATE_FILE.write_text(json.dumps({"volume": level}, indent=2))
    except OSError as exc:
        print(f"Warning: could not persist volume ({exc})")


def read_ui_prefs_file() -> dict:
    if UI_STATE_FILE.exists():
        try:
            loaded = json.loads(UI_STATE_FILE.read_text())
            if isinstance(loaded, dict):
                return dict(loaded)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
    return {}


def write_ui_prefs_file(data: dict) -> None:
    try:
        UI_STATE_FILE.write_text(json.dumps(data, indent=2))
    except OSError as exc:
        print(f"Warning: could not persist UI preferences ({exc})")


def load_ui_preference(key: str, *, default: bool = True) -> bool:
    if UI_STATE_FILE.exists():
        try:
            data = json.loads(UI_STATE_FILE.read_text())
            if key in data:
                return bool(data[key])
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
    return default


def save_ui_preference(key: str, value: bool) -> None:
    data = read_ui_prefs_file()
    data[key] = value
    write_ui_prefs_file(data)


def save_theme_mode(mode: str) -> None:
    data = read_ui_prefs_file()
    data["theme_mode"] = mode
    write_ui_prefs_file(data)


def save_theme_preferences(
    *,
    theme_mode: str,
    accent_rgb: tuple[int, int, int],
    accent_style: str,
) -> None:
    data = read_ui_prefs_file()
    data["theme_mode"] = theme_mode
    data["accent_rgb"] = list(accent_rgb)
    data["accent_style"] = accent_style
    write_ui_prefs_file(data)


def save_custom_accent_colors(colors: list[SavedAccentColor]) -> None:
    data = read_ui_prefs_file()
    data["custom_accent_colors"] = serialize_custom_accent_colors(colors)
    write_ui_prefs_file(data)
