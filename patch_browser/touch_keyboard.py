"""On-screen keyboard for touch text entry (Wi‑Fi passwords, names, etc.)."""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum

from patch_browser.geometry import Rect

LOWER_ROWS = ("qwertyuiop", "asdfghjkl", "zxcvbnm")
NUMBER_ROW = "0123456789"
SYMBOL_ROW = "-_.!@#$%&*"


class KeyboardProfile(Enum):
    """Layout density — password keeps symbols; text is names/folders."""

    PASSWORD = "password"
    TEXT = "text"


def wifi_password_char_visible(ch: str) -> str:
    if ch == " ":
        return "␣"
    return ch


def backspace_key_label(rect: Rect) -> str:
    if rect.w >= 96:
        return "Backspace"
    if rect.w >= 56:
        return "⌫"
    return "⌫"


def draw_touch_keyboard(
    kb: TouchKeyboardLayout,
    *,
    draw_button: Callable[..., None],
    pressed_key: str | None,
) -> None:
    """Draw keyboard keys via the host's _draw_button (Wi‑Fi + looper song name)."""
    for rect, label in kb.keys:
        draw_button(rect, label, small=True, pressed=pressed_key == label)
    if kb.backspace_rect:
        draw_button(
            kb.backspace_rect,
            backspace_key_label(kb.backspace_rect),
            small=True,
            pressed=pressed_key == "backspace",
        )
    if kb.space_rect:
        draw_button(kb.space_rect, "space", small=True, pressed=pressed_key == " ")


class TouchKeyboardLayout:
    """Compute key rectangles for a compact keyboard inside a panel."""

    def __init__(
        self,
        panel: Rect,
        *,
        profile: KeyboardProfile = KeyboardProfile.PASSWORD,
        row_h: int = 36,
        row_gap: int = 5,
        key_gap: int = 4,
    ) -> None:
        self.panel = panel
        self.profile = profile
        self.row_h = row_h
        self.row_gap = row_gap
        self.key_gap = key_gap
        self.keys: list[tuple[Rect, str]] = []
        self.backspace_rect: Rect | None = None
        self.space_rect: Rect | None = None
        self._layout()

    def _row_count(self) -> int:
        if self.profile == KeyboardProfile.TEXT:
            return len(LOWER_ROWS) + 1 + 1  # letters + numbers + bottom
        return len(LOWER_ROWS) + 2 + 1  # letters + numbers + symbols + bottom

    def _fit_row_height(self) -> None:
        rows = self._row_count()
        if rows <= 0 or self.panel.h <= 0:
            return
        padding = 8
        total_gap = self.row_gap * max(0, rows - 1)
        max_row_h = int((self.panel.h - padding - total_gap) / rows)
        if max_row_h < self.row_h:
            self.row_h = max(28, max_row_h)

    def _add_even_row(self, y: int, labels: list[str], inner_x: int, inner_w: int) -> int:
        if not labels:
            return y
        total_gap = self.key_gap * max(0, len(labels) - 1)
        key_w = max(24, int((inner_w - total_gap) / len(labels)))
        x = inner_x
        for label in labels:
            rect = Rect(x, y, key_w, self.row_h)
            self.keys.append((rect, label))
            x += key_w + self.key_gap
        return y + self.row_h + self.row_gap

    def _add_bottom_row(self, y: int, inner_x: int, inner_w: int) -> int:
        gap = self.key_gap
        back_w = max(72, int(inner_w * 0.24))
        space_w = max(48, inner_w - back_w - gap)
        self.space_rect = Rect(inner_x, y, space_w, self.row_h)
        self.backspace_rect = Rect(inner_x + space_w + gap, y, back_w, self.row_h)
        return y + self.row_h

    def _layout(self) -> None:
        self.keys.clear()
        self.backspace_rect = None
        self.space_rect = None
        self._fit_row_height()

        inner_x = self.panel.x + 12
        inner_w = max(0, self.panel.w - 24)
        y = self.panel.y + 4

        y = self._add_even_row(y, list(LOWER_ROWS[0]), inner_x, inner_w)
        y = self._add_even_row(y, list(LOWER_ROWS[1]), inner_x, inner_w)
        y = self._add_even_row(y, list(LOWER_ROWS[2]), inner_x, inner_w)
        y = self._add_even_row(y, list(NUMBER_ROW), inner_x, inner_w)
        if self.profile == KeyboardProfile.PASSWORD:
            y = self._add_even_row(y, list(SYMBOL_ROW), inner_x, inner_w)
        self._add_bottom_row(y, inner_x, inner_w)

    def hit(self, pos: tuple[int, int]) -> str | None:
        if self.backspace_rect and self.backspace_rect.contains(*pos):
            return "backspace"
        if self.space_rect and self.space_rect.contains(*pos):
            return " "
        for rect, label in self.keys:
            if rect.contains(*pos):
                return label
        return None
