"""Favorites copies -> pointers migration (#181), against throwaway trees and git repos.

Layout per test: a library checkout (git) holding a factory root, a third-party root and
a user root, with the favorites folder inside the user root; the user root is reached
through a symlink, as on the device. Stores and the index live in a separate home.
Nothing leaves the temp dir and no network or device is touched.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from patch_browser import favorites_migrate as fm
from patch_browser.library_ops import LibraryError

FACTORY = "assets/patches/patches_factory"
THIRD = "assets/patches/third-party/patches_3rdparty"
USER = "assets/user-data/Patches"
QA = "Quick Access"


def git(repo: Path, *args: str) -> str:
    env = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        raise AssertionError(f"git {args}: {proc.stderr}{proc.stdout}")
    return proc.stdout.strip()


def digest(root: Path) -> dict[str, str]:
    out = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and ".git" not in path.parts:
            out[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


class MigrationCase(unittest.TestCase):
    """A device in miniature."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.repo = self.root / "library"
        self.home = self.root / "home"
        self.home.mkdir()
        self.repo.mkdir()
        env = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.repo)], check=True, env=env)
        git(self.repo, "config", "user.name", "Test")
        git(self.repo, "config", "user.email", "test@example.invalid")
        git(self.repo, "config", "commit.gpgsign", "false")
        # Same environment for the module's own git calls.
        patcher = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"})
        patcher.start()
        self.addCleanup(patcher.stop)

        # stock
        self.put(f"{FACTORY}/Bass/Acid.fxp", b"stock-acid")
        self.put(f"{FACTORY}/Keys/Rhodes.fxp", b"stock-rhodes")
        self.put(f"{FACTORY}/Pads/Cloud.fxp", b"stock-cloud")
        self.put(f"{FACTORY}/Strings/Bowed String.fxp", b"stock-bowed")
        self.put(f"{FACTORY}/Leads/Duduk.fxp", b"stock-duduk")
        self.put(f"{THIRD}/Dan/Kick.fxp", b"stock-kick")
        self.put(f"{THIRD}/Dan/Chime.fxp", b"stock-chime")
        # the favorites folder: copies
        self.put(f"{USER}/{QA}/Acid.fxp", b"stock-acid")  # identical, indexed
        self.put(f"{USER}/{QA}/Gig/Rhodes.fxp", b"stock-rhodes")  # identical, in a folder
        self.put(f"{USER}/{QA}/Kick.fxp", b"stock-kick")  # identical, not in the index
        self.put(f"{USER}/{QA}/Bowed String.fxp", b"resaved-bowed")  # plain re-save
        self.put(f"{USER}/{QA}/Duduk.fxp", b"tuned-duduk")  # tuned, indexed under stock key
        self.put(f"{USER}/{QA}/My Own.fxp", b"original")  # no namesake in the library
        self.put(f"{USER}/Linn/Winds/Glassy Wind.fxp", b"tuned-glassy")  # outside, tuned
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "device state")
        self.base = git(self.repo, "rev-parse", "HEAD")

        # The user root is reached through a link named Patches, as on the device.
        self.link_parent = self.root / "docs"
        self.link_parent.mkdir()
        self.user_link = self.link_parent / "Patches"
        self.user_link.symlink_to(self.repo / USER)
        self.roots = [self.repo / FACTORY, self.repo / THIRD, self.user_link]
        self.fav_dir = self.user_link / QA

        self.index_path = self.home / ".patch_browser_favorites.json"
        self.index_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "folders": ["Gig"],
                    "entries": {
                        "factory:Bass/Acid": {"folder": "", "dest_path": f"/x/{QA}/Acid.fxp", "added_at": "t1"},
                        "factory:Keys/Rhodes": {"folder": "Gig", "dest_path": f"/x/{QA}/Gig/Rhodes.fxp", "added_at": "t2", "order": 4},
                        "factory:Leads/Duduk": {"folder": "", "dest_path": f"/x/{QA}/Duduk.fxp", "added_at": "t3"},
                        "factory:Strings/Bowed String": {"folder": "", "dest_path": f"/x/{QA}/Bowed String.fxp"},
                        "thirdparty:patches_3rdparty/Dan/Chime": {"folder": "", "dest_path": f"/x/{QA}/Chime.fxp"},
                    },
                }
            )
        )
        self.stores = {
            "fx": self.home / ".patch_browser_fx.json",
            "hold": self.home / ".patch_browser_hold.json",
            "pressure": self.home / ".patch_browser_pressure.json",
            "normalization": self.home / ".patch_browser_normalization.json",
            "metadata": self.home / ".patch_browser_metadata.json",
        }
        self.write_store(
            "normalization",
            {
                "_global": {"enabled": True},
                "user:Quick Access/Acid": {"user_trim_db": -5.6, "gain_db": -3.0},
                "factory:Bass/Acid": {"user_trim_db": -7.8, "calibrated_at": "old"},
                "user:Quick Access/Gig/Rhodes": {"user_trim_db": -1.0},
                "user:Quick Access/Duduk": {"user_trim_db": -2.0},
                "factory:Leads/Duduk": {"user_trim_db": 9.0, "calibrated_at": "stock"},
                "user:Quick Access/My Own": {"user_trim_db": -4.0},
                "user:Quick Access/Bowed String": {"user_trim_db": -6.0},
            },
        )
        self.write_store("hold", {"user:Quick Access/Kick": {"hold_mult": 2.0}})
        self.write_store("fx", {"user:Quick Access/Acid": {"enabled": [1]}})
        self.write_store(
            "metadata",
            {"version": 1, "patches": {"user:Quick Access/Acid": {"instrument_user": "lead"}}},
        )
        self.write_store("pressure", {"factory:Leads/Duduk": {"touch_offset": 0.25}})

    # -- helpers
    def put(self, rel: str, data: bytes) -> None:
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def write_store(self, label: str, data: dict) -> None:
        self.stores[label].write_text(json.dumps(data, indent=2, sort_keys=True))

    def store(self, label: str) -> dict:
        return json.loads(self.stores[label].read_text())

    def kw(self, **over):
        base = dict(
            repo=self.repo,
            roots=self.roots,
            fav_dir=self.fav_dir,
            index_path=self.index_path,
            store_paths=self.stores,
        )
        base.update(over)
        return base

    def plan(self, **over) -> fm.Plan:
        return fm.build_plan(**self.kw(**over))

    def apply(self, plan: fm.Plan | None = None) -> str | None:
        plan = plan or self.plan()
        return fm.apply_plan(
            plan, repo=self.repo, index_path=self.index_path, store_paths=self.stores
        )

    def klass(self, plan: fm.Plan) -> dict[str, tuple[str, str]]:
        return {r.ident: (r.klass, r.after_key) for r in plan.rows}

    def tree(self) -> dict[str, str]:
        return digest(self.root)


