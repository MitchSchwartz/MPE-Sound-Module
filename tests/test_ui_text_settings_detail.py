"""Tests for stacked settings detail line helpers."""

import unittest
import unittest.mock as mock

from patch_browser.ui_text import (
    normalize_settings_detail,
    settings_detail_height,
    settings_detail_lines,
)


class TestSettingsDetailLines(unittest.TestCase):
    def test_settings_detail_lines_filters_empty(self) -> None:
        self.assertEqual(settings_detail_lines("USB session", "", "48 kHz"), ["USB session", "48 kHz"])

    def test_normalize_splits_legacy_bullet(self) -> None:
        self.assertEqual(
            normalize_settings_detail("Analog · 768 · 48 kHz"),
            ["Analog", "768", "48 kHz"],
        )

    def test_settings_detail_height_scales_with_line_count(self) -> None:
        font = mock.Mock()
        font.get_linesize.return_value = 20
        one = settings_detail_height(font, ["A"])
        two = settings_detail_height(font, ["A", "B"])
        self.assertGreater(two, one)


if __name__ == "__main__":
    unittest.main()
