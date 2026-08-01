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
    # Tiered OLED surfaces — None keeps standard-theme legacy draw behavior.
    surface_elevated: tuple[int, int, int] | None = None
    surface_content: tuple[int, int, int] | None = None
    backdrop_alpha: int | None = None
    hairline_alpha: int = 0
    elevated_top_highlight: bool = False

    def panel_surface(self) -> tuple[int, int, int]:
        """2dp — settings slide-out, modals."""
        return self.surface_elevated if self.surface_elevated is not None else self.surface

    def content_surface(self) -> tuple[int, int, int]:
        """Main detail canvas — true black on OLED, raised card on standard."""
        return self.surface_content if self.surface_content is not None else self.surface


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
    surface=(6, 6, 8),  # #060608 — 1dp header / nav
    surface_alt=(14, 14, 18),  # #0E0E12 — row hover / selected
    text=(235, 235, 240),
    muted=(150, 150, 158),
    accent=(100, 110, 203),  # #646ecb
    playing=(210, 155, 75),
    danger=(210, 85, 85),
    ok=(80, 185, 125),
    surface_elevated=(10, 10, 14),  # #0A0A0E — 2dp panels / modals
    surface_content=(0, 0, 0),  # true-black main content (OLED power)
    backdrop_alpha=128,  # ~50% dim behind overlays
    hairline_alpha=24,  # ~9% white hairline (bumped for darker surfaces)
    elevated_top_highlight=True,
)


def theme_oled_black() -> Theme:
    return OLED_BLACK_THEME


def theme_for_mode(mode: str) -> Theme:
    if mode == THEME_MODE_OLED_BLACK:
        return theme_oled_black()
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
