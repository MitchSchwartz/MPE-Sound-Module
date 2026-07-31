"""Shared UI colors for pygame surfaces (touch browser, calibration loader)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

UI_STATE_FILE = Path.home() / ".patch_browser_ui.json"

THEME_MODE_STANDARD = "standard"
THEME_MODE_OLED_BLACK = "oled_black"
THEME_MODES = (THEME_MODE_STANDARD, THEME_MODE_OLED_BLACK)


@dataclass(frozen=True)
class Theme:
    bg: tuple[int, int, int]
    surface: tuple[int, int, int]
    surface_alt: tuple[int, int, int]
    text: tuple[int, int, int]
    muted: tuple[int, int, int]
    accent: tuple[int, int, int]
    playing: tuple[int, int, int]
    danger: tuple[int, int, int]
    ok: tuple[int, int, int]


STANDARD_THEME = Theme(
    bg=(10, 10, 12),
    surface=(22, 22, 28),
    surface_alt=(32, 32, 40),
    text=(232, 232, 236),
    muted=(130, 130, 140),
    accent=(107, 159, 255),
    playing=(255, 180, 90),
    danger=(220, 90, 90),
    ok=(90, 200, 140),
)

OLED_BLACK_THEME = Theme(
    bg=(0, 0, 0),
    surface=(0, 0, 0),
    surface_alt=(18, 18, 22),
    text=(235, 235, 240),
    muted=(150, 150, 158),
    accent=(90, 130, 210),
    playing=(210, 155, 75),
    danger=(210, 85, 85),
    ok=(80, 185, 125),
)


def theme_for_mode(mode: str) -> Theme:
    if mode == THEME_MODE_OLED_BLACK:
        return OLED_BLACK_THEME
    return STANDARD_THEME


def load_theme_mode_from_prefs(*, default: str = THEME_MODE_STANDARD) -> str:
    if UI_STATE_FILE.exists():
        try:
            data = json.loads(UI_STATE_FILE.read_text())
            mode = data.get("theme_mode", default)
            if mode in THEME_MODES:
                return str(mode)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
    return default
