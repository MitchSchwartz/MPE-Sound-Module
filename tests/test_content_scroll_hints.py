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


class ScrollHintStyleTests(unittest.TestCase):
    def test_oled_style_is_stronger_than_standard(self) -> None:
        oled_rgb, oled_h, oled_power, oled_line, oled_chevron = _scroll_hint_style(
            OLED_BLACK_THEME,
            None,
        )
        std_rgb, std_h, std_power, std_line, std_chevron = _scroll_hint_style(
            STANDARD_THEME,
            None,
        )
        self.assertGreater(sum(oled_rgb), sum(std_rgb))
        self.assertGreater(oled_h, std_h)
        self.assertLess(oled_power, std_power)
        self.assertGreater(oled_line, std_line)
        self.assertTrue(oled_chevron)
        self.assertFalse(std_chevron)


if __name__ == "__main__":
    unittest.main()
