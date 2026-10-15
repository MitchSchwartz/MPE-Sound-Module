"""Laptop-mode capture must leave the appliance alone.

capture-external-state.sh used to sync the device's checkout before every
capture (git stash, checkout, reset --hard) and park scratch files in its /tmp.
A backup that rewrites the thing it backs up is not a backup. These tests run the
laptop path against a stand-in `ssh` that records every remote command.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "provision" / "capture-external-state.sh"

# Anything that changes the device. A read-only capture sends none of these.
MUTATING = re.compile(
    r"git (stash|checkout|reset|fetch|pull|clean|merge|rebase|commit|push)"
    r"|\brm\b|\bmkdir\b|\bmv\b|\bcp\b|\btee\b|\bscp\b|\bsudo\b|systemctl|>>?\s*[~/\w]"
    r"|/tmp/"
)

FAKE_SSH = """#!/usr/bin/env bash
echo "$*" >> "$FAKE_SSH_LOG"
case "$*" in
  *"CAPTURE_SCHEMA_VERSION="*) echo "CAPTURE_SCHEMA_VERSION=${FAKE_SCHEMA:-2}" ;;
  *"grep -c -e"*) echo "${FAKE_STREAMS:-1}" ;;
  *"rev-parse HEAD"*) echo deadbeef ;;
  *"--local --stdout"*) cat "$FAKE_CAPTURE_TAR" ;;
  *) echo ok ;;
esac
"""


class CaptureReadOnlyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        bindir = self.root / "bin"
        bindir.mkdir()
        ssh = bindir / "ssh"
        ssh.write_text(FAKE_SSH)
        ssh.chmod(ssh.stat().st_mode | stat.S_IEXEC)
        self.log = self.root / "ssh.log"
        self.log.write_text("")

        src = self.root / "cap-src"
        (src / "boot").mkdir(parents=True)
        (src / "home").mkdir()
        (src / "platform.json").write_text("{}")
        (src / "boot/dsi-config.snippet").write_text("# none\n")
        (src / "MANIFEST.md").write_text("# m\n\n## Platform / kernel\n\nx\n")
        (src / "home/.patch_browser_fx.json").write_text('{"user:A": {}}')
        self.tar = self.root / "cap.tar"
        with tarfile.open(self.tar, "w") as tf:
            tf.add(src, arcname=".")
        self.out = self.root / "out"
        self.bindir = bindir

    def run_capture(self, *args: str, schema: str = "2", streams: str = "1"):
        env = {
            "PATH": f"{self.bindir}:{os.environ['PATH']}",
            "HOME": str(self.root),
            "MPE_ENV_FILE": "",
            "PI_USER": "someone",
            "PI_HOST": "device.invalid",
            "SSH_KEY": "/dev/null",
            "FAKE_SSH_LOG": str(self.log),
            "FAKE_CAPTURE_TAR": str(self.tar),
            "FAKE_SCHEMA": schema,
            "FAKE_STREAMS": streams,
        }
        return subprocess.run(
            ["bash", str(SCRIPT), *args, str(self.out)], capture_output=True, text=True, env=env, cwd=self.root
        )

    def remote_commands(self) -> list[str]:
        return [line for line in self.log.read_text().splitlines() if line.strip()]

    def test_capture_sends_only_read_commands_and_lands_the_files(self) -> None:
        proc = self.run_capture()
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertEqual((self.out / "home/.patch_browser_fx.json").read_text(), '{"user:A": {}}')
        cmds = self.remote_commands()
        self.assertTrue(any("--local --stdout" in c for c in cmds))
        for c in cmds:
            self.assertIsNone(MUTATING.search(c), f"mutating remote command: {c}")

    def test_check_mode_is_read_only_too(self) -> None:
        self.run_capture()
        self.log.write_text("")
        proc = self.run_capture("--check")
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        for c in self.remote_commands():
            self.assertIsNone(MUTATING.search(c), f"mutating remote command: {c}")

    def test_old_device_checkout_is_refused_not_updated(self) -> None:
        for kwargs in ({"schema": "1"}, {"streams": "0"}):
            self.log.write_text("")
            proc = self.run_capture(**kwargs)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("does not touch the device's checkout", proc.stderr)
            for c in self.remote_commands():
                self.assertIsNone(MUTATING.search(c), f"mutating remote command: {c}")
            self.assertFalse(self.out.exists())

    def test_failed_transfer_does_not_wipe_an_existing_capture(self) -> None:
        self.run_capture()
        self.tar.write_bytes(b"not a tar")
        proc = self.run_capture()
        self.assertNotEqual(proc.returncode, 0)
        self.assertTrue((self.out / "home/.patch_browser_fx.json").exists())

    def test_script_text_has_no_device_side_git_mutation(self) -> None:
        text = SCRIPT.read_text()
        laptop_half = text[text.index("# Read-only: looks at the device's checkout"):]
        for banned in ("git stash", "git checkout", "reset --hard", "git fetch", "scp ", "rm -rf '$remote"):
            self.assertNotIn(banned, laptop_half)


if __name__ == "__main__":
    unittest.main()
