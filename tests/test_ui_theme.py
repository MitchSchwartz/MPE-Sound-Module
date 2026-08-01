"""UI theme accent is a single live knob, not duplicated per theme."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import patch_browser.ui_theme as ui_theme


class UiThemeAccentTests(unittest.TestCase):
    def test_both_themes_read_shared_accent(self) -> None:
        self.assertEqual(ui_theme.STANDARD_THEME.accent, ui_theme.ACCENT)
        self.assertEqual(ui_theme.OLED_BLACK_THEME.accent, ui_theme.ACCENT)
        self.assertEqual(ui_theme.accent_color(), ui_theme.ACCENT)

    def test_both_themes_read_shared_text(self) -> None:
        self.assertEqual(ui_theme.STANDARD_THEME.text, ui_theme.TEXT)
        self.assertEqual(ui_theme.OLED_BLACK_THEME.text, ui_theme.TEXT)
        self.assertEqual(ui_theme.text_color(), ui_theme.TEXT)

    def test_both_themes_read_shared_muted(self) -> None:
        self.assertEqual(ui_theme.STANDARD_THEME.muted, ui_theme.MUTED)
        self.assertEqual(ui_theme.OLED_BLACK_THEME.muted, ui_theme.MUTED)
        self.assertEqual(ui_theme.muted_color(), ui_theme.MUTED)

    def test_text_and_accent_are_separate_knobs(self) -> None:
        self.assertEqual(ui_theme.TEXT, ui_theme.ACCENT)
        original_text = ui_theme.TEXT
        original_accent = ui_theme.ACCENT
        try:
            ui_theme.TEXT = (200, 200, 200)
            ui_theme.ACCENT = (10, 20, 30)
            self.assertEqual(ui_theme.STANDARD_THEME.text, (200, 200, 200))
            self.assertEqual(ui_theme.STANDARD_THEME.accent, (10, 20, 30))
            self.assertNotEqual(ui_theme.TEXT, ui_theme.ACCENT)
        finally:
            ui_theme.TEXT = original_text
            ui_theme.ACCENT = original_accent

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

    def test_brand_text_is_not_legacy_white(self) -> None:
        legacy_standard_text = (232, 232, 236)
        legacy_oled_text = (235, 235, 240)
        self.assertNotEqual(ui_theme.TEXT, legacy_standard_text)
        self.assertNotEqual(ui_theme.TEXT, legacy_oled_text)

    def test_brand_muted_is_not_legacy_gray(self) -> None:
        legacy_standard_muted = (130, 130, 140)
        legacy_oled_muted = (150, 150, 158)
        self.assertNotEqual(ui_theme.MUTED, legacy_standard_muted)
        self.assertNotEqual(ui_theme.MUTED, legacy_oled_muted)


class UiThemePreferencesTests(unittest.TestCase):
    def test_monochrome_style_uses_accent_for_text(self) -> None:
        prefs = ui_theme.ThemePreferences(
            theme_mode=ui_theme.THEME_MODE_OLED_BLACK,
            accent_rgb=(200, 100, 50),
            accent_style=ui_theme.ACCENT_STYLE_MONOCHROME,
        )
        ui_theme.apply_theme_preferences(prefs)
        self.assertEqual(ui_theme.ACCENT, (200, 100, 50))
        self.assertEqual(ui_theme.TEXT, (200, 100, 50))
        self.assertEqual(ui_theme.MUTED, ui_theme.derive_muted_from_accent((200, 100, 50)))
        self.assertTrue(ui_theme.is_monochrome_style())

    def test_monochrome_style_maps_playing_to_accent_not_danger(self) -> None:
        prefs = ui_theme.ThemePreferences(
            theme_mode=ui_theme.THEME_MODE_STANDARD,
            accent_rgb=(12, 34, 56),
            accent_style=ui_theme.ACCENT_STYLE_MONOCHROME,
        )
        ui_theme.apply_theme_preferences(prefs)
        theme = ui_theme.STANDARD_THEME
        self.assertEqual(ui_theme.theme_semantic_color(theme, "playing"), (12, 34, 56))
        self.assertEqual(ui_theme.theme_semantic_color(theme, "ok"), (12, 34, 56))
        self.assertEqual(ui_theme.theme_semantic_color(theme, "danger"), theme.danger)

    def test_minimal_accent_style_keeps_legacy_text(self) -> None:
        prefs = ui_theme.ThemePreferences(
            theme_mode=ui_theme.THEME_MODE_OLED_BLACK,
            accent_rgb=(200, 100, 50),
            accent_style=ui_theme.ACCENT_STYLE_MINIMAL,
        )
        ui_theme.apply_theme_preferences(prefs)
        self.assertEqual(ui_theme.ACCENT, (200, 100, 50))
        self.assertEqual(ui_theme.TEXT, ui_theme.MINIMAL_TEXT_OLED)
        self.assertEqual(ui_theme.MUTED, ui_theme.MINIMAL_MUTED_OLED)
        theme = ui_theme.OLED_BLACK_THEME
        self.assertEqual(ui_theme.theme_semantic_color(theme, "playing"), theme.playing)
        self.assertEqual(ui_theme.theme_semantic_color(theme, "ok"), (200, 100, 50))
        self.assertEqual(ui_theme.theme_semantic_color(theme, "danger"), theme.danger)

    def test_legacy_full_accent_style_loads_as_monochrome(self) -> None:
        self.assertEqual(
            ui_theme.normalize_accent_style("full"),
            ui_theme.ACCENT_STYLE_MONOCHROME,
        )
        with tempfile.TemporaryDirectory() as tmp:
            prefs_path = Path(tmp) / "ui.json"
            prefs_path.write_text(json.dumps({"accent_style": "full"}))
            with mock.patch.object(ui_theme, "UI_STATE_FILE", prefs_path):
                prefs = ui_theme.load_theme_preferences()
        self.assertEqual(prefs.accent_style, ui_theme.ACCENT_STYLE_MONOCHROME)

    def test_custom_accent_colors_round_trip(self) -> None:
        colors = [
            ui_theme.SavedAccentColor("abc123", "#aabbcc", (170, 187, 204)),
            ui_theme.SavedAccentColor("def456", "#112233", (17, 34, 51)),
        ]
        payload = ui_theme.serialize_custom_accent_colors(colors)
        parsed = ui_theme.parse_custom_accent_colors(payload)
        self.assertEqual(parsed, colors)

    def test_load_custom_accent_colors_from_prefs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefs_path = Path(tmp) / "ui.json"
            prefs_path.write_text(
                json.dumps(
                    {
                        "custom_accent_colors": [
                            {"id": "cafebabe", "name": "#ff0088", "rgb": [255, 0, 136]}
                        ]
                    }
                )
            )
            with mock.patch.object(ui_theme, "UI_STATE_FILE", prefs_path):
                loaded = ui_theme.load_custom_accent_colors()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].color_id, "cafebabe")
        self.assertEqual(loaded[0].rgb, (255, 0, 136))

    def test_load_theme_preferences_reads_accent_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefs_path = Path(tmp) / "ui.json"
            prefs_path.write_text(
                json.dumps(
                    {
                        "theme_mode": "oled_black",
                        "accent_rgb": [107, 159, 255],
                        "accent_style": "minimal",
                    }
                )
            )
            with mock.patch.object(ui_theme, "UI_STATE_FILE", prefs_path):
                prefs = ui_theme.load_theme_preferences()
        self.assertEqual(prefs.theme_mode, ui_theme.THEME_MODE_OLED_BLACK)
        self.assertEqual(prefs.accent_rgb, (107, 159, 255))
        self.assertEqual(prefs.accent_style, ui_theme.ACCENT_STYLE_MINIMAL)

    def test_reload_theme_from_prefs_applies_globals(self) -> None:
        original_accent = ui_theme.ACCENT
        original_text = ui_theme.TEXT
        original_muted = ui_theme.MUTED
        try:
            with tempfile.TemporaryDirectory() as tmp:
                prefs_path = Path(tmp) / "ui.json"
                prefs_path.write_text(
                    json.dumps(
                        {
                            "theme_mode": "standard",
                            "accent_rgb": [78, 205, 196],
                            "accent_style": "full",
                        }
                    )
                )
                with mock.patch.object(ui_theme, "UI_STATE_FILE", prefs_path):
                    prefs = ui_theme.reload_theme_from_prefs()
            self.assertEqual(prefs.accent_rgb, (78, 205, 196))
            self.assertEqual(prefs.accent_style, ui_theme.ACCENT_STYLE_MONOCHROME)
            self.assertEqual(ui_theme.ACCENT, (78, 205, 196))
            self.assertEqual(ui_theme.TEXT, (78, 205, 196))
        finally:
            ui_theme.ACCENT = original_accent
            ui_theme.TEXT = original_text
            ui_theme.MUTED = original_muted


if __name__ == "__main__":
    unittest.main()
