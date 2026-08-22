"""Tests for scripts/parse-fxp-metadata.py."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "parse-fxp-metadata.py"
QS_ROOT = REPO_ROOT.parent / "MPE-Library/assets/user-data/quick-select/latest/Quick Select"


def _load_parser():
    spec = importlib.util.spec_from_file_location("parse_fxp_metadata", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class ParseFxpMetadataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parser = _load_parser()

    def test_truncates_binary_tail_after_patch_close(self):
        xml = b'<?xml version="1.0"?><patch><parameters><a_osc1_type value="0"/></parameters></patch>\x00binary'
        trimmed = self.parser._extract_xml_blob(b"prefix" + xml)
        self.assertTrue(trimmed.endswith(b"</patch>"))
        self.assertNotIn(b"binary", trimmed)

    def test_classic_type_zero_counts_as_oscillator(self):
        xml = (
            b'<?xml version="1.0"?><patch><parameters>'
            b'<a_osc1_type value="0"/><a_mute_o1 value="0"/>'
            b'<a_osc2_type value="0"/><a_mute_o2 value="0"/>'
            b'<a_osc3_type value="0"/><a_mute_o3 value="1"/>'
            b'<a_filter1_type value="14"/><a_filter2_type value="10"/>'
            b"</parameters></patch>"
        )
        by_name = self.parser._params_from_regex(xml)
        meta = self.parser._metadata_from_param_map(by_name, Path("Analog.fxp"), "Analog")
        self.assertEqual(meta["osc_types"], [0, 0])
        self.assertEqual(meta["osc_count"], 2)
        self.assertEqual(meta["filter1_type"], 14)

    def test_crystals_unison_and_engines(self):
        path = QS_ROOT / "Crystals.fxp"
        if not path.is_file():
            self.skipTest("MPE-Library Quick Select snapshot not on disk")
        meta = self.parser.parse_fxp_metadata(path)
        self.assertEqual(meta["osc_types"], [10, 10, 10])
        self.assertEqual(meta["unison_per_osc"], [1, 1, 1])
        self.assertEqual(meta["osc_engines"], [4, 4, 6])

    def test_cloud_horn_not_sixteen_unison_voices(self):
        """Mid-tier at clean 5 — would not be if param0 were unison voice count."""
        path = QS_ROOT / "Cloud Horn.fxp"
        if not path.is_file():
            self.skipTest("MPE-Library Quick Select snapshot not on disk")
        meta = self.parser.parse_fxp_metadata(path)
        self.assertEqual(meta["osc_types"], [9, 9])
        self.assertEqual(meta["unison_per_osc"], [1, 1])
        self.assertEqual(meta["osc_engines"], [8, 8])

    def test_brave_new_world_unison_per_osc_not_summed(self):
        path = QS_ROOT / "Brave New World.fxp"
        if not path.is_file():
            self.skipTest("MPE-Library Quick Select snapshot not on disk")
        meta = self.parser.parse_fxp_metadata(path)
        self.assertEqual(meta["osc_types"], [2, 2, 2])
        self.assertEqual(meta["unison_per_osc"], [1, 1, 1])
        self.assertNotIn("osc_engines", meta)

    def test_duduk_wavetable_unison_from_param6(self):
        path = QS_ROOT / "Duduk.fxp"
        if not path.is_file():
            self.skipTest("MPE-Library Quick Select snapshot not on disk")
        meta = self.parser.parse_fxp_metadata(path)
        self.assertNotIn("error", meta)
        self.assertEqual(meta["osc_types"], [2])
        self.assertEqual(meta["unison_per_osc"], [1])

    def test_modern_uses_param6_not_param0_for_unison(self):
        path = QS_ROOT / "Axel's Brassy Pad.fxp"
        if not path.is_file():
            self.skipTest("MPE-Library Quick Select snapshot not on disk")
        meta = self.parser.parse_fxp_metadata(path)
        self.assertEqual(meta["osc_types"], [8, 8])
        self.assertEqual(meta["unison_per_osc"], [4, 5])


if __name__ == "__main__":
    unittest.main()
