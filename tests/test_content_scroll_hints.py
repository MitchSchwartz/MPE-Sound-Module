"""Tests for ContentScrollArea overflow hint helpers."""

from __future__ import annotations

import unittest

from patch_browser.geometry import Rect
from patch_browser.scroll_widgets import ContentScrollArea


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


if __name__ == "__main__":
    unittest.main()
