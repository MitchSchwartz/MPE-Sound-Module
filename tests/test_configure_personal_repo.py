"""configure-pi-paths.sh --paths-only: one key, backup kept, idempotent, upstream wired."""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "configure-pi-paths.sh"


class PathsOnlyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        sudo = self.bin / "sudo"
        sudo.write_text('#!/usr/bin/env bash\nexec "$@"\n')
        sudo.chmod(sudo.stat().st_mode | stat.S_IEXEC)
        self.env_file = self.root / "mpe.env"
        self.env_file.write_text("MPE_PI_USER=u\nMPE_PERSONAL_REPO=/old/place\nMPE_JACK_BUFFER=256\n# keep\n")
        self.lib = self.root / "lib"
        self.lib.mkdir()
        subprocess.run(["git", "-C", str(self.lib), "init", "-q"], check=True)

    def run_cfg(self, *args: str, url: str | None = None) -> subprocess.CompletedProcess:
        env = {
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "HOME": str(self.root),
            "MPE_ENV_FILE": "",
            "MPE_APPLIANCE_ENV_FILE": str(self.env_file),
        }
        if url:
            env["MPE_LIBRARY_UPSTREAM_URL"] = url
        return subprocess.run(
            ["bash", str(SCRIPT), "--local", "--paths-only", "--personal-repo", str(self.lib), *args],
            capture_output=True,
            text=True,
            env=env,
        )

    def remote_url(self) -> str:
        p = subprocess.run(["git", "-C", str(self.lib), "remote", "get-url", "upstream"], capture_output=True, text=True)
        return p.stdout.strip()

    def test_rewrites_only_the_one_key_and_keeps_a_backup(self) -> None:
        proc = self.run_cfg()
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        lines = self.env_file.read_text().splitlines()
        self.assertIn(f"MPE_PERSONAL_REPO={self.lib.resolve()}", lines)
        self.assertNotIn("MPE_PERSONAL_REPO=/old/place", lines)
        self.assertIn("MPE_JACK_BUFFER=256", lines)
        self.assertIn("# keep", lines)
        backups = list(self.root.glob("mpe.env.pre-library.*"))
        self.assertEqual(len(backups), 1)
        self.assertIn("MPE_PERSONAL_REPO=/old/place", backups[0].read_text())

    def test_second_run_changes_nothing_and_makes_no_second_backup(self) -> None:
        self.run_cfg()
        before = self.env_file.read_text()
        proc = self.run_cfg()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("nothing to change", proc.stdout)
        self.assertEqual(self.env_file.read_text(), before)
        self.assertEqual(len(list(self.root.glob("mpe.env.pre-library.*"))), 1)

    def test_upstream_remote_added_then_left_alone(self) -> None:
        self.run_cfg(url="https://example.invalid/template.git")
        self.assertEqual(self.remote_url(), "https://example.invalid/template.git")
        again = self.run_cfg(url="https://example.invalid/template.git")
        self.assertIn("already set", again.stdout)

    def test_no_restart_and_no_unit_install(self) -> None:
        proc = self.run_cfg()
        self.assertNotIn("systemctl", proc.stdout + proc.stderr)
        self.assertIn("not restarted", proc.stdout)

    def test_paths_only_needs_a_repo(self) -> None:
        env = {"PATH": os.environ["PATH"], "HOME": str(self.root), "MPE_ENV_FILE": ""}
        proc = subprocess.run(
            ["bash", str(SCRIPT), "--local", "--paths-only"], capture_output=True, text=True, env=env
        )
        self.assertEqual(proc.returncode, 2)


if __name__ == "__main__":
    unittest.main()
