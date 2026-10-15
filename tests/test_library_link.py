"""setup-pi-symlinks.sh --local against a temp tree: checks, idempotence, rollback."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "setup-pi-symlinks.sh"


class LinkTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = self.root / "library"
        self.res = self.root / "surge" / "resources" / "data"
        self.docs = self.root / "docs"
        for rel in (
            "assets/patches/patches_factory/Bass",
            "assets/patches/third-party/patches_3rdparty/Emu",
            "assets/user-data/Patches",
        ):
            (self.repo / rel).mkdir(parents=True)
        self.put(self.repo / "assets/patches/patches_factory/Bass/Sub.fxp", b"sub")
        self.put(self.repo / "assets/patches/third-party/patches_3rdparty/Emu/Synths.fxp", b"emu")
        self.put(self.repo / "assets/user-data/Patches/Mine.fxp", b"mine")
        # the live directories, as on the device: real dirs, same content
        self.put(self.res / "patches_factory/Bass/Sub.fxp", b"sub")
        self.put(self.res / "patches_3rdparty/Emu/Synths.fxp", b"emu")
        self.put(self.docs / "Patches/Mine.fxp", b"mine")

    @staticmethod
    def put(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def run_script(self, *args: str) -> subprocess.CompletedProcess:
        env = {
            "PATH": os.environ["PATH"],
            "HOME": str(self.root),
            "MPE_ENV_FILE": "",
            "MPE_SURGE_RESOURCES": str(self.res),
            "MPE_SURGE_DOCS": str(self.docs),
            "MPE_PERSONAL_REPO": str(self.repo),
        }
        return subprocess.run(
            ["bash", str(SCRIPT), "--local", *args], capture_output=True, text=True, env=env
        )

    def links(self) -> dict[str, bool]:
        return {
            "factory": (self.res / "patches_factory").is_symlink(),
            "third": (self.res / "patches_3rdparty").is_symlink(),
            "user": (self.docs / "Patches").is_symlink(),
        }

    def test_links_keep_their_names_and_content_resolves(self) -> None:
        proc = self.run_script()
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertEqual(self.links(), {"factory": True, "third": True, "user": True})
        self.assertEqual((self.res / "patches_factory/Bass/Sub.fxp").read_bytes(), b"sub")
        self.assertEqual((self.docs / "Patches/Mine.fxp").read_bytes(), b"mine")
        backups = sorted(p.name for p in self.res.glob("patches_factory.backup.*"))
        self.assertEqual(len(backups), 1)

    def test_dry_run_changes_nothing(self) -> None:
        proc = self.run_script("--dry-run")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("would link", proc.stdout)
        self.assertEqual(self.links(), {"factory": False, "third": False, "user": False})

    def test_refuses_when_live_dir_has_a_file_the_library_lacks(self) -> None:
        self.put(self.docs / "Patches/OnlyOnDevice.fxp", b"new")
        proc = self.run_script()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("OnlyOnDevice", proc.stdout)
        # all or nothing: the roots that were fine were not linked either
        self.assertEqual(self.links(), {"factory": False, "third": False, "user": False})

    def test_refuses_when_a_file_differs(self) -> None:
        self.put(self.docs / "Patches/Mine.fxp", b"edited on device")
        proc = self.run_script()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("differ", proc.stdout)
        self.assertEqual(self.links(), {"factory": False, "third": False, "user": False})

    def test_library_may_hold_more_than_the_live_dir(self) -> None:
        self.put(self.repo / "assets/user-data/Patches/Extra.fxp", b"x")
        proc = self.run_script()
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)

    def test_refuses_when_target_tree_is_missing(self) -> None:
        (self.repo / "assets/user-data/Patches/Mine.fxp").unlink()
        (self.repo / "assets/user-data/Patches").rmdir()
        proc = self.run_script()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("missing target", proc.stdout)

    def test_second_run_is_a_no_op(self) -> None:
        self.run_script()
        before = sorted(p.name for p in self.res.iterdir())
        proc = self.run_script()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(sorted(p.name for p in self.res.iterdir()), before)
        self.assertIn("already points", proc.stdout)

    def test_rollback_puts_the_real_directories_back(self) -> None:
        self.run_script()
        proc = self.run_script("--rollback")
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertEqual(self.links(), {"factory": False, "third": False, "user": False})
        self.assertEqual((self.res / "patches_factory/Bass/Sub.fxp").read_bytes(), b"sub")
        self.assertEqual((self.docs / "Patches/Mine.fxp").read_bytes(), b"mine")
        # and again: nothing left to do, no error
        again = self.run_script("--rollback")
        self.assertEqual(again.returncode, 0)

    def test_no_restart_unless_asked(self) -> None:
        proc = self.run_script()
        self.assertNotIn("systemctl", proc.stdout + proc.stderr)
        self.assertIn("Restart", proc.stdout)


if __name__ == "__main__":
    unittest.main()