class Classification(MigrationCase):
    def assert_classification(self, plan: fm.Plan) -> None:
        k = self.klass(plan)
        self.assertEqual(k["Acid.fxp"], (fm.POINTER, "factory:Bass/Acid"))
        self.assertEqual(k["Gig/Rhodes.fxp"], (fm.POINTER, "factory:Keys/Rhodes"))
        self.assertEqual(k["Kick.fxp"], (fm.POINTER, "thirdparty:Dan/Kick"))
        self.assertEqual(k["Bowed String.fxp"], (fm.RESAVE, "factory:Strings/Bowed String"))
        self.assertEqual(k["Duduk.fxp"], (fm.TUNED, "user:Tuned Patches/Duduk"))
        self.assertEqual(k["My Own.fxp"], (fm.USER, "user:Quick Access/My Own"))
        glassy = [v for key, v in k.items() if "Glassy Wind" in key]
        self.assertEqual(glassy, [(fm.TUNED, "user:Tuned Patches/Glassy Wind")])
        # the legacy spelling is normalized when the index loads
        self.assertEqual(k["thirdparty:Dan/Chime"], (fm.INDEX_ONLY, "thirdparty:Dan/Chime"))

    def test_by_bytes(self) -> None:
        plan = self.plan()
        self.assertEqual(plan.errors, [])
        self.assert_classification(plan)

    def test_a_renamed_identical_copy_is_still_a_pointer(self) -> None:
        (self.fav_dir / "Whatever.fxp").write_bytes(b"stock-cloud")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "x")
        k = self.klass(self.plan())
        self.assertEqual(k["Whatever.fxp"], (fm.POINTER, "factory:Pads/Cloud"))

    def test_same_name_different_bytes_is_not_a_pointer(self) -> None:
        (self.fav_dir / "Rhodes.fxp").write_bytes(b"someone-edited")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "x")
        k = self.klass(self.plan())
        self.assertEqual(k["Rhodes.fxp"], (fm.USER, "user:Quick Access/Rhodes"))

    def test_identical_to_two_library_files_prefers_the_index_key_then_the_name(self) -> None:
        self.put(f"{THIRD}/Other/Acid Twin.fxp", b"stock-acid")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "x")
        plan = self.plan()
        row = next(r for r in plan.rows if r.file_rel == "Acid.fxp")
        self.assertEqual(row.after_key, "factory:Bass/Acid")  # the index named it
        self.assertIn("identical to 2", row.note)

    def test_the_two_names_are_configurable(self) -> None:
        plan = self.plan(tuned_names=("Cyber Pad",), resave_names=())
        k = self.klass(plan)
        self.assertEqual(k["Duduk.fxp"][0], fm.USER)  # no longer named as tuned
        self.assertEqual(k["Bowed String.fxp"][0], fm.USER)

    def test_classification_assert_has_teeth(self) -> None:
        """Mutant: decide by file name. The by-bytes assertions must notice."""
        with mock.patch.object(fm, "_sha", lambda path: path.stem):
            with self.assertRaises(AssertionError):
                self.assert_classification(self.plan())


