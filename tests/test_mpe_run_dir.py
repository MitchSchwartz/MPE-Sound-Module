"""Tests for patch_browser/mpe_run_dir.py."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from patch_browser.mpe_run_dir import run_dir


class MpeRunDirTests(unittest.TestCase):
    def test_falls_back_when_run_dir_not_writable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            blocked = Path(tmp) / "blocked"
            blocked.mkdir()
            os.chmod(blocked, 0o555)
            fallback_root = Path(tmp) / "fallback"
            with tempfile.TemporaryDirectory(dir=fallback_root.parent) as _:
                pass
            with unittest.mock.patch.dict(
                os.environ,
                {"MPE_RUN_DIR": str(blocked), "TMPDIR": str(Path(tmp) / "fallback")},
                clear=False,
            ):
                resolved = run_dir()
                self.assertEqual(resolved, Path(tmp) / "fallback" / "mpe")


if __name__ == "__main__":
    unittest.main()
