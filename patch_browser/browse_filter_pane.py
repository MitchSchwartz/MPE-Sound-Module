"""Masonry packing for the browse carousel's filter pane tags.

Pure geometry — no pygame — so packing can be unit tested without a
display. See
Documents/specs/touch-browser-browse-carousel-spec.md §Filter pane UI.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from patch_browser.geometry import Rect

ALL_INSTRUMENT_TAG = "__all__"


@dataclass
class PackedTag:
    tag_id: str
    label: str
    rect: Rect


def layout_filter_pane_tags(
    tag_ids: list[str],
    *,
    label_fn: Callable[[str], str],
    measure_fn: Callable[[str], int],
    content_rect: Rect,
    row_h: int,
    pad_x: int,
    gap: int,
    min_chip_w: int = 0,
) -> tuple[list[PackedTag], int]:
    """Pack tags left-to-right, wrapping rows — chip width follows label width.

    `label_fn` should return the exact text that will be rendered (e.g.
    "Bass (3)"); `measure_fn` measures that same text so packed width
    always matches what gets drawn. Returns the packed tags plus the
    total content height consumed (>= row_h, even for zero tags).
    """
    x = content_rect.x
    y = content_rect.y
    row_start_x = x
    max_x = content_rect.right
    packed: list[PackedTag] = []
    for tag_id in tag_ids:
        label = label_fn(tag_id)
        chip_w = max(measure_fn(label) + pad_x * 2, min_chip_w)
        if x + chip_w > max_x and x > row_start_x:
            x = row_start_x
            y += row_h + gap
        packed.append(PackedTag(tag_id, label, Rect(x, y, chip_w, row_h)))
        x += chip_w + gap
    content_h = max(row_h, y + row_h - content_rect.y)
    return packed, content_h