class DryRun(MigrationCase):
    def test_writes_nothing(self) -> None:
        before = self.tree()
        head = git(self.repo, "rev-parse", "HEAD")
        plan = self.plan()
        text = fm.format_plan(plan)
        self.assertEqual(self.tree(), before)
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), head)
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")
        self.assertFalse((self.home / fm.BACKUP_DIR_NAME).exists())
        for needle in (
            "pointer         Acid.fxp -> factory:Bass/Acid",
            "tuned",
            "git mv",
            "git rm",
            "CONFLICT (favorite wins) user_trim_db",
            "index: rewritten as version 2",
        ):
            self.assertIn(needle, text)

    def test_the_plan_names_every_file_and_store_key(self) -> None:
        text = fm.format_plan(self.plan())
        for needle in ("Gig/Rhodes.fxp", "Kick.fxp", "Bowed String.fxp", "My Own.fxp", "Glassy Wind",
                       "normalization: ", "user:Quick Access/Acid -> factory:Bass/Acid"):
            self.assertIn(needle, text)


class Apply(MigrationCase):
    def setUp(self) -> None:
        super().setUp()
        self.before_stores = {k: p.read_bytes() for k, p in self.stores.items()}
        self.before_index = self.index_path.read_bytes()
        self.stamp = self.apply()

    def test_files(self) -> None:
        qa = self.repo / USER / QA
        self.assertEqual(sorted(p.name for p in qa.rglob("*.fxp")), ["My Own.fxp"])
        tuned = self.repo / USER / "Tuned Patches"
        self.assertEqual(sorted(p.name for p in tuned.glob("*.fxp")), ["Duduk.fxp", "Glassy Wind.fxp"])
        self.assertEqual((tuned / "Duduk.fxp").read_bytes(), b"tuned-duduk")
        self.assertEqual((tuned / "Glassy Wind.fxp").read_bytes(), b"tuned-glassy")
        self.assertFalse((self.repo / USER / "Linn" / "Winds" / "Glassy Wind.fxp").exists())
        # stock is untouched
        self.assertEqual((self.repo / FACTORY / "Bass" / "Acid.fxp").read_bytes(), b"stock-acid")

    def test_one_commit_that_holds_only_the_moves(self) -> None:
        self.assertEqual(git(self.repo, "rev-list", "--count", f"{self.base}..HEAD"), "1")
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")
        subject = git(self.repo, "log", "-1", "--format=%s")
        self.assertIn("Favorites become pointers", subject)
        self.assertIn("2 tuned patches", subject)
        changed = git(self.repo, "diff", "--name-status", "-M", self.base, "HEAD").splitlines()
        self.assertEqual(len(changed), 6, changed)  # 4 copies removed, 2 tuned patches moved
        self.assertTrue(all(USER in line or QA in line for line in changed))

    def test_index(self) -> None:
        raw = json.loads(self.index_path.read_text())
        self.assertEqual(raw["version"], 2)
        self.assertTrue(all("dest_path" not in e for e in raw["entries"].values()))
        self.assertEqual(
            sorted(raw["entries"]),
            sorted([
                "factory:Bass/Acid", "factory:Keys/Rhodes", "thirdparty:Dan/Kick",
                "factory:Strings/Bowed String", "user:Tuned Patches/Duduk",
                "user:Tuned Patches/Glassy Wind", "user:Quick Access/My Own", "thirdparty:Dan/Chime",
            ]),
        )
        self.assertEqual(raw["entries"]["factory:Keys/Rhodes"]["folder"], "Gig")
        self.assertEqual(raw["entries"]["factory:Bass/Acid"]["added_at"], "t1")
        self.assertEqual(raw["entries"]["factory:Keys/Rhodes"]["order"], 4)
        # the stock key the tuned patch was filed under is gone: the favorite plays the tuned file
        self.assertNotIn("factory:Leads/Duduk", raw["entries"])
        self.assertEqual(raw["imported"], ["user:Quick Access/My Own"])

    def test_settings_move_to_the_target_key(self) -> None:
        norm = self.store("normalization")
        self.assertNotIn("user:Quick Access/Acid", norm)
        self.assertNotIn("user:Quick Access/Gig/Rhodes", norm)
        self.assertEqual(norm["factory:Keys/Rhodes"]["user_trim_db"], -1.0)
        self.assertEqual(self.store("hold")["thirdparty:Dan/Kick"], {"hold_mult": 2.0})
        self.assertEqual(self.store("fx")["factory:Bass/Acid"], {"enabled": [1]})
        self.assertEqual(
            self.store("metadata")["patches"]["factory:Bass/Acid"], {"instrument_user": "lead"}
        )
        self.assertEqual(norm["factory:Strings/Bowed String"]["user_trim_db"], -6.0)
        self.assertEqual(norm["_global"], {"enabled": True})

    def test_the_favorites_value_wins_and_the_targets_other_fields_stay(self) -> None:
        acid = self.store("normalization")["factory:Bass/Acid"]
        self.assertEqual(acid["user_trim_db"], -5.6)  # the copy's, not the source's -7.8
        self.assertEqual(acid["gain_db"], -3.0)
        self.assertEqual(acid["calibrated_at"], "old")  # only the source had it

    def test_a_tuned_patch_takes_its_copys_settings_and_starts_from_the_stock_ones(self) -> None:
        norm = self.store("normalization")
        duduk = norm["user:Tuned Patches/Duduk"]
        self.assertEqual(duduk["user_trim_db"], -2.0)  # the copy's value wins
        self.assertEqual(duduk["calibrated_at"], "stock")  # inherited from the stock key
        self.assertEqual(norm["factory:Leads/Duduk"]["user_trim_db"], 9.0)  # stock keeps its own
        self.assertEqual(self.store("pressure")["user:Tuned Patches/Duduk"], {"touch_offset": 0.25})
        self.assertEqual(self.store("pressure")["factory:Leads/Duduk"], {"touch_offset": 0.25})

    def test_own_patches_are_not_re_keyed(self) -> None:
        self.assertEqual(self.store("normalization")["user:Quick Access/My Own"], {"user_trim_db": -4.0})

    def test_backups_are_byte_exact_and_in_a_timestamped_run(self) -> None:
        run = fm.run_dir_for(self.index_path, self.stamp)
        for label, data in self.before_stores.items():
            self.assertEqual((run / "backup" / f"{label}.json").read_bytes(), data)
        self.assertEqual((run / "backup" / "index.json").read_bytes(), self.before_index)
        manifest = json.loads((run / fm.MANIFEST).read_text())
        self.assertEqual(manifest["state"], "done")
        self.assertEqual(manifest["repo_base_commit"], self.base)
        self.assertRegex(self.stamp, r"^\d{8}T\d{12}Z$")

    def test_idempotent(self) -> None:
        tree = self.tree()
        head = git(self.repo, "rev-parse", "HEAD")
        plan = self.plan()
        self.assertTrue(plan.empty, fm.format_plan(plan))
        self.assertIn("nothing to do", fm.format_plan(plan))
        self.assertIsNone(self.apply(plan))
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), head)
        self.assertEqual(self.tree(), tree)

    def test_a_folder_chosen_since_survives_a_second_run(self) -> None:
        raw = json.loads(self.index_path.read_text())
        raw["entries"]["user:Quick Access/My Own"]["folder"] = "Faves"
        raw["folders"].append("Faves")
        self.index_path.write_text(json.dumps(raw))
        plan = self.plan()
        self.assertTrue(plan.empty, fm.format_plan(plan))

    def test_verify_passes(self) -> None:
        self.assertEqual(self.verify(), [])

    def verify(self) -> list[str]:
        return fm.verify(index_path=self.index_path, store_paths=self.stores, roots=self.roots, stamp=self.stamp)

    def test_latest_run(self) -> None:
        self.assertEqual(fm.latest_run(self.index_path), self.stamp)


