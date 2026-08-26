"""Minimal on-screen keyboard for WPA passwords on the touch panel."""

from __future__ import annotations

from collections.abc import Callable

from patch_browser.geometry import Rect

LOWER_ROWS = ("qwertyuiop", "asdfghjkl", "zxcvbnm")
NUMBER_ROW = "0123456789"
SYMBOL_ROW = "-_.!@#$%&*"


def wifi_password_char_visible(ch: str) -> str:
    if ch == " ":
        return "␣"
    return ch


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
        draw_button(kb.backspace_rect, "⌫", small=True, pressed=pressed_key == "backspace")
    if kb.space_rect:
        draw_button(kb.space_rect, "space", small=True, pressed=pressed_key == " ")


class TouchKeyboardLayout:
    """Compute key rectangles for a compact keyboard inside a panel."""

    def __init__(
        self,
        panel: Rect,
        *,
        row_h: int = 36,
        row_gap: int = 5,
        key_gap: int = 4,
    ) -> None:
        self.panel = panel
        self.row_h = row_h
        self.row_gap = row_gap
        self.key_gap = key_gap
        self.keys: list[tuple[Rect, str]] = []
        self.backspace_rect: Rect | None = None
        self.space_rect: Rect | None = None
        self._layout()

    def _add_even_row(self, y: int, labels: list[str], inner_x: int, inner_w: int) -> int:
        if not labels:
            return y
        total_gap = self.key_gap * max(0, len(labels) - 1)
        key_w = max(24, int((inner_w - total_gap) / len(labels)))
        x = inner_x
        for label in labels:
            rect = Rect(x, y, key_w, self.row_h)
            if label == "⌫":
                self.backspace_rect = rect
            elif label == "space":
                self.space_rect = rect
            else:
                self.keys.append((rect, label))
            x += key_w + self.key_gap
        return y + self.row_h + self.row_gap

    def _layout(self) -> None:
        inner_x = self.panel.x + 12
        inner_w = self.panel.w - 24
        y = self.panel.y + 4
        y = self._add_even_row(y, list(LOWER_ROWS[0]), inner_x, inner_w)
        y = self._add_even_row(y, list(LOWER_ROWS[1]), inner_x, inner_w)
        y = self._add_even_row(y, list(LOWER_ROWS[2]) + ["⌫"], inner_x, inner_w)
        y = self._add_even_row(y, list(NUMBER_ROW), inner_x, inner_w)
        y = self._add_even_row(y, list(SYMBOL_ROW), inner_x, inner_w)
        self._add_even_row(y, ["space"], inner_x, inner_w)

    def hit(self, pos: tuple[int, int]) -> str | None:
        if self.backspace_rect and self.backspace_rect.contains(*pos):
            return "backspace"
        if self.space_rect and self.space_rect.contains(*pos):
            return " "
        for rect, label in self.keys:
            if rect.contains(*pos):
                return label
        return None
