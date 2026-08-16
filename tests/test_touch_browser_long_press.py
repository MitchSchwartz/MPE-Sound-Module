"""Long-press context menu timing tests."""

from __future__ import annotations

import sys
import time
import types
import unittest
from unittest import mock

if "pygame" not in sys.modules:
    sys.modules["pygame"] = mock.MagicMock()

from patch_browser.context_menu import ContextTarget
from patch_browser.touch_browser_context import TouchBrowserContextMixin
from patch_browser.touch_ui_enums import LeftNavMode, Screen


class _LongPressHost(TouchBrowserContextMixin):
    def __init__(self) -> None:
        self.left_nav_collapsed = False
        self.screen_state = Screen.BROWSER
        self.left_nav_mode = LeftNavMode.PATCHES
        self.categories = ["Bass"]
        self._browse_nav_entries = [
            {"kind": "patch", "label": "Acid", "patch": {"name": "Acid", "category": "Bass"}},
        ]
        self.nav_list = mock.Mock()
        self.nav_list.rect.contains.return_value = True
        self.nav_list.item_at.return_value = 0
        self.nav_list._pointer_scrolled = False
        self.width = 800
        self.height = 480
        self._init_context_menu_state()
        self._opened: list[ContextTarget] = []

    def _open_context_menu(self, target: ContextTarget) -> None:
        self._opened.append(target)

    def _patch_is_favorited(self, _patch: dict) -> bool:
        return False

    def _browse_category_name(self) -> str:
        return "Bass"

    def _browse_inner_segments(self) -> tuple[str, ...]:
        return ()


class LongPressTickTests(unittest.TestCase):
    def test_tick_opens_menu_after_hold_without_scroll(self) -> None:
        host = _LongPressHost()
        host._context_nav_pointer_down((100, 200))
        host._long_press_pending["started"] = time.time() - 0.7
        host._tick_long_press()
        self.assertEqual(len(host._opened), 1)
        self.assertEqual(host._opened[0].kind, "patch")

    def test_tick_cancels_when_list_scrolled(self) -> None:
        host = _LongPressHost()
        host._context_nav_pointer_down((100, 200))
        host.nav_list._pointer_scrolled = True
        host._long_press_pending["started"] = time.time() - 0.7
        host._tick_long_press()
        self.assertEqual(host._opened, [])


if __name__ == "__main__":
    unittest.main()
