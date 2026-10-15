"""Restore-to-default and template-merge helpers, against throwaway git repos.

Layout per test: a template repo (T), a bare personal origin (O), and a
personal checkout (P) cloned from T so the histories share a merge base, with
``origin`` -> O and ``upstream`` -> T. No network, nothing outside the temp dir.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from patch_browser import library_ops as lib

FACTORY = "assets/patches/patches_factory"
THIRD = "assets/patches/third-party/patches_3rdparty"
USER = "assets/user-data/Patches"
KEY = "factory:Bass/Sub"
BASS = f"{FACTORY}/Bass/Sub.fxp"


def git(repo: Path, *args: str) -> str:
    env = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        raise AssertionError(f"git {args}: {proc.stderr}{proc.stdout}")
    return proc.stdout.strip()


def put(repo: Path, rel: str, data: bytes | str) -> Path:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode())
    return path


def commit_all(repo: Path, msg: str) -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--allow-empty", "-m", msg)


def init_identity(repo: Path) -> None:
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "commit.gpgsign", "false")


class LibraryCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self._isolate_git()
        self.tpl = root / "template"
        self.origin = root / "origin.git"
        self.repo = root / "personal"

        self.tpl.mkdir()
        git(self.tpl, "init", "-q", "-b", "main")
        init_identity(self.tpl)
        put(self.tpl, BASS, b"template-bass-v1")
        put(self.tpl, f"{FACTORY}/Keys/Piano.fxp", b"template-piano")
        put(self.tpl, f"{THIRD}/Emu/Synths.fxp", b"template-emu")
        put(self.tpl, "assets/cc0-pack/Pad.fxp", b"template-cc0")
        put(self.tpl, f"{USER}/Starter.fxp", b"template-starter")
        put(self.tpl, "assets/binaries/engine.bin", b"engine")
        put(self.tpl, "README.md", "line1\nline2\nline3\n")
        put(self.tpl, "defaults/normalization.json", json.dumps({"factory:Bass/Sub": {"gain_db": 1.5}}))
        commit_all(self.tpl, "template v1")

        git(root, "init", "-q", "--bare", "-b", "main", str(self.origin))
        git(root, "clone", "-q", str(self.tpl), str(self.repo))
        init_identity(self.repo)
        git(self.repo, "remote", "add", "upstream", str(self.tpl))
        git(self.repo, "remote", "set-url", "origin", str(self.origin))
        git(self.repo, "push", "-q", "origin", "main")
        git(self.repo, "fetch", "-q", "upstream")

        self.cfg = lib.Config(repo=self.repo)

    def _isolate_git(self) -> None:
        saved = {k: os.environ.get(k) for k in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM")}
        os.environ["GIT_CONFIG_GLOBAL"] = "/dev/null"
        os.environ["GIT_CONFIG_SYSTEM"] = "/dev/null"

        def restore() -> None:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

        self.addCleanup(restore)

    def template_commit(self, rel: str, data: bytes | str, msg: str = "template change") -> None:
        put(self.tpl, rel, data)
        commit_all(self.tpl, msg)

    def user_commit(self, rel: str, data: bytes | str, msg: str = "user change") -> None:
        put(self.repo, rel, data)
        commit_all(self.repo, msg)

    def seed_settings(self) -> None:
        from patch_browser.patch_fx import PatchFx, PatchFxStore
        from patch_browser.patch_hold import PatchHoldStore
        from patch_browser.patch_metadata import PatchMetadataIndex

        s = self.repo / lib.SETTINGS_DIR
        s.mkdir(exist_ok=True)
        PatchFxStore(s / "patch_browser_fx.json").set("Sub", PatchFx(mask=1, depth=0.5), stable_key=KEY)
        PatchFxStore(s / "patch_browser_fx.json").set("Other", PatchFx(mask=2, depth=0.4), stable_key="factory:Keys/Piano")
        PatchHoldStore(s / "patch_browser_hold.json").set_user_hold_mult("Sub", 1.3, stable_key=KEY)
        PatchMetadataIndex(user_path=s / "patch_browser_metadata.json").set_user_instrument(KEY, "Bass")
        commit_all(self.repo, "settings")

    def fx_keys(self) -> set[str]:
        path = self.repo / lib.SETTINGS_DIR / "patch_browser_fx.json"
        return set(json.loads(path.read_text()))


class RestorePatchTests(LibraryCase):
    def test_restores_bytes_backs_up_edit_tags_and_pushes(self) -> None:
        self.seed_settings()
        put(self.repo, BASS, b"user-edit")
        result = lib.restore_patch(self.cfg, KEY)

        self.assertEqual((self.repo / BASS).read_bytes(), b"template-bass-v1")
        self.assertTrue(result.tag and result.tag.startswith("pre-restore/"))
        self.assertTrue(result.pushed)
        # the edit lives in the backup commit the tag points at
        self.assertEqual(git(self.repo, "show", f"{result.tag}:{BASS}"), "user-edit")
        # branch and tag reached the origin
        self.assertIn(result.tag, git(self.origin, "tag", "-l").split("\n"))
        self.assertEqual(git(self.origin, "rev-parse", "main"), result.backup_commit)

    def test_clears_only_that_patchs_settings(self) -> None:
        self.seed_settings()
        result = lib.restore_patch(self.cfg, KEY)
        self.assertEqual(self.fx_keys(), {"factory:Keys/Piano"})
        hold = json.loads((self.repo / "settings/patch_browser_hold.json").read_text())
        self.assertNotIn("user_hold_mult", hold.get(KEY, {}))
        meta = json.loads((self.repo / "settings/patch_browser_metadata.json").read_text())
        self.assertNotIn(KEY, meta["patches"])
        self.assertTrue(any("cleared settings in" in n for n in result.notes))

    def test_undo_command_returns_the_edit(self) -> None:
        put(self.repo, BASS, b"user-edit")
        result = lib.restore_patch(self.cfg, KEY)
        git(self.repo, "checkout", result.tag, "--", ".")
        self.assertEqual((self.repo / BASS).read_bytes(), b"user-edit")

    def test_failed_push_stops_before_touching_anything(self) -> None:
        put(self.repo, BASS, b"user-edit")
        git(self.repo, "remote", "set-url", "origin", str(Path(self._tmp.name) / "missing.git"))
        with self.assertRaises(lib.LibraryError) as ctx:
            lib.restore_patch(self.cfg, KEY)
        self.assertIn("Nothing was restored", str(ctx.exception))
        self.assertEqual((self.repo / BASS).read_bytes(), b"user-edit")
        # the local backup and tag exist, so the edit is safe
        self.assertTrue(git(self.repo, "tag", "-l", "pre-restore/*"))

    def test_allow_offline_proceeds_and_says_so(self) -> None:
        put(self.repo, BASS, b"user-edit")
        git(self.repo, "remote", "set-url", "origin", str(Path(self._tmp.name) / "missing.git"))
        self.cfg.allow_offline = True
        result = lib.restore_patch(self.cfg, KEY)
        self.assertFalse(result.pushed)
        self.assertEqual((self.repo / BASS).read_bytes(), b"template-bass-v1")
        self.assertTrue(any("local only" in n for n in result.notes))

    def test_patch_the_template_lacks_is_refused_without_a_backup(self) -> None:
        put(self.repo, f"{USER}/Mine.fxp", b"mine")
        commit_all(self.repo, "own patch")
        before = git(self.repo, "rev-parse", "HEAD")
        with self.assertRaises(lib.LibraryError):
            lib.restore_patch(self.cfg, "user:Mine")
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), before)
        self.assertEqual((self.repo / f"{USER}/Mine.fxp").read_bytes(), b"mine")

    def test_unsafe_keys_are_refused(self) -> None:
        for bad in ("factory:../../etc/x", "nokey", "weird:Bass/Sub", "factory:", "factory:/abs"):
            with self.assertRaises(lib.LibraryError, msg=bad):
                lib.patch_path_for_key(bad)

    def test_offline_fetch_uses_last_fetched_ref(self) -> None:
        git(self.repo, "remote", "set-url", "upstream", str(Path(self._tmp.name) / "gone"))
        put(self.repo, BASS, b"user-edit")
        result = lib.restore_patch(self.cfg, KEY)
        self.assertEqual((self.repo / BASS).read_bytes(), b"template-bass-v1")
        self.assertTrue(any("fetch failed" in n for n in result.notes))

    def test_two_restores_in_one_second_get_distinct_tags(self) -> None:
        import datetime as dt

        now = dt.datetime(2026, 10, 15, 12, 0, 0, tzinfo=dt.timezone.utc)
        a = lib.restore_patch(self.cfg, KEY, now=now)
        b = lib.restore_patch(self.cfg, KEY, now=now)
        self.assertNotEqual(a.tag, b.tag)
        self.assertEqual(a.tag, "pre-restore/20261015T120000Z")


class LiveSnapshotAndUndoTests(LibraryCase):
    def make_live_home(self, fx: dict) -> Path:
        home = Path(self._tmp.name) / "device-home"
        home.mkdir()
        (home / ".patch_browser_fx.json").write_text(json.dumps(fx))
        return home

    def test_live_store_newer_than_settings_is_captured_before_the_backup(self) -> None:
        self.seed_settings()  # settings/ holds the old copy
        newer = {"user:Brand New": {"mask": 4, "depth": 0.9}, "factory:Keys/Piano": {"mask": 2, "depth": 0.4}}
        home = self.make_live_home(newer)
        self.cfg.live_home = home
        result = lib.restore_patch(self.cfg, KEY)
        backed_up = git(self.repo, "show", f"{result.tag}:settings/patch_browser_fx.json")
        self.assertIn("user:Brand New", backed_up)
        self.assertEqual(self.fx_keys(), {"user:Brand New", "factory:Keys/Piano"})

    def test_cli_refuses_without_a_live_choice(self) -> None:
        rc = lib.main(["--repo", str(self.repo), "restore-patch", KEY])
        self.assertEqual(rc, 1)
        rc = lib.main(["--repo", str(self.repo), "factory-reset"])
        self.assertEqual(rc, 1)
        self.assertEqual((self.repo / BASS).read_bytes(), b"template-bass-v1")
        self.assertFalse(git(self.repo, "tag", "-l", "pre-restore/*"))

    def test_live_home_without_stores_is_harmless(self) -> None:
        self.cfg.live_home = Path(self._tmp.name) / "no-such-home"
        put(self.repo, BASS, b"user-edit")
        before = git(self.repo, "rev-parse", "HEAD")
        lib.restore_patch(self.cfg, KEY)  # missing files are skipped, not an error
        self.assertNotEqual(git(self.repo, "rev-parse", "HEAD"), before)

    def test_undo_is_limited_to_backed_up_paths_and_keeps_other_edits(self) -> None:
        put(self.repo, BASS, b"user-edit")
        put(self.repo, "README.md", "device edit not in the backup\n")
        git(self.repo, "add", "README.md")  # tracked, uncommitted, outside the backed-up paths
        with self.assertRaises(lib.LibraryError):  # restore itself refuses a dirty index
            lib.restore_patch(self.cfg, KEY)
        git(self.repo, "reset", "-q")
        git(self.repo, "checkout", "--", "README.md")
        result = lib.restore_patch(self.cfg, KEY)
        put(self.repo, "README.md", "later device edit\n")
        with self.assertRaises(lib.LibraryError):
            lib.undo_restore(self.cfg, result.tag)
        self.assertEqual((self.repo / "README.md").read_text(), "later device edit\n")
        git(self.repo, "checkout", "--", "README.md")
        lib.undo_restore(self.cfg, result.tag)
        self.assertEqual((self.repo / BASS).read_bytes(), b"user-edit")

    def test_undo_backs_up_edits_made_since_the_restore_first(self) -> None:
        put(self.repo, BASS, b"user-edit")
        result = lib.restore_patch(self.cfg, KEY)
        put(self.repo, BASS, b"edit made after the restore")
        undone = lib.undo_restore(self.cfg, result.tag)
        self.assertEqual((self.repo / BASS).read_bytes(), b"user-edit")
        self.assertNotEqual(undone.tag, result.tag)
        self.assertEqual(git(self.repo, "show", f"{undone.tag}:{BASS}"), "edit made after the restore")
        self.assertIn(undone.tag, git(self.origin, "tag", "-l").split("\n"))

    def test_undo_with_a_failing_push_leaves_the_files_alone(self) -> None:
        put(self.repo, BASS, b"user-edit")
        result = lib.restore_patch(self.cfg, KEY)
        put(self.repo, BASS, b"edit made after the restore")
        git(self.repo, "remote", "set-url", "origin", str(Path(self._tmp.name) / "missing.git"))
        with self.assertRaises(lib.LibraryError):
            lib.undo_restore(self.cfg, result.tag)
        self.assertEqual((self.repo / BASS).read_bytes(), b"edit made after the restore")

    def test_overrides_in_the_device_env_file_apply_to_its_own_home_only(self) -> None:
        import unittest.mock as mock

        self.seed_settings()
        home = Path(self._tmp.name) / "own-home"
        home.mkdir()
        elsewhere = Path(self._tmp.name) / "elsewhere-fx.json"
        elsewhere.write_text(json.dumps({"user:From Override": {"mask": 1, "depth": 0.5}}))
        (home / ".patch_browser_fx.json").write_text(json.dumps({"user:From Home": {"mask": 1, "depth": 0.5}}))
        env_file = Path(self._tmp.name) / "device.env"
        env_file.write_text(f"MPE_FX_FILE={elsewhere}\n")
        with mock.patch.dict(os.environ, {"MPE_ENV_FILE": str(env_file), "MPE_HOME": str(home)}):
            self.cfg.live_home = home
            lib.restore_patch(self.cfg, KEY)
        self.assertEqual(self.fx_keys(), {"user:From Override"})

    def test_env_file_overrides_are_ignored_for_someone_elses_home(self) -> None:
        import unittest.mock as mock

        self.seed_settings()
        other = Path(self._tmp.name) / "other-home"
        other.mkdir()
        (other / ".patch_browser_fx.json").write_text(json.dumps({"user:From Home": {"mask": 1, "depth": 0.5}}))
        env_file = Path(self._tmp.name) / "laptop.env"
        env_file.write_text("MPE_FX_FILE=/nonexistent/laptop-fx.json\n")
        with mock.patch.dict(os.environ, {"MPE_ENV_FILE": str(env_file), "MPE_HOME": str(Path(self._tmp.name) / "mine")}):
            self.cfg.live_home = other
            lib.restore_patch(self.cfg, KEY)
        self.assertEqual(self.fx_keys(), {"user:From Home"})

    def test_undo_force_keeps_the_other_edit_on_disk(self) -> None:
        put(self.repo, BASS, b"user-edit")
        result = lib.restore_patch(self.cfg, KEY)
        put(self.repo, "README.md", "unrelated edit\n")
        lib.undo_restore(self.cfg, result.tag, force=True)
        self.assertEqual((self.repo / "README.md").read_text(), "unrelated edit\n")
        self.assertEqual((self.repo / BASS).read_bytes(), b"user-edit")

    def test_glob_characters_in_a_patch_name_are_literal(self) -> None:
        rel = f"{FACTORY}/Bass/Lead [1]*.fxp"
        put(self.tpl, rel, b"tpl")
        put(self.tpl, f"{FACTORY}/Bass/Lead 1x.fxp", b"decoy")
        commit_all(self.tpl, "glob names")
        git(self.repo, "fetch", "-q", "upstream")
        git(self.repo, "merge", "-q", "upstream/main")
        put(self.repo, rel, b"edited")
        put(self.repo, f"{FACTORY}/Bass/Lead 1x.fxp", b"decoy edited")
        commit_all(self.repo, "edits")
        lib.restore_patch(self.cfg, "factory:Bass/Lead [1]*")
        self.assertEqual((self.repo / rel).read_bytes(), b"tpl")
        self.assertEqual((self.repo / f"{FACTORY}/Bass/Lead 1x.fxp").read_bytes(), b"decoy edited")


class RestoreStockTests(LibraryCase):
    def test_restores_stock_and_cc0_but_not_user_or_settings(self) -> None:
        self.seed_settings()
        put(self.repo, BASS, b"edit")
        put(self.repo, "assets/cc0-pack/Pad.fxp", b"edit-cc0")
        put(self.repo, f"{USER}/Starter.fxp", b"my-version")
        commit_all(self.repo, "edits")
        result = lib.restore_stock(self.cfg)

        self.assertEqual((self.repo / BASS).read_bytes(), b"template-bass-v1")
        self.assertEqual((self.repo / "assets/cc0-pack/Pad.fxp").read_bytes(), b"template-cc0")
        self.assertEqual((self.repo / f"{USER}/Starter.fxp").read_bytes(), b"my-version")
        self.assertEqual(self.fx_keys(), {KEY, "factory:Keys/Piano"})
        self.assertEqual(git(self.repo, "show", f"{result.tag}:{BASS}"), "edit")

    def test_stock_dirs_exclude_user_binaries_and_state(self) -> None:
        put(self.tpl, "assets/appliance-state/x.fxp", b"x")
        commit_all(self.tpl, "state")
        git(self.repo, "fetch", "-q", "upstream")
        dirs = lib.stock_asset_dirs(self.cfg)
        self.assertEqual(dirs, ["assets/cc0-pack", "assets/patches"])


class FactoryResetTests(LibraryCase):
    def test_everything_returns_to_template_and_defaults(self) -> None:
        self.seed_settings()
        put(self.repo, BASS, b"edit")
        put(self.repo, f"{USER}/Mine.fxp", b"mine")
        put(self.repo, f"{USER}/Starter.fxp", b"my-version")
        commit_all(self.repo, "edits")
        result = lib.factory_reset(self.cfg)

        self.assertEqual((self.repo / BASS).read_bytes(), b"template-bass-v1")
        self.assertFalse((self.repo / f"{USER}/Mine.fxp").exists())
        self.assertEqual((self.repo / f"{USER}/Starter.fxp").read_bytes(), b"template-starter")
        self.assertEqual(self.fx_keys(), set())
        norm = json.loads((self.repo / "settings/patch_browser_normalization.json").read_text())
        self.assertEqual(norm, {"factory:Bass/Sub": {"gain_db": 1.5}})
        meta = json.loads((self.repo / "settings/patch_browser_metadata.json").read_text())
        self.assertEqual(meta["patches"], {})
        # the user's patch is recoverable from the tag
        self.assertEqual(git(self.repo, "show", f"{result.tag}:{USER}/Mine.fxp"), "mine")

    def test_user_patch_dir_survives_when_template_has_no_starter(self) -> None:
        git(self.tpl, "rm", "-r", "-q", USER)
        git(self.tpl, "commit", "-q", "-m", "no starter")
        git(self.repo, "fetch", "-q", "upstream")
        put(self.repo, f"{USER}/Mine.fxp", b"mine")
        commit_all(self.repo, "own")
        lib.factory_reset(self.cfg)
        # the device's link target must still exist
        self.assertTrue((self.repo / USER).is_dir())
        self.assertEqual(list((self.repo / USER).iterdir()), [])


class UpdateTests(LibraryCase):
    def kept(self) -> dict:
        return json.loads((self.repo / lib.KEPT_FILE).read_text())

    def test_user_wins_on_a_patch_both_sides_changed_and_blob_is_recorded(self) -> None:
        self.user_commit(BASS, b"user-bass")
        self.template_commit(BASS, b"template-bass-v2")
        theirs_blob = git(self.tpl, "rev-parse", f"HEAD:{BASS}")
        lib.update_from_template(self.cfg)

        self.assertEqual((self.repo / BASS).read_bytes(), b"user-bass")
        entries = self.kept()["entries"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["path"], BASS)
        self.assertEqual(entries[0]["blob"], theirs_blob)
        self.assertEqual(entries[0]["upstream_commit"], git(self.tpl, "rev-parse", "HEAD"))
        # the recorded blob really is the template's bytes
        self.assertEqual(git(self.repo, "cat-file", "-p", theirs_blob), "template-bass-v2")

    def test_no_template_copy_is_written_into_the_tree(self) -> None:
        self.user_commit(BASS, b"user-bass")
        self.template_commit(BASS, b"template-bass-v2")
        lib.update_from_template(self.cfg)
        fxps = sorted(p.name for p in self.repo.rglob("*.fxp"))
        self.assertEqual(len(fxps), 5)

    def test_untouched_patch_takes_the_template_version(self) -> None:
        self.template_commit(f"{FACTORY}/Keys/Piano.fxp", b"template-piano-v2")
        self.user_commit(BASS, b"user-bass")
        lib.update_from_template(self.cfg)
        self.assertEqual((self.repo / f"{FACTORY}/Keys/Piano.fxp").read_bytes(), b"template-piano-v2")
        self.assertEqual(self.kept()["entries"], [])

    def test_template_deletion_applies_only_when_user_has_not_modified(self) -> None:
        self.user_commit(BASS, b"user-bass")
        git(self.tpl, "rm", "-q", BASS)
        git(self.tpl, "rm", "-q", f"{FACTORY}/Keys/Piano.fxp")
        git(self.tpl, "commit", "-q", "-m", "withdraw two")
        lib.update_from_template(self.cfg)
        self.assertEqual((self.repo / BASS).read_bytes(), b"user-bass")
        self.assertFalse((self.repo / f"{FACTORY}/Keys/Piano.fxp").exists())
        entry = self.kept()["entries"][0]
        self.assertEqual((entry["path"], entry["blob"]), (BASS, None))

    def test_user_deleted_patch_stays_deleted(self) -> None:
        git(self.repo, "rm", "-q", BASS)
        git(self.repo, "commit", "-q", "-m", "drop")
        self.template_commit(BASS, b"template-bass-v2")
        lib.update_from_template(self.cfg)
        self.assertFalse((self.repo / BASS).exists())

    def test_text_file_edited_in_different_places_merges_cleanly_and_is_not_recorded(self) -> None:
        self.user_commit("README.md", "line1 mine\nline2\nline3\n")
        self.template_commit("README.md", "line1\nline2\nline3 theirs\n")
        lib.update_from_template(self.cfg)
        self.assertEqual((self.repo / "README.md").read_text(), "line1 mine\nline2\nline3 theirs\n")
        self.assertEqual(self.kept()["entries"], [])

    def test_same_line_edited_on_both_sides_keeps_the_users_and_records_it(self) -> None:
        self.user_commit("README.md", "line1 mine\nline2\nline3\n")
        self.template_commit("README.md", "line1 theirs\nline2\nline3\n")
        lib.update_from_template(self.cfg)
        self.assertEqual((self.repo / "README.md").read_text(), "line1 mine\nline2\nline3\n")
        self.assertEqual(self.kept()["entries"][0]["path"], "README.md")
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")

    def test_rerun_is_a_no_op_and_does_not_duplicate_records(self) -> None:
        self.user_commit(BASS, b"user-bass")
        self.template_commit(BASS, b"template-bass-v2")
        lib.update_from_template(self.cfg)
        head = git(self.repo, "rev-parse", "HEAD")
        again = lib.update_from_template(self.cfg)
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), head)
        self.assertTrue(any("up to date" in n for n in again.notes))
        self.assertEqual(len(self.kept()["entries"]), 1)

    def test_second_template_release_appends_a_second_record(self) -> None:
        self.user_commit(BASS, b"user-bass")
        self.template_commit(BASS, b"template-bass-v2")
        lib.update_from_template(self.cfg)
        self.template_commit(BASS, b"template-bass-v3")
        lib.update_from_template(self.cfg)
        self.assertEqual((self.repo / BASS).read_bytes(), b"user-bass")
        self.assertEqual(len(self.kept()["entries"]), 2)

    def test_dirty_tree_is_refused(self) -> None:
        put(self.repo, BASS, b"uncommitted")
        self.template_commit("README.md", "new\n")
        with self.assertRaises(lib.LibraryError):
            lib.update_from_template(self.cfg)
        self.assertEqual((self.repo / BASS).read_bytes(), b"uncommitted")

    def test_settings_and_defaults_do_not_conflict(self) -> None:
        self.seed_settings()
        self.template_commit("defaults/normalization.json", json.dumps({"factory:Bass/Sub": {"gain_db": 2.0}}))
        lib.update_from_template(self.cfg)
        self.assertEqual(self.fx_keys(), {KEY, "factory:Keys/Piano"})
        self.assertIn("2.0", (self.repo / "defaults/normalization.json").read_text())

    def test_strip_commit_trap_absorb_keeps_user_folders(self) -> None:
        put(self.tpl, "assets/appliance-state/cap.txt", b"capture")
        commit_all(self.tpl, "has state")
        git(self.repo, "fetch", "-q", "upstream")
        lib.update_from_template(self.cfg)
        self.assertTrue((self.repo / "assets/appliance-state/cap.txt").exists())
        git(self.tpl, "rm", "-r", "-q", "assets/appliance-state", USER)
        git(self.tpl, "commit", "-q", "-m", "strip")
        strip = git(self.tpl, "rev-parse", "HEAD")
        git(self.repo, "fetch", "-q", "upstream")
        lib.absorb_commit(self.cfg, strip)
        self.assertTrue((self.repo / "assets/appliance-state/cap.txt").exists())
        self.assertTrue((self.repo / f"{USER}/Starter.fxp").exists())
        again = lib.absorb_commit(self.cfg, strip)
        self.assertTrue(any("already" in n for n in again.notes))
        self.template_commit("README.md", "later\n")
        lib.update_from_template(self.cfg)
        self.assertTrue((self.repo / "assets/appliance-state/cap.txt").exists())


class StoreHitTests(unittest.TestCase):
    def test_link_name_decides_the_key_and_a_rename_shows_as_lost_hits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real = root / "repo" / "factory-tree"
            (real / "Bass").mkdir(parents=True)
            (real / "Bass" / "Sub.fxp").write_bytes(b"x")
            good = root / "patches_factory"
            good.symlink_to(real)
            store = root / "fx.json"
            store.write_text(json.dumps({"factory:Bass/Sub": {"mask": 1}, "factory:Gone/Nope": {}}))
            before = lib.store_hit_snapshot([good], {"fx": store})
            self.assertEqual(before["total_patches"], 1)
            self.assertEqual(before["store_hits"]["fx"]["stable_hits"], 1)

            renamed = root / "factory_renamed"
            renamed.symlink_to(real)
            after = lib.store_hit_snapshot([renamed], {"fx": store})
            problems = lib.compare_snapshots(before, after)
            self.assertTrue(any("stable_hits" in p for p in problems))
            self.assertEqual(lib.compare_snapshots(before, before), [])


class RekeyThirdPartyTests(unittest.TestCase):
    NESTED = "thirdparty:patches_3rdparty/Emu/Synths"
    FLAT = "thirdparty:Emu/Synths"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        self.stores = {
            "hold": self.home / ".patch_browser_hold.json",
            "pressure": self.home / ".patch_browser_pressure.json",
            "metadata": self.home / ".patch_browser_metadata.json",
            "favorites": self.home / ".patch_browser_favorites.json",
            "fx": self.home / ".patch_browser_fx.json",  # absent: skipped
        }
        self.write("hold", {self.NESTED: {"a": 1}, "factory:Bass/Sub": {"a": 2}, "_global": {"x": 1}})
        # an orphaned flat key for ANOTHER patch stays as it is
        self.write("pressure", {"thirdparty:Kuniklo/Pad": {"t": 1}, "Legacy Stem": {"t": 2}})
        self.write("metadata", {"version": 1, "patches": {self.NESTED: {"tags": ["x"]}}})
        self.write("favorites", {"version": 2, "folders": [], "entries": {self.NESTED: {"dest_path": "/q/Synths.fxp"}}})

    def write(self, label: str, data: dict) -> None:
        self.stores[label].write_text(json.dumps(data))

    def read(self, label: str) -> dict:
        return json.loads(self.stores[label].read_text())

    def backups(self) -> list[str]:
        return sorted(p.name for p in self.home.glob("*.pre-rekey.*"))

    def test_rekeys_every_store_with_a_backup_of_each_changed_one(self) -> None:
        originals = {k: p.read_bytes() for k, p in self.stores.items() if p.exists()}
        lib.rekey_thirdparty(self.stores)
        self.assertEqual(self.read("hold"), {self.FLAT: {"a": 1}, "factory:Bass/Sub": {"a": 2}, "_global": {"x": 1}})
        self.assertEqual(self.read("metadata"), {"version": 1, "patches": {self.FLAT: {"tags": ["x"]}}})
        fav = self.read("favorites")
        self.assertEqual(fav["entries"], {self.FLAT: {"dest_path": "/q/Synths.fxp"}})
        self.assertEqual((fav["version"], fav["folders"]), (2, []))
        # pressure had nothing nested: untouched, no backup
        self.assertEqual(self.stores["pressure"].read_bytes(), originals["pressure"])
        self.assertFalse(self.stores["fx"].exists())
        names = self.backups()
        self.assertEqual(len(names), 3, names)
        for label in ("hold", "metadata", "favorites"):
            (backup,) = self.home.glob(self.stores[label].name + ".pre-rekey.*")
            self.assertEqual(backup.read_bytes(), originals[label])

    def test_second_run_changes_nothing(self) -> None:
        lib.rekey_thirdparty(self.stores)
        after = {k: p.read_bytes() for k, p in self.stores.items() if p.exists()}
        names = self.backups()
        result = lib.rekey_thirdparty(self.stores)
        self.assertIn("nothing to re-key", " ".join(result.notes))
        self.assertEqual({k: p.read_bytes() for k, p in self.stores.items() if p.exists()}, after)
        self.assertEqual(self.backups(), names)

    def test_both_forms_for_one_patch_refuses_and_writes_nothing(self) -> None:
        # the conflict is in the LAST store checked; earlier stores must not be written either
        self.write("pressure", {self.NESTED: {"t": 1}, self.FLAT: {"t": 2}})
        originals = {k: p.read_bytes() for k, p in self.stores.items() if p.exists()}
        with self.assertRaises(lib.LibraryError) as ctx:
            lib.rekey_thirdparty(self.stores)
        self.assertIn("both", str(ctx.exception))
        self.assertEqual({k: p.read_bytes() for k, p in self.stores.items() if p.exists()}, originals)
        self.assertEqual(self.backups(), [])

    def test_unreadable_store_refuses_and_writes_nothing(self) -> None:
        self.stores["pressure"].write_text("{not json")
        originals = {k: p.read_bytes() for k, p in self.stores.items() if p.exists()}
        with self.assertRaises(lib.LibraryError):
            lib.rekey_thirdparty(self.stores)
        self.assertEqual({k: p.read_bytes() for k, p in self.stores.items() if p.exists()}, originals)

    def test_undo_restores_the_backups(self) -> None:
        originals = {k: p.read_bytes() for k, p in self.stores.items() if p.exists()}
        result = lib.rekey_thirdparty(self.stores)
        (undo,) = [n for n in result.notes if n.startswith("undo:")]
        stamp = undo.rsplit(" ", 1)[1]
        lib.undo_rekey(self.stores, stamp)
        self.assertEqual({k: p.read_bytes() for k, p in self.stores.items() if p.exists()}, originals)
        with self.assertRaises(lib.LibraryError):
            lib.undo_rekey(self.stores, "19990101T000000Z")

    def test_cli_uses_the_live_home_stores(self) -> None:
        env = dict(os.environ, MPE_HOME=str(self.home), HOME=str(self.home))
        for k in ("MPE_FX_FILE", "MPE_HOLD_FILE", "MPE_PRESSURE_FILE", "MPE_NORMALIZATION_FILE", "MPE_FAVORITES_INDEX_FILE"):
            env.pop(k, None)
        script = Path(__file__).resolve().parents[1] / "scripts" / "mpe-library"
        proc = subprocess.run(["python3", str(script), "rekey-thirdparty"], capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(self.FLAT, self.read("hold"))
        self.assertIn(self.FLAT, self.read("favorites")["entries"])


class VerifyIncreaseTests(unittest.TestCase):
    def test_flattening_plus_rekey_only_adds_hits_and_says_so(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            res = home / "res"
            # before: nested layout; the nested key lands, the flat one is orphaned
            nested = res / "patches_3rdparty"
            (nested / "patches_3rdparty/Emu").mkdir(parents=True)
            (nested / "patches_3rdparty/Emu/Synths.fxp").write_bytes(b"e")
            (nested / "patches_3rdparty/Kuniklo").mkdir()
            (nested / "patches_3rdparty/Kuniklo/Pad.fxp").write_bytes(b"k")
            stores = {"hold": home / "hold.json", "favorites": home / "fav.json"}
            stores["hold"].write_text(json.dumps({"thirdparty:patches_3rdparty/Emu/Synths": {}, "thirdparty:Kuniklo/Pad": {}}))
            stores["favorites"].write_text(json.dumps({"entries": {"thirdparty:patches_3rdparty/Emu/Synths": {}}}))
            before = lib.store_hit_snapshot([nested], stores)
            self.assertEqual(before["store_hits"]["hold"]["stable_hits"], 1)
            self.assertEqual(before["store_hits"]["favorites"]["stable_hits"], 1)

            # after: flat layout and re-keyed stores
            flat_tree = home / "lib/patches_3rdparty"
            (nested / "patches_3rdparty").rename(home / "inner")
            nested.rmdir()
            flat_tree.parent.mkdir()
            (home / "inner").rename(flat_tree)
            lib.rekey_thirdparty(stores)
            after = lib.store_hit_snapshot([flat_tree], stores)
            self.assertEqual(lib.compare_snapshots(before, after), [])
            self.assertEqual(after["store_hits"]["hold"]["stable_hits"], 2)
            self.assertEqual(lib.snapshot_increases(before, after), ["hold: stable_hits 1 -> 2 (+1)"])

            # flattening WITHOUT the re-key loses the nested key's hit: verify must say so
            for p in stores.values():
                for b in home.glob(p.name + ".pre-rekey.*"):
                    b.replace(p)
            lost = lib.store_hit_snapshot([flat_tree], stores)
            problems = lib.compare_snapshots(before, lost)
            self.assertTrue(any(p.startswith("favorites: stable_hits") for p in problems), problems)


if __name__ == "__main__":
    unittest.main()
