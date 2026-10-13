"""Jump strip geometry: finger y on the strip to a section or a scroll position.

Pure arithmetic so the mapping is testable without pygame. The browser owns the
strip's rect and the list; these functions only convert between them.
"""

from __future__ import annotations


def section_cell_at(y: float, top: int, height: int, count: int) -> int:
    """Which of ``count`` equal cells (top to bottom) a finger at ``y`` is on.

    Clamped, so a drag past either end stays on the first or last section.
    """
    if count <= 0:
        raise ValueError("no sections")
    if height <= 0:
        return 0
    cell = int((y - top) * count // height)
    return max(0, min(count - 1, cell))


def section_cell_bounds(index: int, top: int, height: int, count: int) -> tuple[int, int]:
    """(y0, y1) of cell ``index``: the inverse of ``section_cell_at``."""
    y0 = top + (index * height + count - 1) // count
    y1 = top + ((index + 1) * height + count - 1) // count
    return y0, y1


def scrub_fraction(y: float, top: int, height: int, thumb_h: int) -> float:
    """Scroll fraction (0 = top, 1 = bottom) for a finger at ``y``.

    The finger holds the thumb's centre, so the thumb sits where it is drawn
    (``thumb_top``) and the fraction is clamped to [0, 1].
    """
    travel = height - thumb_h
    if travel <= 0:
        return 0.0
    return max(0.0, min(1.0, (y - top - thumb_h / 2) / travel))


def thumb_height(height: int, viewport_h: int, content_h: int, min_h: int) -> int:
    """Thumb length in proportion to how much of the list is on screen."""
    if content_h <= 0 or viewport_h >= content_h:
        return height
    return max(min_h, min(height, int(height * viewport_h / content_h)))


def thumb_top(fraction: float, top: int, height: int, thumb_h: int) -> int:
    return top + int(round(max(0.0, min(1.0, fraction)) * (height - thumb_h)))
