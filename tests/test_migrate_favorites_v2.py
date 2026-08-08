"""Fixture tests for migrate-favorites-v2.py."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


class MigrateFavoritesV2Tests(unittest.TestCase):
    def test_dry_run_reports_moves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            factory = root / "patches_factory"
            patches = root / "Patches"
            qa = patches / "Quick Access"
            factory.mkdir(parents=True)
            patches.mkdir(parents=True)
            (factory / "Bass").mkdir()
            (factory / "Bass" / "Acid.fxp").write_bytes(b"x")
            qa.mkdir()
            (qa / "Acid.fxp").write_bytes(b"x")

            env = {
                **dict(__import__("os").environ),
                "MPE_SURGE_DOCS": str(root),
                "MPE_FAVORITES_INDEX_FILE": str(root / "fav.json"),
                "MPE_FAVORITES_NAME": "Quick Access",
            }
            proc = subprocess.run(
                [
                    sys.executable,
                    str(REPO / "scripts" / "migrate-favorites-v2.py"),
                    "--dry-run",
                ],
                cwd=str(REPO),
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            self.assertIn("Acid.fxp", proc.stdout)
            self.assertIn("Quick Access", proc.stdout)
            self.assertFalse((qa / "Acid.fxp").exists())

    def test_apply_moves_and_writes_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            factory = root / "patches_factory"
            patches = root / "Patches"
            qa = patches / "Quick Access"
            factory.mkdir(parents=True)
            patches.mkdir(parents=True)
            (factory / "Keys").mkdir()
            (factory / "Keys" / "Rhodes.fxp").write_bytes(b"x")
            qa.mkdir()
            (qa / "Rhodes.fxp").write_bytes(b"x")
            index_path = root / "fav.json"

            env = {
                **dict(__import__("os").environ),
                "MPE_SURGE_DOCS": str(root),
                "MPE_FAVORITES_INDEX_FILE": str(index_path),
                "MPE_FAVORITES_NAME": "Quick Access",
            }
            proc = subprocess.run(
                [
                    sys.executable,
                    str(REPO / "scripts" / "migrate-favorites-v2.py"),
                    "--apply",
                ],
                cwd=str(REPO),
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            self.assertTrue((qa / "Rhodes.fxp").exists())
            self.assertFalse((qa / "Liked" / "Rhodes.fxp").exists())
            self.assertTrue(index_path.exists())
            raw = index_path.read_text()
            self.assertIn("Rhodes", raw)
            self.assertIn("entries", raw)


if __name__ == "__main__":
    unittest.main()
