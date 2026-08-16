"""Verify PatchScanner attaches instrument metadata after scan."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from patch_browser.patch_metadata import PatchMetadataIndex
from patch_browser.patch_scanner import PatchScanner


class PatchScannerMetadataIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "patches_factory"
        self.root.mkdir()
        self.baseline = Path(self._tmp.name) / "baseline.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_scan_enriches_instruments(self) -> None:
        piano = self.root / "Piano"
        piano.mkdir()
        (piano / "Concert D.fxp").write_bytes(b"x")

        scanner = PatchScanner([self.root])
        scanner.metadata_index = PatchMetadataIndex(
            baseline_path=self.baseline,
            user_path=Path(self._tmp.name) / "user.json",
        )
        scanner.scan_patches()

        patch = scanner.get_patches_in_category("Piano")[0]
        self.assertEqual(patch["instrument_primary"], "piano")
        self.assertIn("piano", patch["instruments"])


if __name__ == "__main__":
    unittest.main()
