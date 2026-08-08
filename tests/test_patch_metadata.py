"""Tests for patch metadata classification and index merge."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from patch_browser.patch_identity import make_patch_entry
from patch_browser.patch_metadata import (
    PatchMetadataIndex,
    build_baseline_document,
    classify_patch_instruments,
    load_metadata_file,
    metadata_entry_for_patch,
    write_metadata_file,
)


class PatchMetadataClassifierTests(unittest.TestCase):
    def _patch(self, **kwargs) -> dict:
        base = {
            "name": "Test",
            "path": "/tmp/test.fxp",
            "category": "Bass",
            "folder_segments": ("Bass",),
            "inner_segments": (),
            "stable_key": "factory:Bass/Test",
        }
        base.update(kwargs)
        return base

    def test_folder_bass_classifies_bass(self) -> None:
        tags = classify_patch_instruments(self._patch(name="Mystery", category="Bass"))
        self.assertEqual(tags[0], "bass")

    def test_nested_subfolder_adds_bass(self) -> None:
        tags = classify_patch_instruments(
            self._patch(
                name="Deep",
                category="Bass",
                folder_segments=("Bass", "Sub"),
                inner_segments=("Sub",),
            )
        )
        self.assertIn("bass", tags)

    def test_name_piano_overrides_opaque_folder(self) -> None:
        tags = classify_patch_instruments(
            self._patch(name="Grand Piano D", category="Templates", folder_segments=("Templates",))
        )
        self.assertEqual(tags[0], "piano")

    def test_no_signal_returns_other(self) -> None:
        tags = classify_patch_instruments(
            self._patch(name="X17", category="Uncategorized", folder_segments=())
        )
        self.assertEqual(tags, ["other"])

    def test_kick_name_classifies_percussion(self) -> None:
        tags = classify_patch_instruments(self._patch(name="808 Kick", category="Templates"))
        self.assertEqual(tags[0], "percussion")

    def test_sequencer_folder(self) -> None:
        tags = classify_patch_instruments(
            self._patch(
                name="Run",
                category="Templates",
                folder_segments=("Templates", "Sequencer"),
                inner_segments=("Sequencer",),
            )
        )
        self.assertEqual(tags[0], "sequencer")

    def test_church_organ_name(self) -> None:
        tags = classify_patch_instruments(self._patch(name="Church Organ", category="Templates"))
        self.assertEqual(tags[0], "organ")
        tags = classify_patch_instruments(
            self._patch(
                name="Lush Cloud",
                category="Templates",
                folder_segments=("Templates", "Ambient"),
                inner_segments=("Ambient",),
            )
        )
        self.assertIn("pad", tags)

    def test_metadata_entry_shape(self) -> None:
        entry = metadata_entry_for_patch(self._patch(name="Acid", category="Bass"))
        self.assertEqual(entry["path_segments"], ["Bass"])
        self.assertIn("bass", entry["instruments"])
        self.assertIsNone(entry["instrument_user"])


class PatchMetadataIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.baseline_path = self.tmp / "baseline.json"
        self.user_path = self.tmp / "user.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_baseline_enriches_patch(self) -> None:
        write_metadata_file(
            self.baseline_path,
            {
                "version": 1,
                "patches": {
                    "factory:Bass/Sub/Lead 1": {
                        "instruments": ["bass"],
                        "instrument_user": None,
                    }
                },
            },
        )
        index = PatchMetadataIndex(
            baseline_path=self.baseline_path,
            user_path=self.user_path,
        )
        patch = {
            "stable_key": "factory:Bass/Sub/Lead 1",
            "name": "Lead 1",
            "category": "Bass",
            "inner_segments": ("Sub",),
        }
        index.enrich_patch(patch)
        self.assertEqual(patch["instruments"], ["bass"])
        self.assertEqual(patch["instrument_primary"], "bass")

    def test_user_override_wins(self) -> None:
        write_metadata_file(
            self.baseline_path,
            {
                "version": 1,
                "patches": {
                    "factory:Templates/X": {"instruments": ["synth"], "instrument_user": None}
                },
            },
        )
        index = PatchMetadataIndex(
            baseline_path=self.baseline_path,
            user_path=self.user_path,
        )
        index.set_user_instrument("factory:Templates/X", "pad")
        patch = {"stable_key": "factory:Templates/X", "name": "X", "category": "Templates"}
        index.enrich_patch(patch)
        self.assertEqual(patch["instruments"], ["pad"])

    def test_build_baseline_from_scanner_entries(self) -> None:
        root = self.tmp / "factory"
        root.mkdir()
        path = root / "Piano" / "Grand.fxp"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"x")
        entry = make_patch_entry(
            name="Grand",
            path=path,
            patch_dir=root,
            root_label="factory",
        )
        doc = build_baseline_document({entry["stable_key"]: entry})
        self.assertEqual(doc["version"], 1)
        row = doc["patches"][entry["stable_key"]]
        self.assertEqual(row["instruments"][0], "piano")

    def test_load_metadata_file_wrong_version(self) -> None:
        write_metadata_file(self.baseline_path, {"version": 99, "patches": {"a": {}}})
        self.assertEqual(load_metadata_file(self.baseline_path), {})


if __name__ == "__main__":
    unittest.main()
