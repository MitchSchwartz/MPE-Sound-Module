"""library-migrate-device.sh against a temp "device": every step idempotent, reversible, silent."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "library-migrate-device.sh"


class MigrateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.home = self.root / "home"
        self.res = self.home / "surge/resources/data"
        self.docs = self.home / "docs"
        self.new = self.root / "new-library"
        self.old = self.home / "old-library"
        bindir = self.root / "bin"
        bindir.mkdir()
        sudo = bindir / "sudo"
        sudo.write_text('#!/usr/bin/env bash\nexec "$@"\n')
        sudo.chmod(sudo.stat().st_mode | stat.S_IEXEC)
        # a log of anything that tries to restart a unit
        systemctl = bindir / "systemctl"
        systemctl.write_text(f'#!/usr/bin/env bash\necho "$@" >> {self.root}/systemctl.log\n')
        systemctl.chmod(systemctl.stat().st_mode | stat.S_IEXEC)
        self.bin = bindir

        self.old.mkdir(parents=True)
        (self.old / "stale.txt").write_text("old copy")
        for rel, data in {
            "assets/patches/patches_factory/Bass/Sub.fxp": b"sub",
            "assets/patches/third-party/patches_3rdparty/Emu/Synths.fxp": b"emu",
            "assets/user-data/Patches/Mine.fxp": b"mine",
        }.items():
            self.put(self.new / rel, data)
        for live, data in {
            self.res / "patches_factory/Bass/Sub.fxp": b"sub",
            self.res / "patches_3rdparty/patches_3rdparty/Emu/Synths.fxp": b"emu",
            self.docs / "Patches/Mine.fxp": b"mine",
        }.items():
            self.put(live, data)
        (self.home / ".patch_browser_fx.json").write_text(
            json.dumps({"factory:Bass/Sub": {"mask": 1}, "user:Mine": {"mask": 2},
                        "thirdparty:patches_3rdparty/Emu/Synths": {"mask": 4}})
        )
        self.env_file = self.root / "mpe.env"
        self.env_file.write_text(f"MPE_PERSONAL_REPO={self.old}\nMPE_UI_MODE=touch\n")

    @staticmethod
    def put(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def run_step(self, *args: str) -> subprocess.CompletedProcess:
        env = {
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "HOME": str(self.home),
            "MPE_HOME": str(self.home),
            "MPE_ENV_FILE": "",
            "MPE_PERSONAL_REPO": str(self.old),
            "MPE_SURGE_RESOURCES": str(self.res),
            "MPE_SURGE_DOCS": str(self.docs),
            "MPE_APPLIANCE_ENV_FILE": str(self.env_file),
        }
        return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, env=env)

    def is_linked(self) -> bool:
        return all(p.is_symlink() for p in (self.res / "patches_factory", self.res / "patches_3rdparty", self.docs / "Patches"))

    def test_link_needs_a_snapshot_first(self) -> None:
        proc = self.run_step("link", "--dir", str(self.new))
        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse(self.is_linked())

    def test_full_sequence_then_rollback_and_reruns(self) -> None:
        self.assertEqual(self.run_step("snapshot").returncode, 0)
        self.assertEqual(self.run_step("snapshot").returncode, 0)  # idempotent: keeps the first
        snap = json.loads((self.home / ".mpe-library-migration/before.json").read_text())
        # all three fx keys land, the nested third-party one included; verify below
        # fails if linking moved any of them
        self.assertEqual(snap["store_hits"]["fx"]["stable_hits"], 3)

        dry = self.run_step("link", "--dir", str(self.new), "--dry-run")
        self.assertEqual(dry.returncode, 0, dry.stdout + dry.stderr)
        self.assertFalse(self.is_linked())

        for _ in range(2):  # a re-run is harmless
            proc = self.run_step("link", "--dir", str(self.new))
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(self.is_linked())
        self.assertIn(f"MPE_PERSONAL_REPO={self.new.resolve()}", self.env_file.read_text())
        self.assertIn("MPE_UI_MODE=touch", self.env_file.read_text())
        # same patches resolve through the links, and the settings still land
        self.assertEqual((self.docs / "Patches/Mine.fxp").read_bytes(), b"mine")
        self.assertEqual(self.run_step("verify").returncode, 0)

        # never restarts anything
        self.assertFalse((self.root / "systemctl.log").exists())

        back = self.run_step("rollback")
        self.assertEqual(back.returncode, 0, back.stdout + back.stderr)
        self.assertFalse(self.is_linked())
        self.assertEqual((self.res / "patches_factory/Bass/Sub.fxp").read_bytes(), b"sub")
        self.assertIn(f"MPE_PERSONAL_REPO={self.old}", self.env_file.read_text())
        self.assertIn("MPE_UI_MODE=touch", self.env_file.read_text())
        self.assertEqual(self.run_step("rollback").returncode, 0)

    def test_link_refuses_and_changes_nothing_when_device_has_extra_patch(self) -> None:
        self.run_step("snapshot")
        self.put(self.docs / "Patches/OnlyHere.fxp", b"x")
        proc = self.run_step("link", "--dir", str(self.new))
        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse(self.is_linked())
        self.assertIn(f"MPE_PERSONAL_REPO={self.old}", self.env_file.read_text())

    def test_cleanup_lists_by_default_and_refuses_the_active_library(self) -> None:
        for extra in ([], ["--yes"]):
            proc = self.run_step("cleanup", "--old-copy", str(self.old), *extra)
            self.assertNotEqual(proc.returncode, 0)  # MPE_PERSONAL_REPO still points at it
            self.assertIn("active library", proc.stderr)
            self.assertTrue(self.old.exists())

    def git(self, repo: Path, *args: str) -> str:
        env = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")
        p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout.strip()

    def make_new_library_a_pushed_checkout(self) -> Path:
        origin = self.root / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
        self.git(self.new, "init", "-q", "-b", "main")
        self.git(self.new, "config", "user.name", "T")
        self.git(self.new, "config", "user.email", "t@example.invalid")
        self.git(self.new, "config", "commit.gpgsign", "false")
        self.git(self.new, "add", "-A")
        self.git(self.new, "commit", "-q", "-m", "library")
        self.git(self.new, "remote", "add", "origin", str(origin))
        self.git(self.new, "push", "-q", "origin", "main")
        return origin

    def run_new(self, *args: str) -> subprocess.CompletedProcess:
        env = {
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "HOME": str(self.home),
            "MPE_HOME": str(self.home),
            "MPE_ENV_FILE": "",
            "MPE_PERSONAL_REPO": str(self.new),
            "MPE_SURGE_RESOURCES": str(self.res),
            "MPE_SURGE_DOCS": str(self.docs),
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
        }
        return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, env=env)

    def test_cleanup_deletes_only_with_yes_and_only_when_the_library_is_pushed(self) -> None:
        self.run_step("snapshot")
        self.run_step("link", "--dir", str(self.new))
        # not a git checkout: refuse
        r = self.run_new("cleanup", "--old-copy", str(self.old), "--yes")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not a git checkout", r.stderr)
        self.make_new_library_a_pushed_checkout()
        # uncommitted change under assets/: refuse
        self.put(self.new / "assets/user-data/Patches/New.fxp", b"n")
        r = self.run_new("cleanup", "--old-copy", str(self.old), "--yes")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("uncommitted", r.stderr)
        # committed but not pushed: refuse
        self.git(self.new, "add", "-A")
        self.git(self.new, "commit", "-q", "-m", "more")
        r = self.run_new("cleanup", "--old-copy", str(self.old), "--yes")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not on", r.stderr)
        self.assertTrue(self.old.exists())
        # pushed: listing is harmless, --yes deletes
        self.git(self.new, "push", "-q", "origin", "main")
        listed = self.run_new("cleanup", "--old-copy", str(self.old))
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertTrue(self.old.exists())
        done = self.run_new("cleanup", "--old-copy", str(self.old), "--yes")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertFalse(self.old.exists())
        self.assertFalse(list(self.res.glob("patches_*.backup.*")))
        self.assertTrue(self.is_linked())

    def test_failed_verify_after_linking_rolls_everything_back(self) -> None:
        self.run_step("snapshot")
        before = self.home / ".mpe-library-migration/before.json"
        snap = json.loads(before.read_text())
        snap["patches_per_root"]["patches_factory"] += 5  # pretend 5 more patches existed
        before.write_text(json.dumps(snap))
        proc = self.run_step("link", "--dir", str(self.new))
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("rolling back", proc.stderr)
        self.assertFalse(self.is_linked())
        self.assertEqual((self.docs / "Patches/Mine.fxp").read_bytes(), b"mine")
        self.assertIn(f"MPE_PERSONAL_REPO={self.old}", self.env_file.read_text())
        self.assertIn("MPE_UI_MODE=touch", self.env_file.read_text())

    def test_rollback_stops_at_the_pre_migration_state_not_at_older_backups(self) -> None:
        import shutil

        # The user root starts as a LINK to an older copy, with a stale backup dir beside it.
        older = self.root / "older-copy"
        shutil.copytree(self.docs / "Patches", older)
        shutil.rmtree(self.docs / "Patches")
        (self.docs / "Patches").symlink_to(older)
        stale = self.docs / "Patches.backup.1000"
        self.put(stale / "Ancient.fxp", b"ancient")
        self.run_step("snapshot")
        link = self.run_step("link", "--dir", str(self.new))
        self.assertEqual(link.returncode, 0, link.stdout + link.stderr)
        self.assertEqual((self.docs / "Patches").resolve(), (self.new / "assets/user-data/Patches").resolve())

        back = self.run_step("rollback")
        self.assertEqual(back.returncode, 0, back.stdout + back.stderr)
        # exactly the state before `link`: still a link to the older copy, stale dir untouched
        self.assertTrue((self.docs / "Patches").is_symlink())
        self.assertEqual((self.docs / "Patches").resolve(), older.resolve())
        self.assertEqual((stale / "Ancient.fxp").read_bytes(), b"ancient")
        self.assertFalse((self.res / "patches_factory").is_symlink())
        self.assertIn(f"MPE_PERSONAL_REPO={self.old}", self.env_file.read_text())
        # a second rollback has nothing recorded and changes nothing
        again = self.run_step("rollback")
        self.assertEqual(again.returncode, 0)
        self.assertEqual((self.docs / "Patches").resolve(), older.resolve())

    def test_a_failed_second_link_undoes_only_itself(self) -> None:
        import shutil

        second = self.root / "new-library-2"
        shutil.copytree(self.new, second)
        self.run_step("snapshot")
        self.assertEqual(self.run_step("link", "--dir", str(self.new)).returncode, 0)
        snap = self.home / ".mpe-library-migration/before.json"
        data = json.loads(snap.read_text())
        data["patches_per_root"]["patches_factory"] += 9
        snap.write_text(json.dumps(data))
        bad = self.run_step("link", "--dir", str(second))
        self.assertNotEqual(bad.returncode, 0)
        # still linked into the FIRST library, env still names it
        self.assertEqual((self.docs / "Patches").resolve(), (self.new / "assets/user-data/Patches").resolve())
        self.assertIn(f"MPE_PERSONAL_REPO={self.new.resolve()}", self.env_file.read_text())

    def test_rollback_restores_the_original_state_after_two_links(self) -> None:
        import shutil

        second = self.root / "new-library-2"
        shutil.copytree(self.new, second)
        self.run_step("snapshot")
        self.assertEqual(self.run_step("link", "--dir", str(self.new)).returncode, 0)
        self.assertEqual(self.run_step("link", "--dir", str(second)).returncode, 0)
        self.assertEqual(len(list(self.root.glob("mpe.env.pre-library.*"))), 2)
        back = self.run_step("rollback")
        self.assertEqual(back.returncode, 0, back.stdout + back.stderr)
        # links: real directories again; env: the ORIGINAL value, not the first link's
        self.assertFalse(self.is_linked())
        self.assertEqual((self.res / "patches_factory/Bass/Sub.fxp").read_bytes(), b"sub")
        self.assertIn(f"MPE_PERSONAL_REPO={self.old}", self.env_file.read_text())


if __name__ == "__main__":
    unittest.main()
