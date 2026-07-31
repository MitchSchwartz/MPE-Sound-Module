"""Text layout helpers for pygame touch UI (wrap, ellipsize, block draw)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pygame


def wrap_text_lines(
    font: pygame.font.Font,
    text: str,
    max_width: int,
    *,
    max_lines: int | None = None,
) -> list[str]:
    """Word-wrap *text* to fit *max_width* pixels. Long tokens break by character."""
    if max_width <= 0:
        return [text] if text else []
    if not text:
        return [""]

    words = text.split()
    if not words:
        return [""]

    lines: list[str] = []
    current = ""

    def _flush() -> None:
        nonlocal current
        if current:
            lines.append(current)
            current = ""

    def _append_word(word: str) -> None:
        nonlocal current
        if not current:
            if font.size(word)[0] <= max_width:
                current = word
                return
            lines.extend(_break_word(font, word, max_width))
            return
        trial = f"{current} {word}"
        if font.size(trial)[0] <= max_width:
            current = trial
            return
        _flush()
        _append_word(word)

    for word in words:
        _append_word(word)
        if max_lines is not None and len(lines) >= max_lines:
            current = ""
            break
    if max_lines is not None and len(lines) >= max_lines:
        lines = lines[:max_lines]
        lines[-1] = ellipsize_text(font, lines[-1], max_width)
    else:
        _flush()
        if max_lines is not None and len(lines) > max_lines:
            lines = lines[:max_lines]
            lines[-1] = ellipsize_text(font, lines[-1], max_width)

    return lines or [""]


def _break_word(font: pygame.font.Font, word: str, max_width: int) -> list[str]:
    chunks: list[str] = []
    current = ""
    for ch in word:
        trial = current + ch
        if font.size(trial)[0] <= max_width:
            current = trial
        else:
            if current:
                chunks.append(current)
            current = ch
    if current:
        chunks.append(current)
    return chunks or [word[:1]]


def ellipsize_text(font: pygame.font.Font, text: str, max_width: int) -> str:
    """Single-line truncate with ellipsis when *text* exceeds *max_width*."""
    if max_width <= 0 or not text:
        return text
    if font.size(text)[0] <= max_width:
        return text
    ellipsis = "…"
    if font.size(ellipsis)[0] > max_width:
        return ellipsis[:1]
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if font.size(text[:mid] + ellipsis)[0] <= max_width:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + ellipsis if lo > 0 else ellipsis


def text_block_height(
    font: pygame.font.Font,
    line_count: int,
    *,
    line_spacing: int = 4,
) -> int:
    if line_count <= 0:
        return 0
    line_h = font.get_linesize()
    return line_count * line_h + max(0, line_count - 1) * line_spacing


def wrapped_row_height(
    font: pygame.font.Font,
    text: str,
    max_width: int,
    *,
    min_height: int = 52,
    line_spacing: int = 4,
    max_lines: int = 2,
    vertical_pad: int = 12,
) -> int:
    lines = wrap_text_lines(font, text, max_width, max_lines=max_lines)
    content_h = text_block_height(font, len(lines), line_spacing=line_spacing)
    return max(min_height, content_h + vertical_pad)


def blit_text_block(
    surface: pygame.Surface,
    font: pygame.font.Font,
    lines: list[str],
    x: int,
    y: int,
    color: tuple[int, int, int],
    *,
    line_spacing: int = 4,
) -> int:
    """Draw *lines* starting at (x, y). Returns y after the last line."""
    line_h = font.get_linesize()
    cy = y
    for line in lines:
        surf = font.render(line, True, color)
        surface.blit(surf, (x, cy))
        cy += line_h + line_spacing
    return cy - line_spacing if lines else y


def draw_wrapped_text_in_rect(
    surface: pygame.Surface,
    font: pygame.font.Font,
    text: str,
    rect_x: int,
    rect_y: int,
    rect_w: int,
    rect_h: int,
    color: tuple[int, int, int],
    *,
    pad_x: int = 0,
    line_spacing: int = 4,
    max_lines: int | None = None,
    align: str = "left",
) -> None:
    """Draw wrapped *text* vertically centered in a rect."""
    max_text_w = max(1, rect_w - pad_x * 2)
    lines = wrap_text_lines(font, text, max_text_w, max_lines=max_lines)
    block_h = text_block_height(font, len(lines), line_spacing=line_spacing)
    start_y = rect_y + max(0, (rect_h - block_h) // 2)
    for i, line in enumerate(lines):
        surf = font.render(line, True, color)
        if align == "center":
            tx = rect_x + (rect_w - surf.get_width()) // 2
        else:
            tx = rect_x + pad_x
        ty = start_y + i * (font.get_linesize() + line_spacing)
        surface.blit(surf, (tx, ty))
