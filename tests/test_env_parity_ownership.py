"""One owner for what is set in `/etc/mpe/mpe.env`.

Two scripts write that file. `bootstrap-pi5-looper.sh` sets the looper keys and
actively REMOVES a list of dead ones; `apply-player-env-parity.sh` merges
`config/platform/player-env-parity.<board>.env` into the same file afterwards.
Nothing made them agree, and they did not: the pi5 parity file set
`MPE_SL_LOOPS=16` against the bootstrap's 15, and reinstated three of the four
keys the bootstrap removes — including `MPE_SL_SCRATCH_LOOP`, which still has
live readers and makes track 15 disappear from the surface.

That is not a typo to correct once. It is two owners of one file with no seam
between them, so these tests read the bootstrap itself as the authority rather
than hardcoding a list: adding a `_remove_env_key` line there puts the key
under the rule automatically.

Every check carries a positive control, so none can pass by finding nothing.
"""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BOOTSTRAP = REPO / "scripts" / "bootstrap-pi5-looper.sh"
PARITY_DIR = REPO / "config" / "platform"

#: `KEY=value` at the start of a line — i.e. actually set, not commented out.
SET_KEY = re.compile(r"^([A-Z_][A-Z0-9_]*)=(.*)$", re.MULTILINE)


def parity_files() -> list[Path]:
    return sorted(PARITY_DIR.glob("player-env-parity*.env"))


def removed_by_bootstrap() -> set[str]:
    return set(re.findall(r"^_remove_env_key\s+(\S+)", BOOTSTRAP.read_text(), re.MULTILINE))


def bootstrap_sets() -> dict[str, str]:
    return dict(re.findall(r"^_ensure_env_kv\s+(\S+)\s+(\S+)", BOOTSTRAP.read_text(), re.MULTILINE))


def keys_set_in(path: Path) -> dict[str, str]:
    return {k: v.strip() for k, v in SET_KEY.findall(path.read_text())}


def has_a_reader(key: str) -> bool:
    """Is this key read anywhere outside the env files that declare it?"""
    out = subprocess.run(
        ["git", "grep", "-l", key, "--", "scripts", "patch_browser", "config"],
        cwd=REPO, capture_output=True, text=True,
    ).stdout.split()
    return any(
        not f.startswith("config/platform/player-env-parity")
        and not f.endswith("mpe.env.example")
        for f in out
    )


class BootstrapIsTheAuthorityTests(unittest.TestCase):

    def test_the_scan_finds_the_bootstraps_own_lists(self) -> None:
        """Positive control: if these come back empty every test below is vacuous."""
        self.assertIn("MPE_SL_SCRATCH_LOOP", removed_by_bootstrap())
        self.assertIn("MPE_SL_LOOPS", bootstrap_sets())
        self.assertTrue(parity_files(), "no parity files found to check")

    def test_no_parity_file_reinstates_a_key_the_bootstrap_removes(self) -> None:
        removed = removed_by_bootstrap()
        offenders = [
            f"{p.name}:{k}"
            for p in parity_files()
            for k in keys_set_in(p)
            if k in removed
        ]
        self.assertEqual(
            offenders, [],
            "these are merged into /etc/mpe/mpe.env AFTER the bootstrap deletes "
            "them, so the parity pass silently undoes the cleanup — and the keys "
            "persist across deploys",
        )

    def test_no_parity_file_contradicts_a_value_the_bootstrap_sets(self) -> None:
        fixed = bootstrap_sets()
        clashes = [
            f"{p.name}:{k}={v} but bootstrap sets {fixed[k]}"
            for p in parity_files()
            for k, v in keys_set_in(p).items()
            if k in fixed and v != fixed[k] and "pi5" in p.name
        ]
        self.assertEqual(
            clashes, [],
            "same file, two writers, later one wins — and which runs last is an "
            "ordering nobody re-checks",
        )

    def test_no_parity_file_sets_a_key_nothing_reads(self) -> None:
        """A dead key set as live is indistinguishable from a live one."""
        dead = [
            f"{p.name}:{k}"
            for p in parity_files()
            for k in keys_set_in(p)
            if k.startswith("MPE_") and not has_a_reader(k)
        ]
        self.assertEqual(dead, [], "no code reads these; they document a system "
                                   "that no longer exists")

    def test_the_reader_check_can_tell_the_difference(self) -> None:
        """Positive control for the check above."""
        self.assertTrue(has_a_reader("MPE_SL_LOOPS"))
        self.assertFalse(has_a_reader("MPE_SL_A_KEY_THAT_IS_NOT_REAL"))


class LoopCountTests(unittest.TestCase):

    def test_no_parity_file_asks_for_more_loops_than_exist(self) -> None:
        """`sl_limits.MAX_USABLE_LOOPS` is MEASURED, 2026-08-27.

        Index 15 is a phantom: it answers `get` with plausible defaults and
        discards every `set`, so asking for 16 buys a track that reads healthy
        and ignores commands. That cost a morning once already.
        """
        import sys
        sys.path.insert(0, str(REPO / "scripts" / "sooperlooper"))
        from sl_limits import MAX_USABLE_LOOPS

        over = [
            f"{p.name}:MPE_SL_LOOPS={v} > {MAX_USABLE_LOOPS}"
            for p in parity_files()
            for k, v in keys_set_in(p).items()
            if k == "MPE_SL_LOOPS" and int(v) > MAX_USABLE_LOOPS
        ]
        self.assertEqual(over, [])


if __name__ == "__main__":
    unittest.main()