class VerifyHasTeeth(MigrationCase):
    """Each mutant breaks one promise; verify must name it."""

    def setUp(self) -> None:
        super().setUp()
        self.stamp = self.apply()

    def verify(self) -> list[str]:
        return fm.verify(index_path=self.index_path, store_paths=self.stores, roots=self.roots, stamp=self.stamp)

    def test_a_dropped_store_hit(self) -> None:
        data = self.store("hold")
        del data["thirdparty:Dan/Kick"]
        self.write_store("hold", data)
        self.assertTrue(any("hold hit dropped" in p and "Kick" in p for p in self.verify()), self.verify())

    def test_a_changed_value(self) -> None:
        data = self.store("normalization")
        data["factory:Bass/Acid"]["user_trim_db"] = -7.8
        self.write_store("normalization", data)
        self.assertTrue(any("user_trim_db" in p for p in self.verify()), self.verify())

    def test_a_favorite_gone_from_the_index(self) -> None:
        raw = json.loads(self.index_path.read_text())
        del raw["entries"]["factory:Keys/Rhodes"]
        self.index_path.write_text(json.dumps(raw))
        self.assertTrue(any("no longer a favorite" in p for p in self.verify()))

    def test_a_target_file_gone(self) -> None:
        (self.repo / USER / "Tuned Patches" / "Duduk.fxp").unlink()
        self.assertTrue(any("not in the library" in p and "Duduk" in p for p in self.verify()))

    def test_a_legacy_index(self) -> None:
        raw = json.loads(self.index_path.read_text())
        raw["version"] = 1
        self.index_path.write_text(json.dumps(raw))
        self.assertTrue(any("version 1" in p for p in self.verify()))

    def test_an_unfinished_run(self) -> None:
        run = fm.run_dir_for(self.index_path, self.stamp)
        m = json.loads((run / fm.MANIFEST).read_text())
        m["state"] = "started"
        (run / fm.MANIFEST).write_text(json.dumps(m))
        self.assertTrue(any("did not finish" in p for p in self.verify()))

    def test_unknown_run(self) -> None:
        self.assertTrue(fm.verify(index_path=self.index_path, store_paths=self.stores, roots=self.roots, stamp="nope"))


