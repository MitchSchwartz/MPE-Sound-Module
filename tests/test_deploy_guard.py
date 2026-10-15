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
            f'  *"bash -s"*) echo "ssh bash-s" >> {self.log}; payload="$(cat)"\n'
            '     case "$payload" in *"Refuse a tarball patch deploy"*) printf "%s\\n" "$payload" | bash -s; exit $? ;; esac ;;\n'
            # the third-party extraction runs here for real, against the copy scp kept
            f'  *"tar xzf /tmp/patches_3rdparty.tar.gz"*) echo "ssh $*" >> {self.log}\n'
            f'     cmd="${{@: -1}}"; bash -c "${{cmd//\\/tmp\\/patches_3rdparty.tar.gz/{self.root}/sent/patches_3rdparty.tar.gz}}" ;;\n'
            f'  *) echo "ssh $*" >> {self.log} ;;\n'
            'esac\n'
        )
        # scp: logs, and keeps a copy of any tarball it was asked to send
        self.sent = self.root / "sent"
        self.sent.mkdir()
        (bindir / "scp").write_text(
            f'#!/usr/bin/env bash\necho "scp $*" >> {self.log}\n'
            f'for a in "$@"; do case "$a" in /tmp/*.tar.gz) cp "$a" {self.sent}/ ;; esac; done\n'
        )
        for f in bindir.iterdir():
            f.chmod(0o755)
        self.bin = bindir

        self.lib = self.root / "library"
        self.res = self.root / "surge/resources/data"
        self.docs = self.root / "docs"
        (self.lib / "assets/user-data/Patches").mkdir(parents=True)
        (self.lib / "assets/patches/patches_factory").mkdir(parents=True)
        (self.lib / "assets/patches/third-party/patches_3rdparty/Emu").mkdir(parents=True)
        (self.lib / "assets/patches/third-party/patches_3rdparty/Emu/Synths.fxp").write_bytes(b"emu")
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

    def patch_scps(self) -> list[str]:
        return [c for c in self.calls().splitlines() if c.startswith("scp ") and "patches" in c or "user-patches" in c]

    def test_deploy_patches_refuses_when_a_root_is_linked(self) -> None:
        (self.docs / "Patches").symlink_to(self.lib / "assets/user-data/Patches")
        proc = self.run_deploy("deploy-patches.sh")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("link into the personal library", proc.stderr + proc.stdout)
        self.assertNotIn("scp ", self.calls())
        self.assertTrue((self.docs / "Patches").is_symlink())

    def test_deploy_all_on_a_linked_device_skips_patches_but_deploys_code(self) -> None:
        (self.docs / "Patches").symlink_to(self.lib / "assets/user-data/Patches")
        (self.res / "patches_3rdparty").symlink_to(self.lib / "assets/patches/third-party/patches_3rdparty")
        proc = self.run_deploy("deploy-all.sh")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        calls = self.calls()
        self.assertIn("Skipping the patch steps", proc.stdout)
        self.assertEqual(self.patch_scps(), [])
        self.assertNotIn("tar xzf", calls)
        # code and unit deploy still ran
        self.assertIn("git pull", calls)
        self.assertIn("configure-pi-paths.sh --local --force", calls)
        self.assertTrue((self.docs / "Patches").is_symlink())
        self.assertTrue((self.res / "patches_3rdparty").is_symlink())

    def test_deploy_all_on_an_unlinked_device_still_sends_patches(self) -> None:
        (self.docs / "Patches").mkdir()
        proc = self.run_deploy("deploy-all.sh")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        calls = self.calls()
        self.assertNotIn("Skipping the patch steps", proc.stdout)
        self.assertIn("patches_factory.tar.gz", calls)
        self.assertIn("patches_3rdparty.tar.gz", calls)
        self.assertIn("user-patches.tar.gz", calls)
        self.assertIn("configure-pi-paths.sh --local --force", calls)
        # extraction goes through the configured resources variable, not a hardcoded path
        self.assertIn("MPE_SURGE_RESOURCES", calls)
        self.assertNotIn("cd ~/surge/resources/data && tar", calls)

    def test_deploy_all_puts_third_party_authors_one_level_deep(self) -> None:
        (self.docs / "Patches").mkdir()
        proc = self.run_deploy("deploy-all.sh")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual((self.res / "patches_3rdparty/Emu/Synths.fxp").read_bytes(), b"emu")
        self.assertFalse((self.res / "patches_3rdparty/patches_3rdparty").exists())
        self.assertFalse((self.res / "third-party").exists())

    @unittest.skipIf(os.geteuid() == 0, "root ignores directory permissions")
    def test_a_failed_third_party_extract_fails_the_deploy(self) -> None:
        (self.docs / "Patches").mkdir()
        os.chmod(self.res, 0o555)  # nothing can be extracted into it
        self.addCleanup(os.chmod, self.res, 0o755)
        proc = self.run_deploy("deploy-all.sh")
        self.assertNotEqual(proc.returncode, 0, proc.stdout)
        self.assertNotIn("Third-party patches deployed", proc.stdout)

    def test_deploy_all_leaves_a_nested_device_alone(self) -> None:
        (self.docs / "Patches").mkdir()
        nested = self.res / "patches_3rdparty/patches_3rdparty/Emu"
        nested.mkdir(parents=True)
        (nested / "Synths.fxp").write_bytes(b"old")
        proc = self.run_deploy("deploy-all.sh")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("nested third-party layout", proc.stdout)
        self.assertEqual((nested / "Synths.fxp").read_bytes(), b"old")
        self.assertFalse((self.res / "patches_3rdparty/Emu").exists())

    def test_guard_fails_closed_when_the_roots_are_unknown(self) -> None:
        guard = REPO / "scripts/lib/refuse-linked-roots.sh"
        for env in ({}, {"MPE_SURGE_RESOURCES": "/x"}, {"MPE_SURGE_DOCS": "/x"}, {"MPE_SURGE_RESOURCES": "", "MPE_SURGE_DOCS": "/x"}):
            proc = subprocess.run(["bash", str(guard)], capture_output=True, text=True, env={"PATH": os.environ["PATH"], **env})
            self.assertEqual(proc.returncode, 2, env)

    def test_an_unloadable_paths_file_on_the_device_blocks_patch_deploys(self) -> None:
        (self.docs / "Patches").mkdir()
        for script, expect_rc_zero in (("deploy-patches.sh", False), ("deploy-all.sh", True)):
            self.log.write_text("")
            env = {
                "PATH": f"{self.bin}:{os.environ['PATH']}", "HOME": str(self.root), "MPE_ENV_FILE": "",
                "PI_USER": "someone", "PI_HOST": "device.invalid", "SSH_KEY": "/dev/null",
                "PI_MPE_MODULE": str(self.root / "missing-checkout"),  # the device cannot source paths.sh
                "MPE_PERSONAL_REPO": str(self.lib),
                "MPE_SURGE_RESOURCES": "", "MPE_SURGE_DOCS": "",
            }
            proc = subprocess.run(["bash", str(REPO / "scripts" / script)], capture_output=True, text=True, env=env, cwd=self.root)
            self.assertEqual(self.patch_scps(), [], script)
            if expect_rc_zero:
                self.assertIn("Skipping the patch steps", proc.stdout)
            else:
                self.assertNotEqual(proc.returncode, 0)

    def test_unlinked_roots_pass_the_guard(self) -> None:
        (self.docs / "Patches").mkdir()
        proc = self.run_deploy("deploy-patches.sh")
        self.assertNotIn("link into the personal library", proc.stderr + proc.stdout)
        self.assertIn("scp ", self.calls())


if __name__ == "__main__":
    unittest.main()
