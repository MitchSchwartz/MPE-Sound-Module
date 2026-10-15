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
        # a space in the path, as on the device ("Documents/Surge XT")
        self.docs = self.root / "Surge XT"
        for rel in (
            "assets/patches/patches_factory/Bass",
            "assets/patches/third-party/patches_3rdparty/Emu",
            "assets/user-data/Patches",
        ):
            (self.repo / rel).mkdir(parents=True)
        self.put(self.repo / "assets/patches/patches_factory/Bass/Sub.fxp", b"sub")
        self.put(self.repo / "assets/patches/third-party/patches_3rdparty/Emu/Synths.fxp", b"emu")
        self.put(self.repo / "assets/user-data/Patches/Mine.fxp", b"mine")
        # the live directories, as on the device: real dirs, same content. deploy-all
        # renames the library's whole third-party/ dir to patches_3rdparty, so the
        # authors sit one level down on the device.
        self.put(self.res / "patches_factory/Bass/Sub.fxp", b"sub")
        self.put(self.res / "patches_3rdparty/patches_3rdparty/Emu/Synths.fxp", b"emu")
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

    def test_third_party_keys_are_unchanged_through_the_link(self) -> None:
        import sys

        sys.path.insert(0, str(SCRIPT.parents[1]))
        from patch_browser.patch_sidecar_key import clear_path_caches, stable_key_from_absolute_path

        roots = [self.res / "patches_factory", self.res / "patches_3rdparty", self.docs / "Patches"]
        live = self.res / "patches_3rdparty/patches_3rdparty/Emu/Synths.fxp"
        before = stable_key_from_absolute_path(live, roots)
        self.assertEqual(before, "thirdparty:patches_3rdparty/Emu/Synths")
        proc = self.run_script()
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        clear_path_caches()
        self.assertEqual(stable_key_from_absolute_path(live, roots), before)

    def test_refuses_a_missing_file_under_a_path_with_spaces(self) -> None:
        self.put(self.docs / "Patches/Quick Select/Only On Device.fxp", b"new")
        proc = self.run_script("--dry-run")
        self.assertNotEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("Quick Select/Only On Device.fxp", proc.stdout)

    def test_empty_live_directories_do_not_block(self) -> None:
        (self.docs / "Patches/MIDI Programs").mkdir()
        (self.docs / "Patches/Quick Select/Dan Maurer").mkdir(parents=True)
        proc = self.run_script("--dry-run")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        # but a directory holding a file still does
        self.put(self.docs / "Patches/MIDI Programs/prog.fxp", b"p")
        proc = self.run_script("--dry-run")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("MIDI Programs/prog.fxp", proc.stdout)

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

    def make_second_library(self) -> Path:
        import shutil

        other = self.root / "library2"
        shutil.copytree(self.repo, other)
        return other

    def test_link_pointing_somewhere_else_is_compared_too(self) -> None:
        other = self.make_second_library()
        self.put(other / "assets/user-data/Patches/OnlyInOther.fxp", b"x")
        self.run_script()  # live dirs -> linked into self.repo
        self.put(self.repo / "assets/user-data/Patches/OnlyInFirst.fxp", b"y")
        proc = subprocess.run(
            ["bash", str(SCRIPT), "--local", "--personal-repo", str(other)],
            capture_output=True, text=True,
            env={"PATH": os.environ["PATH"], "HOME": str(self.root), "MPE_ENV_FILE": "",
                 "MPE_SURGE_RESOURCES": str(self.res), "MPE_SURGE_DOCS": str(self.docs)},
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("OnlyInFirst", proc.stdout)
        self.assertEqual((self.docs / "Patches").resolve(), (self.repo / "assets/user-data/Patches").resolve())

    def test_relinking_in_the_same_second_never_nests_a_backup(self) -> None:
        other = self.make_second_library()
        self.run_script()
        env = {"PATH": os.environ["PATH"], "HOME": str(self.root), "MPE_ENV_FILE": "",
               "MPE_SURGE_RESOURCES": str(self.res), "MPE_SURGE_DOCS": str(self.docs)}
        # a fake clock: every `date +%s` answers the same second
        fake = self.root / "fakebin"
        fake.mkdir()
        (fake / "date").write_text('#!/usr/bin/env bash\nif [ "$1" = "+%s" ]; then echo 1700000000; else exec /bin/date "$@"; fi\n')
        (fake / "date").chmod(0o755)
        env["PATH"] = f"{fake}:{env['PATH']}"
        first = subprocess.run(["bash", str(SCRIPT), "--local"], capture_output=True, text=True, env={**env, "MPE_PERSONAL_REPO": str(self.repo)})
        second = subprocess.run(["bash", str(SCRIPT), "--local", "--personal-repo", str(other)], capture_output=True, text=True, env=env)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        backups = sorted(p.name for p in self.docs.glob("Patches.backup.*"))
        self.assertEqual(len(backups), 2, backups)
        for b in self.docs.glob("Patches.backup.*"):
            self.assertFalse(any(c.name.startswith("Patches.backup") for c in b.iterdir()), "backup was nested")
        self.assertEqual(first.returncode, 0)

    @unittest.skipIf(os.geteuid() == 0, "root ignores directory permissions")
    def test_a_failure_part_way_puts_the_earlier_links_back(self) -> None:
        os.chmod(self.docs, 0o555)
        self.addCleanup(os.chmod, self.docs, 0o755)
        proc = self.run_script()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("undoing", proc.stderr)
        self.assertEqual(self.links(), {"factory": False, "third": False, "user": False})
        self.assertEqual((self.res / "patches_factory/Bass/Sub.fxp").read_bytes(), b"sub")
        self.assertEqual((self.res / "patches_3rdparty/patches_3rdparty/Emu/Synths.fxp").read_bytes(), b"emu")
        self.assertFalse(list(self.res.glob("*.backup.*")))

    def test_no_restart_unless_asked(self) -> None:
        proc = self.run_script()
        self.assertNotIn("systemctl", proc.stdout + proc.stderr)
        self.assertIn("Restart", proc.stdout)


if __name__ == "__main__":
    unittest.main()
