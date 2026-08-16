"""Tests for ContentScrollArea overflow hint helpers."""

from __future__ import annotations

import unittest

from patch_browser.geometry import Rect
from patch_browser.scroll_widgets import ContentScrollArea, _scroll_hint_style
from patch_browser.ui_theme import OLED_BLACK_THEME, STANDARD_THEME


class ContentScrollHintTests(unittest.TestCase):
    def test_not_scrollable_when_content_fits(self) -> None:
        scroll = ContentScrollArea(Rect(0, 0, 100, 200))
        scroll.content_height = 200
        self.assertFalse(scroll.is_scrollable())
        self.assertFalse(scroll.can_scroll_up())
        self.assertFalse(scroll.can_scroll_down())

    def test_can_scroll_down_from_top(self) -> None:
        scroll = ContentScrollArea(Rect(0, 0, 100, 100))
        scroll.content_height = 300
        self.assertTrue(scroll.is_scrollable())
        self.assertFalse(scroll.can_scroll_up())
        self.assertTrue(scroll.can_scroll_down())

    def test_can_scroll_up_when_scrolled(self) -> None:
        scroll = ContentScrollArea(Rect(0, 0, 100, 100))
        scroll.content_height = 300
        scroll._scroll_pixels = 120.0
        self.assertTrue(scroll.can_scroll_up())
        self.assertTrue(scroll.can_scroll_down())

    def test_cannot_scroll_down_at_bottom(self) -> None:
        scroll = ContentScrollArea(Rect(0, 0, 100, 100))
        scroll.content_height = 300
        scroll._scroll_pixels = scroll._max_scroll_pixels()
        self.assertTrue(scroll.can_scroll_up())
        self.assertFalse(scroll.can_scroll_down())

    def test_edge_hints_ease_in_when_scrollable(self) -> None:
        scroll = ContentScrollArea(Rect(0, 0, 100, 100))
        scroll.content_height = 300
        self.assertEqual(scroll.edge_hint_strength("bottom"), 0.0)
        scroll.tick_edge_hints(0.1)
        self.assertGreater(scroll.edge_hint_strength("bottom"), 0.0)
        self.assertLess(scroll.edge_hint_strength("bottom"), 1.0)

    def test_edge_hints_ease_out_when_not_needed(self) -> None:
        scroll = ContentScrollArea(Rect(0, 0, 100, 100))
        scroll.content_height = 300
        scroll._hint_bottom = 1.0
        scroll._scroll_pixels = scroll._max_scroll_pixels()
        scroll.tick_edge_hints(0.1)
        self.assertLess(scroll.edge_hint_strength("bottom"), 1.0)


class ScrollHintStyleTests(unittest.TestCase):
    def test_oled_uses_subtle_fade_and_chevron(self) -> None:
        rgb, fade_h, _power, max_opacity, chevron = _scroll_hint_style(OLED_BLACK_THEME, None)
        self.assertTrue(chevron)
        self.assertLessEqual(fade_h, 20)
        self.assertLess(max_opacity, 0.2)

    def test_standard_uses_lighter_chevron_off(self) -> None:
        _rgb, fade_h, _power, max_opacity, chevron = _scroll_hint_style(STANDARD_THEME, None)
        self.assertFalse(chevron)
        self.assertLessEqual(fade_h, 20)
        self.assertLess(max_opacity, 0.35)


if __name__ == "__main__":
    unittest.main()
