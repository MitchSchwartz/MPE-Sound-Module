"""Tests for Surge playback policy helpers."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from patch_browser.surge_playback import (
    ONE_VOICE_PER_KEY,
    clamp_poly_limit,
    effective_poly_after_load,
    ensure_reuse_single_patch,
    parse_polylimit_query,
    patch_xml_reuse_single,
)


class SurgePlaybackTests(unittest.TestCase):
    def test_patch_xml_reuse_single_sets_both_scenes(self) -> None:
        xml = """<?xml version="1.0"?>
<patch revision="22" name="Test">
  <nonparamconfig>
    <polyVoiceRepeatedKeyMode_0 v="0"/>
  </nonparamconfig>
</patch>"""
        out = patch_xml_reuse_single(xml)
        self.assertIn(f'polyVoiceRepeatedKeyMode_0 v="{ONE_VOICE_PER_KEY}"', out)
        self.assertIn(f'polyVoiceRepeatedKeyMode_1 v="{ONE_VOICE_PER_KEY}"', out)

    def test_ensure_reuse_single_patch_caches_xml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "Lead.fxp"
            source.write_text(
                """<?xml version="1.0"?><patch revision="1" name="Lead"></patch>""",
                encoding="utf-8",
            )
            with mock.patch.dict("os.environ", {"MPE_REUSE_SINGLE": "1"}, clear=False):
                first = ensure_reuse_single_patch(source)
                second = ensure_reuse_single_patch(source)
            self.assertEqual(first, second)
            self.assertNotEqual(first, source)
            self.assertIn("polyVoiceRepeatedKeyMode_0", first.read_text(encoding="utf-8"))

    def test_effective_poly_after_load_caps_ceiling(self) -> None:
        with mock.patch("patch_browser.surge_playback.poly_ceiling", return_value=12):
            self.assertEqual(effective_poly_after_load(32), 12)
            self.assertEqual(effective_poly_after_load(8), 8)

    def test_clamp_poly_limit(self) -> None:
        self.assertEqual(clamp_poly_limit(1), 2)
        self.assertEqual(clamp_poly_limit(80), 64)

    def test_parse_polylimit_query_accepts_digit_string(self) -> None:
        # Minimal OSC-like payload with display string "16"
        data = b"/q/param/global/polyphony_limit\x00,\x00s\x0016\x00"
        self.assertEqual(parse_polylimit_query(data), 16)


if __name__ == "__main__":
    unittest.main()
