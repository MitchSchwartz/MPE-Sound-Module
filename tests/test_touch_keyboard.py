"""Touch on-screen keyboard layout and hit targets."""

from __future__ import annotations

import unittest

from patch_browser.geometry import Rect
from patch_browser.touch_keyboard import KeyboardProfile, TouchKeyboardLayout, backspace_key_label


class TouchKeyboardLayoutTests(unittest.TestCase):
    def test_password_profile_has_symbols_and_wide_backspace(self) -> None:
        panel = Rect(0, 0, 400, 280)
        kb = TouchKeyboardLayout(panel, profile=KeyboardProfile.PASSWORD)
        labels = {label for _rect, label in kb.keys}
        self.assertIn("-", labels)
        self.assertIsNotNone(kb.backspace_rect)
        self.assertIsNotNone(kb.space_rect)
        assert kb.backspace_rect is not None
        self.assertGreater(kb.backspace_rect.w, 70)

    def test_text_profile_omits_symbols(self) -> None:
        panel = Rect(0, 0, 400, 220)
        kb = TouchKeyboardLayout(panel, profile=KeyboardProfile.TEXT)
        labels = {label for _rect, label in kb.keys}
        self.assertNotIn("-", labels)
        self.assertIn("a", labels)

    def test_backspace_hit_on_bottom_row(self) -> None:
        panel = Rect(0, 0, 360, 260)
        kb = TouchKeyboardLayout(panel, profile=KeyboardProfile.TEXT)
        assert kb.backspace_rect is not None
        cx = kb.backspace_rect.x + kb.backspace_rect.w // 2
        cy = kb.backspace_rect.y + kb.backspace_rect.h // 2
        self.assertEqual(kb.hit((cx, cy)), "backspace")

    def test_space_hit(self) -> None:
        panel = Rect(0, 0, 360, 260)
        kb = TouchKeyboardLayout(panel)
        assert kb.space_rect is not None
        cx = kb.space_rect.x + kb.space_rect.w // 2
        cy = kb.space_rect.y + kb.space_rect.h // 2
        self.assertEqual(kb.hit((cx, cy)), " ")

    def test_scales_row_height_to_fit_panel(self) -> None:
        panel = Rect(0, 0, 320, 160)
        kb = TouchKeyboardLayout(panel, profile=KeyboardProfile.TEXT, row_h=36)
        self.assertLessEqual(kb.row_h, 36)
        bottom = max(
            (r.bottom for r, _ in kb.keys),
            default=0,
        )
        if kb.backspace_rect:
            bottom = max(bottom, kb.backspace_rect.bottom)
        self.assertLessEqual(bottom, panel.bottom + 2)

    def test_backspace_label_wider_for_big_key(self) -> None:
        wide = Rect(0, 0, 120, 36)
        narrow = Rect(0, 0, 40, 36)
        self.assertEqual(backspace_key_label(wide), "Backspace")
        self.assertEqual(backspace_key_label(narrow), "⌫")


if __name__ == "__main__":
    unittest.main()