class MutantMigrations(MigrationCase):
    """Break the migration itself; the checks that guard it must fail."""

    def run_broken(self, **patches) -> list[str]:
        with contextlib.ExitStack() as stack:
            for name, value in patches.items():
                stack.enter_context(mock.patch.object(fm, name, value))
            stamp = self.apply()
        return fm.verify(index_path=self.index_path, store_paths=self.stores, roots=self.roots, stamp=stamp)

    def test_target_wins_instead_of_the_favorite(self) -> None:
        problems = self.run_broken(_merge_entry=lambda target, copy: ({**copy, **(target or {})}, []))
        self.assertTrue(any("user_trim_db" in p for p in problems), problems)

    def test_no_re_keying(self) -> None:
        orig = fm.build_plan

        def no_rekey(**kw):
            plan = orig(**kw)
            for spec in plan.stores.values():
                spec["changed"] = False
            return plan

        with mock.patch.object(fm, "build_plan", no_rekey):
            plan = fm.build_plan(**self.kw())
        stamp = self.apply(plan)
        problems = fm.verify(index_path=self.index_path, store_paths=self.stores, roots=self.roots, stamp=stamp)
        self.assertTrue(any("hit dropped" in p for p in problems), problems)

    def test_the_clean_run_is_clean(self) -> None:
        self.assertEqual(self.run_broken(), [])


