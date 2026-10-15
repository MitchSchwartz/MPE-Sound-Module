"""library-writeback.sh: a capture on the laptop becomes one commit in the personal repo."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def git(repo: Path, *args: str) -> str:
    env = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()


class WritebackTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        # A private copy of the scripts so REPO_ROOT/state is a temp dir, not this worktree's.
        self.code = root / "code"
        shutil.copytree(REPO / "scripts", self.code / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(REPO / "config" / "platform", self.code / "config" / "platform")
        cap = self.code / "state" / "dev-2026-10-15"
        (cap / "home").mkdir(parents=True)
        (cap / "home/.patch_browser_fx.json").write_text('{"user:A": {}}')
        (cap / "home/.patch_browser_device_volume.json").write_text('{"usb:1": -17}')
        (cap / "platform.json").write_text("{}")
        (cap / "home/.mpe/looper-songs").mkdir(parents=True)
        (cap / "home/.mpe/looper-songs/song.wav").write_bytes(b"audio")

        self.repo = root / "personal"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.name", "T")
        git(self.repo, "config", "user.email", "t@example.invalid")
        git(self.repo, "config", "commit.gpgsign", "false")
        (self.repo / "assets").mkdir()
        (self.repo / "assets/keep.txt").write_text("x")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "base")
        (self.repo / "scratch.txt").write_text("unrelated, untracked")

    def run_wb(self) -> subprocess.CompletedProcess:
        env = {
            "PATH": os.environ["PATH"],
            "HOME": str(self.code),
            "MPE_ENV_FILE": "",
            "MPE_PERSONAL_REPO": str(self.repo),
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
        }
        return subprocess.run(
            ["bash", str(self.code / "scripts/library-writeback.sh"), "--device-id", "dev1", "--no-capture"],
            capture_output=True,
            text=True,
            env=env,
        )

    def test_one_commit_with_settings_and_state_only(self) -> None:
        proc = self.run_wb()
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        files = git(self.repo, "show", "--name-only", "--pretty=format:", "HEAD").splitlines()
        self.assertIn("settings/patch_browser_fx.json", files)
        self.assertIn("settings/device/dev1/patch_browser_device_volume.json", files)
        self.assertTrue(any(f.startswith("state/appliance-state/captures/") for f in files))
        self.assertFalse(any(f.startswith("assets/") for f in files))
        self.assertNotIn("scratch.txt", files)
        self.assertEqual(git(self.repo, "log", "--oneline").count("\n"), 1)

    def test_looper_audio_stays_out_of_the_commit(self) -> None:
        self.run_wb()
        files = git(self.repo, "ls-files").splitlines()
        self.assertFalse(any("looper-songs" in f for f in files))

    def test_a_run_that_crosses_midnight_still_uses_one_date(self) -> None:
        fake = self.code / "fakebin"
        fake.mkdir()
        counter = self.code / "date-count"
        (fake / "date").write_text(
            "#!/usr/bin/env bash\n"
            'if [ "$1" = "+%Y-%m-%d" ]; then\n'
            f'  n=$(cat {counter} 2>/dev/null || echo 0); echo $((n+1)) > {counter}\n'
            '  printf "2026-10-%02d\\n" $((14+n))\n'
            "else exec /bin/date \"$@\"; fi\n"
        )
        (fake / "date").chmod(0o755)
        env = {
            "PATH": f"{fake}:{os.environ['PATH']}",
            "HOME": str(self.code),
            "MPE_ENV_FILE": "",
            "MPE_PERSONAL_REPO": str(self.repo),
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
        }
        proc = subprocess.run(
            ["bash", str(self.code / "scripts/library-writeback.sh"), "--device-id", "dev1", "--no-capture"],
            capture_output=True, text=True, env=env,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        files = git(self.repo, "ls-files").splitlines()
        days = {f.split("/")[3] for f in files if f.startswith("state/appliance-state/captures/")}
        self.assertEqual(len(days), 1, days)
        self.assertFalse(any("looper" in f for f in files))

    def test_second_run_with_no_change_commits_nothing(self) -> None:
        self.run_wb()
        head = git(self.repo, "rev-parse", "HEAD")
        proc = self.run_wb()
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), head)
        self.assertIn("Nothing changed", proc.stdout)


if __name__ == "__main__":
    unittest.main()
