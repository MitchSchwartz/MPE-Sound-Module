"""Shared UI colors for pygame surfaces (touch browser, calibration loader)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

UI_STATE_FILE = Path.home() / ".patch_browser_ui.json"

THEME_MODE_STANDARD = "standard"
THEME_MODE_OLED_BLACK = "oled_black"
THEME_MODES = (THEME_MODE_STANDARD, THEME_MODE_OLED_BLACK)

ACCENT_STYLE_FULL = "full"
ACCENT_STYLE_MINIMAL = "minimal"
ACCENT_STYLES = (ACCENT_STYLE_FULL, ACCENT_STYLE_MINIMAL)

DEFAULT_ACCENT_RGB: tuple[int, int, int] = (127, 27, 228)  # #7f1be4
DEFAULT_ACCENT_STYLE = ACCENT_STYLE_FULL

MINIMAL_TEXT_STANDARD: tuple[int, int, int] = (232, 232, 236)
MINIMAL_TEXT_OLED: tuple[int, int, int] = (235, 235, 240)
MINIMAL_MUTED_STANDARD: tuple[int, int, int] = (130, 130, 140)
MINIMAL_MUTED_OLED: tuple[int, int, int] = (150, 150, 158)

ACCENT_PRESETS: tuple[tuple[str, tuple[int, int, int]], ...] = (
    ("Purple", DEFAULT_ACCENT_RGB),
    ("Blue", (107, 159, 255)),
    ("Violet", (100, 110, 203)),
    ("Teal", (78, 205, 196)),
    ("Amber", (255, 159, 67)),
    ("Rose", (230, 90, 130)),
)

# Primary label / title color (replaces near-white body text).
TEXT: tuple[int, int, int] = DEFAULT_ACCENT_RGB
TEXT_HEX = "#7f1be4"

# Secondary labels, hints, list rows, slider captions (replaces gray "muted" white).
MUTED: tuple[int, int, int] = (96, 40, 180)  # #6028b4
MUTED_HEX = "#6028b4"

# Interactive chrome — sliders, checkbox fills, accent buttons, progress fills, etc.
ACCENT: tuple[int, int, int] = DEFAULT_ACCENT_RGB
ACCENT_HEX = "#7f1be4"


def text_color() -> tuple[int, int, int]:
    """Return the active primary text RGB."""
    return TEXT


def muted_color() -> tuple[int, int, int]:
    """Return the active secondary text RGB."""
    return MUTED


def accent_color() -> tuple[int, int, int]:
    """Return the active accent RGB."""
    return ACCENT


@dataclass(frozen=True)
class Theme:
    bg: tuple[int, int, int]
    surface: tuple[int, int, int]
    surface_alt: tuple[int, int, int]
    playing: tuple[int, int, int]
    danger: tuple[int, int, int]
    ok: tuple[int, int, int]
    # Tiered OLED surfaces — None keeps standard-theme legacy draw behavior.
    surface_elevated: tuple[int, int, int] | None = None
    surface_content: tuple[int, int, int] | None = None
    backdrop_alpha: int | None = None
    hairline_alpha: int = 0
    elevated_top_highlight: bool = False

    @property
    def text(self) -> tuple[int, int, int]:
        return text_color()

    @property
    def muted(self) -> tuple[int, int, int]:
        return muted_color()

    @property
    def accent(self) -> tuple[int, int, int]:
        return accent_color()

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
    playing=(255, 180, 90),
    danger=(220, 90, 90),
    ok=(90, 200, 140),
)

OLED_BLACK_THEME = Theme(
    bg=(0, 0, 0),
    surface=(6, 6, 8),  # #060608 — 1dp header / nav
    surface_alt=(14, 14, 18),  # #0E0E12 — row hover / selected
    playing=(210, 155, 75),
    danger=(210, 85, 85),
    ok=(80, 185, 125),
    surface_elevated=(10, 10, 14),  # #0A0A0E — 2dp panels / modals
    surface_content=(0, 0, 0),  # true-black main content (OLED power)
    backdrop_alpha=128,  # ~50% dim behind overlays
    hairline_alpha=24,  # ~9% accent hairline (bumped for darker surfaces)
    elevated_top_highlight=True,
)


def theme_oled_black() -> Theme:
    return OLED_BLACK_THEME


def theme_for_mode(mode: str) -> Theme:
    if mode == THEME_MODE_OLED_BLACK:
        return theme_oled_black()
    return STANDARD_THEME


@dataclass(frozen=True)
class ThemePreferences:
    theme_mode: str
    accent_rgb: tuple[int, int, int]
    accent_style: str


def derive_muted_from_accent(accent: tuple[int, int, int]) -> tuple[int, int, int]:
    """Tint secondary labels from the active accent (full-accent style)."""
    red, green, blue = accent
    return (
        max(0, min(255, int(red * 0.75))),
        max(0, min(255, int(green * 0.65))),
        max(0, min(255, int(blue * 0.78))),
    )


def minimal_text_for_mode(mode: str) -> tuple[int, int, int]:
    if mode == THEME_MODE_OLED_BLACK:
        return MINIMAL_TEXT_OLED
    return MINIMAL_TEXT_STANDARD


def minimal_muted_for_mode(mode: str) -> tuple[int, int, int]:
    if mode == THEME_MODE_OLED_BLACK:
        return MINIMAL_MUTED_OLED
    return MINIMAL_MUTED_STANDARD


def apply_theme_preferences(prefs: ThemePreferences) -> None:
    """Apply accent/text/muted globals from saved or draft preferences."""
    global ACCENT, TEXT, MUTED

    ACCENT = prefs.accent_rgb
    if prefs.accent_style == ACCENT_STYLE_MINIMAL:
        TEXT = minimal_text_for_mode(prefs.theme_mode)
        MUTED = minimal_muted_for_mode(prefs.theme_mode)
    else:
        TEXT = prefs.accent_rgb
        MUTED = derive_muted_from_accent(prefs.accent_rgb)


def _parse_accent_rgb(raw: object) -> tuple[int, int, int] | None:
    if not isinstance(raw, list) or len(raw) != 3:
        return None
    try:
        return (int(raw[0]), int(raw[1]), int(raw[2]))
    except (TypeError, ValueError):
        return None


def load_theme_preferences(*, default_mode: str = THEME_MODE_STANDARD) -> ThemePreferences:
    mode = load_theme_mode_from_prefs(default=default_mode)
    accent = DEFAULT_ACCENT_RGB
    style = DEFAULT_ACCENT_STYLE
    if UI_STATE_FILE.exists():
        try:
            data = json.loads(UI_STATE_FILE.read_text())
            parsed = _parse_accent_rgb(data.get("accent_rgb"))
            if parsed is not None:
                accent = parsed
            raw_style = data.get("accent_style")
            if raw_style in ACCENT_STYLES:
                style = str(raw_style)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
    return ThemePreferences(theme_mode=mode, accent_rgb=accent, accent_style=style)


def reload_theme_from_prefs(*, default_mode: str = THEME_MODE_STANDARD) -> ThemePreferences:
    prefs = load_theme_preferences(default_mode=default_mode)
    apply_theme_preferences(prefs)
    return prefs


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
