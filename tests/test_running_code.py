"""The stale-process detector, including proof that it can report stale.

A checker that always says "fine" is the failure this module exists to stop —
`bench: repo at <sha>` read the checkout and so said "fine" while the pads ran
day-old code. So the tests below are not only about the happy path: the point
is that a stale process is *distinguishable* from a current one.
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "sooperlooper"))

import running_code  # noqa: E402
from running_code import repo_sha, running_code_sha, stale_source_files  # noqa: E402


class RepoShaTests(unittest.TestCase):
    def test_resolves_the_repo_holding_this_module_not_the_cwd(self) -> None:
        sha = repo_sha()
        self.assertNotEqual(sha, "unknown")
        self.assertRegex(sha, r"^[0-9a-f]{7,}$")

    def test_unknown_rather_than_a_crash_outside_a_repo(self) -> None:
        self.assertEqual(repo_sha(Path("/")), "unknown")

    def test_git_missing_is_not_fatal(self) -> None:
        with patch("running_code.subprocess.run", side_effect=OSError):
            self.assertEqual(repo_sha(), "unknown")


class StaleDetectionTests(unittest.TestCase):
    def test_nothing_is_stale_at_startup(self) -> None:
        self.assertEqual(stale_source_files(), [])

    def test_a_file_touched_after_startup_reads_as_stale(self) -> None:
        """The positive control. Without this the detector could return []
        unconditionally and every other test here would still pass — which is
        precisely how the old banner passed for eleven commits."""
        stale = stale_source_files(since=time.time() - 10_000_000)
        self.assertTrue(stale, "loaded modules older than the cutoff must register")
        self.assertTrue(all(p.endswith(".py") for p in stale))

    def test_banner_says_stale_and_names_the_remedy(self) -> None:
        with patch("running_code.stale_source_files",
                   return_value=["/x/apc_footswitch.py", "/x/loop_model.py"]):
            line = running_code_sha()
        self.assertIn("STALE", line)
        self.assertIn("apc_footswitch.py", line)
        self.assertIn("mpe-looper-session", line,
                      "the banner must say what to restart, not just complain")

    def test_banner_is_quiet_when_current(self) -> None:
        with patch("running_code.stale_source_files", return_value=[]):
            line = running_code_sha()
        self.assertNotIn("STALE", line)

    def test_many_stale_files_are_summarised_not_dumped(self) -> None:
        with patch("running_code.stale_source_files",
                   return_value=[f"/x/m{i}.py" for i in range(9)]):
            line = running_code_sha()
        self.assertIn("+6 more", line)

    def test_unreadable_files_are_skipped_not_raised(self) -> None:
        with patch("running_code.os.stat", side_effect=OSError):
            self.assertEqual(stale_source_files(since=0.0), [])
