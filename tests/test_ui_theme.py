"""UI theme accent is a single live knob, not duplicated per theme."""

from __future__ import annotations

import unittest

import patch_browser.ui_theme as ui_theme


class UiThemeAccentTests(unittest.TestCase):
    def test_both_themes_read_shared_accent(self) -> None:
        self.assertEqual(ui_theme.STANDARD_THEME.accent, ui_theme.ACCENT)
        self.assertEqual(ui_theme.OLED_BLACK_THEME.accent, ui_theme.ACCENT)
        self.assertEqual(ui_theme.accent_color(), ui_theme.ACCENT)

    def test_changing_accent_updates_theme_property(self) -> None:
        original = ui_theme.ACCENT
        try:
            ui_theme.ACCENT = (12, 34, 56)
            self.assertEqual(ui_theme.STANDARD_THEME.accent, (12, 34, 56))
            self.assertEqual(ui_theme.OLED_BLACK_THEME.accent, (12, 34, 56))
        finally:
            ui_theme.ACCENT = original

    def test_brand_accent_is_not_legacy_blue(self) -> None:
        legacy_standard_blue = (107, 159, 255)
        legacy_oled_blue = (90, 130, 210)
        self.assertNotEqual(ui_theme.ACCENT, legacy_standard_blue)
        self.assertNotEqual(ui_theme.ACCENT, legacy_oled_blue)


if __name__ == "__main__":
    unittest.main()