class Undo(MigrationCase):
    def test_restores_everything_and_a_second_undo_does_nothing(self) -> None:
        before_files = digest(self.repo)
        before_stores = {k: p.read_bytes() for k, p in self.stores.items()}
        before_index = self.index_path.read_bytes()
        base_tree = git(self.repo, "rev-parse", "HEAD^{tree}")
        stamp = self.apply()
        self.assertNotEqual(digest(self.repo), before_files)
        notes = fm.undo(repo=self.repo, index_path=self.index_path, stamp=stamp)
        self.assertTrue(notes)
        self.assertEqual(digest(self.repo), before_files)
        self.assertEqual(git(self.repo, "rev-parse", "HEAD^{tree}"), base_tree)
        self.assertEqual({k: p.read_bytes() for k, p in self.stores.items()}, before_stores)
        self.assertEqual(self.index_path.read_bytes(), before_index)
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")
        again = fm.undo(repo=self.repo, index_path=self.index_path, stamp=stamp)
        self.assertIn("already undone", again[0])
        self.assertEqual(digest(self.repo), before_files)

    def test_migrating_again_after_an_undo_works(self) -> None:
        stamp = self.apply()
        fm.undo(repo=self.repo, index_path=self.index_path, stamp=stamp)
        second = self.apply()
        self.assertIsNotNone(second)
        self.assertNotEqual(second, stamp)
        fm.verify(index_path=self.index_path, store_paths=self.stores, roots=self.roots, stamp=second)

    def test_refuses_over_uncommitted_changes(self) -> None:
        stamp = self.apply()
        (self.repo / USER / "Tuned Patches" / "Duduk.fxp").write_bytes(b"edited since")
        with self.assertRaises(LibraryError):
            fm.undo(repo=self.repo, index_path=self.index_path, stamp=stamp)
        self.assertEqual((self.repo / USER / "Tuned Patches" / "Duduk.fxp").read_bytes(), b"edited since")

    def test_unknown_run(self) -> None:
        with self.assertRaises(LibraryError):
            fm.undo(repo=self.repo, index_path=self.index_path, stamp="nope")


