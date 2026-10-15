"""Tarball deploys must refuse to run over patch roots that are links into the library."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


class DeployGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.log = self.root / "calls.log"
        bindir = self.root / "bin"
        bindir.mkdir()
        # ssh: runs `bash -s` payloads locally (this is where the guard executes), logs the rest.
        (bindir / "ssh").write_text(
            '#!/usr/bin/env bash\n'
            'case "$*" in\n'
            f'  *"bash -s"*) echo "ssh bash-s" >> {self.log}; exec bash -s ;;\n'
            f'  *) echo "ssh $*" >> {self.log} ;;\n'
            'esac\n'
        )
        (bindir / "scp").write_text(f'#!/usr/bin/env bash\necho "scp $*" >> {self.log}\n')
        for f in bindir.iterdir():
            f.chmod(0o755)
        self.bin = bindir

        self.lib = self.root / "library"
        self.res = self.root / "surge/resources/data"
        self.docs = self.root / "docs"
        (self.lib / "assets/user-data/Patches").mkdir(parents=True)
        (self.lib / "assets/patches/patches_factory").mkdir(parents=True)
        (self.lib / "assets/patches/third-party").mkdir(parents=True)
        self.res.mkdir(parents=True)
        self.docs.mkdir()

    def run_deploy(self, script: str) -> subprocess.CompletedProcess:
        env = {
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "HOME": str(self.root),
            "MPE_ENV_FILE": "",
            "PI_USER": "someone",
            "PI_HOST": "device.invalid",
            "SSH_KEY": "/dev/null",
            "PI_MPE_MODULE": str(REPO),
            "MPE_PERSONAL_REPO": str(self.lib),
            "MPE_SURGE_RESOURCES": str(self.res),
            "MPE_SURGE_DOCS": str(self.docs),
        }
        return subprocess.run(["bash", str(REPO / "scripts" / script)], capture_output=True, text=True, env=env, cwd=self.root)

    def calls(self) -> str:
        return self.log.read_text() if self.log.exists() else ""

    def test_both_deploy_scripts_refuse_when_a_root_is_linked(self) -> None:
        (self.docs / "Patches").symlink_to(self.lib / "assets/user-data/Patches")
        (self.res / "patches_factory").mkdir()
        for script in ("deploy-patches.sh", "deploy-all.sh"):
            self.log.write_text("")
            proc = self.run_deploy(script)
            self.assertNotEqual(proc.returncode, 0, script)
            self.assertIn("link into the personal library", proc.stderr + proc.stdout, script)
            self.assertNotIn("scp ", self.calls(), script)
            self.assertTrue((self.docs / "Patches").is_symlink(), script)

    def test_a_linked_third_party_root_blocks_deploy_all(self) -> None:
        (self.res / "patches_3rdparty").symlink_to(self.lib / "assets/patches/third-party")
        proc = self.run_deploy("deploy-all.sh")
        self.assertNotEqual(proc.returncode, 0)
        self.assertNotIn("scp ", self.calls())

    def test_unlinked_roots_pass_the_guard(self) -> None:
        (self.docs / "Patches").mkdir()
        proc = self.run_deploy("deploy-patches.sh")
        self.assertNotIn("link into the personal library", proc.stderr + proc.stdout)
        self.assertIn("scp ", self.calls())


if __name__ == "__main__":
    unittest.main()
