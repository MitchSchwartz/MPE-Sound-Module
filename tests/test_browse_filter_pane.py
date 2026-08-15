"""Unit tests for patch_browser.browse_filter_pane masonry packing.

Pure geometry — no pygame required. See
Documents/specs/touch-browser-browse-carousel-spec.md §Filter pane UI
and acceptance criterion 12.
"""

from __future__ import annotations

import sys
import types
import unittest


class _FakePygameRect:
    def __init__(self, x, y, w, h):
        self.x, self.y, self.w, self.h = x, y, w, h


def _install_fake_pygame() -> None:
    if isinstance(sys.modules.get("pygame"), types.ModuleType):
        return
    fake = types.ModuleType("pygame")
    fake.error = type("error", (Exception,), {})
    # patch_browser.geometry does `import pygame` once and the binding is
    # cached for the process — a later, fuller pygame stub installed by
    # another test module never reaches it. Since this file may be the
    # first to import geometry (alphabetically early), the stub must
    # cover geometry.Rect.pygame_rect's only dependency: Rect itself.
    fake.Rect = _FakePygameRect
    sys.modules["pygame"] = fake


_install_fake_pygame()

from patch_browser.browse_filter_pane import layout_filter_pane_tags  # noqa: E402
from patch_browser.geometry import Rect  # noqa: E402


def _measure(label: str) -> int:
    """Deterministic stand-in for font.size(label)[0]."""
    return len(label) * 8


class LayoutFilterPaneTagsTests(unittest.TestCase):
    def test_empty_tags_yields_row_height_only(self) -> None:
        content = Rect(0, 0, 400, 200)
        packed, height = layout_filter_pane_tags(
            [],
            label_fn=lambda t: t,
            measure_fn=_measure,
            content_rect=content,
            row_h=24,
            pad_x=8,
            gap=6,
        )
        self.assertEqual(packed, [])
        self.assertEqual(height, 24)

    def test_chip_width_follows_label_length(self) -> None:
        content = Rect(0, 0, 400, 200)
        packed, _height = layout_filter_pane_tags(
            ["bass", "a"],
            label_fn=lambda t: t,
            measure_fn=_measure,
            content_rect=content,
            row_h=24,
            pad_x=8,
            gap=6,
        )
        bass_rect = packed[0].rect
        a_rect = packed[1].rect
        self.assertEqual(bass_rect.w, _measure("bass") + 16)
        self.assertEqual(a_rect.w, _measure("a") + 16)
        self.assertGreater(bass_rect.w, a_rect.w)

    def test_wraps_to_next_row_when_out_of_width(self) -> None:
        content = Rect(0, 0, 60, 200)  # narrow: fits one chip per row
        packed, height = layout_filter_pane_tags(
            ["alpha", "bravo", "charlie"],
            label_fn=lambda t: t,
            measure_fn=_measure,
            content_rect=content,
            row_h=24,
            pad_x=8,
            gap=6,
        )
        rows = {tag.rect.y for tag in packed}
        self.assertEqual(len(rows), 3)
        self.assertEqual(height, 3 * 24 + 2 * 6)

    def test_first_chip_always_placed_even_if_wider_than_content(self) -> None:
        content = Rect(0, 0, 10, 200)
        packed, _height = layout_filter_pane_tags(
            ["much-too-wide-for-the-pane"],
            label_fn=lambda t: t,
            measure_fn=_measure,
            content_rect=content,
            row_h=24,
            pad_x=8,
            gap=6,
        )
        self.assertEqual(len(packed), 1)
        self.assertEqual(packed[0].rect.x, content.x)

    def test_label_and_measured_text_stay_in_sync(self) -> None:
        # Regression guard: chip width must always reflect the exact
        # string that gets drawn (count included), not a bare label.
        content = Rect(0, 0, 400, 200)
        packed, _height = layout_filter_pane_tags(
            ["bass"],
            label_fn=lambda t: f"{t} (3)",
            measure_fn=_measure,
            content_rect=content,
            row_h=24,
            pad_x=8,
            gap=6,
        )
        self.assertEqual(packed[0].label, "bass (3)")
        self.assertEqual(packed[0].rect.w, _measure("bass (3)") + 16)

    def test_content_offset_respected(self) -> None:
        content = Rect(48, 20, 400, 200)
        packed, _height = layout_filter_pane_tags(
            ["bass"],
            label_fn=lambda t: t,
            measure_fn=_measure,
            content_rect=content,
            row_h=24,
            pad_x=8,
            gap=6,
        )
        self.assertEqual(packed[0].rect.x, 48)
        self.assertEqual(packed[0].rect.y, 20)


if __name__ == "__main__":
    unittest.main()