class Refusals(MigrationCase):
    def test_an_untracked_copy_is_refused(self) -> None:
        (self.fav_dir / "New.fxp").write_bytes(b"stock-cloud")  # not committed
        plan = self.plan()
        self.assertTrue(any("New.fxp" in e and "not tracked" in e for e in plan.errors), plan.errors)
        before = self.tree()
        with self.assertRaises(LibraryError):
            self.apply(plan)
        self.assertEqual(self.tree(), before)

    def test_a_modified_copy_is_refused(self) -> None:
        (self.fav_dir / "Acid.fxp").write_bytes(b"stock-acid")  # same bytes: not modified
        self.assertEqual(self.plan().errors, [])
        (self.fav_dir / "Kick.fxp").write_bytes(b"stock-kick")
        (self.fav_dir / "Duduk.fxp").write_bytes(b"tuned-duduk-v2")
        plan = self.plan()
        self.assertTrue(any("Duduk.fxp" in e and "uncommitted" in e for e in plan.errors), plan.errors)

    def test_staged_changes_elsewhere_are_refused(self) -> None:
        (self.repo / "notes.txt").write_text("x")
        git(self.repo, "add", "notes.txt")
        before = self.tree()
        with self.assertRaises(LibraryError):
            self.apply()
        self.assertEqual(self.tree(), before)
        self.assertFalse((self.home / fm.BACKUP_DIR_NAME).exists())

    def test_tuned_destination_exists(self) -> None:
        self.put(f"{USER}/Tuned Patches/Duduk.fxp", b"already here")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "x")
        self.assertTrue(any("already exists" in e for e in self.plan().errors))

    def test_a_resave_with_no_stock_namesake(self) -> None:
        plan = self.plan(resave_names=("My Own",))
        self.assertTrue(any("My Own" in e and "stock patches" in e for e in plan.errors), plan.errors)

    def test_a_resave_with_two_stock_namesakes(self) -> None:
        self.put(f"{THIRD}/Other/Bowed String.fxp", b"another")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "x")
        self.assertTrue(any("Bowed String" in e for e in self.plan().errors))

    def test_a_tuned_name_found_twice_outside(self) -> None:
        self.put(f"{USER}/Elsewhere/Glassy Wind.fxp", b"copy 2")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "x")
        self.assertTrue(any("Glassy Wind" in e and "found 2 times" in e for e in self.plan().errors))

    def test_a_run_directory_that_exists_is_not_reused(self) -> None:
        plan = self.plan()
        run = fm.run_dir_for(self.index_path, "20260101T000000000000Z")
        run.mkdir(parents=True)
        before = self.tree()
        with self.assertRaises(LibraryError):
            fm.apply_plan(plan, repo=self.repo, index_path=self.index_path, store_paths=self.stores,
                          stamp="20260101T000000000000Z")
        self.assertEqual(self.tree(), before)

    def test_a_user_root_outside_the_checkout(self) -> None:
        other = self.root / "elsewhere" / "Patches"
        other.mkdir(parents=True)
        with self.assertRaises(LibraryError):
            self.plan(roots=[self.repo / FACTORY, self.repo / THIRD, other])


class OtherShapes(MigrationCase):
    def test_two_copies_of_one_patch_make_one_favorite(self) -> None:
        self.put(f"{USER}/{QA}/Gig/Acid again.fxp", b"stock-acid")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "x")
        stamp = self.apply()
        raw = json.loads(self.index_path.read_text())
        self.assertEqual(list(raw["entries"]).count("factory:Bass/Acid"), 1)
        self.assertEqual(fm.verify(index_path=self.index_path, store_paths=self.stores, roots=self.roots, stamp=stamp), [])
        self.assertEqual([p.name for p in (self.repo / USER / QA).rglob("*.fxp")], ["My Own.fxp"])

    def test_no_stores_and_no_index_at_all(self) -> None:
        for p in self.stores.values():
            p.unlink()
        self.index_path.unlink()
        plan = self.plan()
        self.assertEqual(plan.errors, [])
        self.assertIsNotNone(self.apply(plan))
        raw = json.loads(self.index_path.read_text())
        self.assertIn("factory:Bass/Acid", raw["entries"])
        self.assertFalse(any(p.exists() for p in self.stores.values()))

    def test_an_unreadable_store_changes_nothing(self) -> None:
        self.stores["hold"].write_text("{not json")
        before = self.tree()
        with self.assertRaises(LibraryError):
            self.plan()
        self.assertEqual(self.tree(), before)

    def test_an_empty_favorites_folder_only_upgrades_the_index(self) -> None:
        git(self.repo, "rm", "-rq", f"{USER}/{QA}")
        git(self.repo, "commit", "-q", "-m", "empty")
        plan = self.plan(tuned_names=())
        self.assertEqual(plan.file_ops, [])
        self.assertTrue(plan.index_changed)
        self.assertIsNotNone(self.apply(plan))
        self.assertEqual(json.loads(self.index_path.read_text())["version"], 2)
        self.assertTrue(self.plan(tuned_names=()).empty)


