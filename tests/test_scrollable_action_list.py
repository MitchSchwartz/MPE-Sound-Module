"""Tests for ScrollableActionList — scrollable bottom-sheet menus."""

from __future__ import annotations

import unittest

from patch_browser.context_menu import instrument_picker_actions
from patch_browser.scroll_widgets import ScrollableActionList


class ScrollableActionListTests(unittest.TestCase):
    def _sheet(self) -> ScrollableActionList:
        sheet = ScrollableActionList()
        sheet.layout(
            screen_w=480,
            screen_h=320,
            margin=16,
            bottom_margin=8,
            actions=instrument_picker_actions(),
            row_h=52,
            gap=8,
        )
        return sheet

    def test_instrument_picker_is_scrollable(self) -> None:
        sheet = self._sheet()
        self.assertTrue(sheet.is_scrollable)
        self.assertGreater(sheet.scroll.content_height, sheet.scroll_viewport.h)

    def test_action_at_bottom_row_after_scroll(self) -> None:
        sheet = self._sheet()
        last_row = sheet.rows[-1]
        sheet.scroll._scroll_pixels = float(last_row.content_rect.y)
        local_y = sheet.scroll_viewport.y + last_row.content_rect.h // 2
        pos = (sheet.scroll_viewport.x + 20, local_y)
        action_id = sheet.action_at(pos)
        self.assertTrue(action_id.startswith("pick_instrument:"))

    def test_pointer_up_scroll_suppresses_tap(self) -> None:
        sheet = self._sheet()
        down = (sheet.scroll_viewport.x + 10, sheet.scroll_viewport.y + 10)
        move = (down[0], down[1] - 40)
        sheet.pointer_down(down)
        sheet.pointer_move(move)
        scrolled = sheet.pointer_up(move)
        self.assertTrue(scrolled)

    def test_pressed_action_id_on_pointer_down(self) -> None:
        sheet = self._sheet()
        first_row = next(row for row in sheet.rows if not row.is_section)
        pos = (
            sheet.scroll_viewport.x + first_row.content_rect.w // 2,
            sheet.scroll_viewport.y + first_row.content_rect.h // 2,
        )
        sheet.pointer_down(pos)
        self.assertEqual(sheet.pressed_action_id, first_row.action_id)
        sheet.pointer_up(pos)
        self.assertIsNone(sheet.pressed_action_id)

    def test_contains_accepts_pos_tuple(self) -> None:
        sheet = self._sheet()
        inside = (sheet.panel.x + 10, sheet.panel.y + 10)
        outside = (0, 0)
        self.assertTrue(sheet.contains(inside))
        self.assertFalse(sheet.contains(outside))


if __name__ == "__main__":
    unittest.main()