class Cli(MigrationCase):
    def run_cli(self, *argv: str) -> subprocess.CompletedProcess:
        env = dict(
            os.environ,
            MPE_PERSONAL_REPO=str(self.repo),
            MPE_HOME=str(self.home),
            MPE_SURGE_RESOURCES=str(self.root / "res"),
            MPE_SURGE_DOCS=str(self.link_parent),
            MPE_FAVORITES_NAME=QA,
            MPE_FAVORITES_INDEX_FILE=str(self.index_path),
            MPE_FX_FILE=str(self.stores["fx"]),
            MPE_HOLD_FILE=str(self.stores["hold"]),
            MPE_PRESSURE_FILE=str(self.stores["pressure"]),
            MPE_NORMALIZATION_FILE=str(self.stores["normalization"]),
            GIT_CONFIG_GLOBAL="/dev/null",
            GIT_CONFIG_SYSTEM="/dev/null",
        )
        (self.root / "res").mkdir(exist_ok=True)
        for name, rel in (("patches_factory", FACTORY), ("patches_3rdparty", THIRD)):
            link = self.root / "res" / name
            if not link.exists():
                link.symlink_to(self.repo / rel)
        script = Path(__file__).resolve().parents[1] / "scripts" / "migrate-favorites-pointers.py"
        return subprocess.run(["python3", "-I", str(script), *argv], capture_output=True, text=True, env=env)

    def test_dry_run_run_verify_undo(self) -> None:
        before = self.tree()
        dry = self.run_cli("dry-run")
        self.assertEqual(dry.returncode, 0, dry.stderr + dry.stdout)
        self.assertIn("git mv", dry.stdout)
        self.assertEqual(self.tree(), before)
        run = self.run_cli("run")
        self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
        stamp = fm.latest_run(self.index_path)
        self.assertIn(f"undo:   migrate-favorites-pointers.py undo {stamp}", run.stdout)
        ver = self.run_cli("verify")
        self.assertEqual(ver.returncode, 0, ver.stderr + ver.stdout)
        again = self.run_cli("run")
        self.assertEqual(again.returncode, 0)
        self.assertIn("nothing changed", again.stdout)
        undo = self.run_cli("undo", stamp)
        self.assertEqual(undo.returncode, 0, undo.stderr + undo.stdout)
        after = {k: v for k, v in self.tree().items() if fm.BACKUP_DIR_NAME not in k}
        self.assertEqual(after, before)

    def test_verify_fails_loudly(self) -> None:
        self.run_cli("run")
        data = self.store("hold")
        data.clear()
        self.write_store("hold", data)
        ver = self.run_cli("verify")
        self.assertEqual(ver.returncode, 1)
        self.assertIn("MISMATCH", ver.stderr)

    def test_dry_run_fails_on_errors(self) -> None:
        (self.fav_dir / "New.fxp").write_bytes(b"stock-cloud")
        dry = self.run_cli("dry-run")
        self.assertEqual(dry.returncode, 1)
        self.assertIn("ERROR", dry.stdout)

    def test_no_repo(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "MPE_PERSONAL_REPO"}
        script = Path(__file__).resolve().parents[1] / "scripts" / "migrate-favorites-pointers.py"
        out = subprocess.run(["python3", "-I", str(script), "dry-run"], capture_output=True, text=True, env=env)
        self.assertEqual(out.returncode, 1)
        self.assertIn("no library checkout", out.stderr)


if __name__ == "__main__":
    unittest.main()
