"""The deploy gate: only a commit CI has judged green reaches the appliance.

The GitHub API is replaced by a fake fetcher; the clock and sleep are fakes
too, so the wait path runs in no time. What is tested is the DECISION and the
EXIT CODE, because the caller is shell and reads nothing else.
"""

from __future__ import annotations

import unittest
import urllib.error

from scripts import ci_gate
from scripts.ci_gate import GATED, PENDING, REFUSED, UNREACHABLE, evaluate, gate, parse_repo

REQ = ("unittest", "engine", "shell-tests")


def _run(name, status="completed", conclusion="success", run_id=1):
    return {"id": run_id, "name": name, "status": status, "conclusion": conclusion}


def _payload(*runs):
    return {"total_count": len(runs), "check_runs": list(runs)}


class ParseRepo(unittest.TestCase):
    def test_https_and_ssh_remotes(self) -> None:
        self.assertEqual(parse_repo("https://github.com/Owner/Repo.git"), "Owner/Repo")
        self.assertEqual(parse_repo("https://github.com/Owner/Repo"), "Owner/Repo")
        self.assertEqual(parse_repo("git@github.com:Owner/Repo.git\n"), "Owner/Repo")
        self.assertIsNone(parse_repo("https://gitlab.com/Owner/Repo.git"))


class Evaluate(unittest.TestCase):
    def test_all_three_green_passes(self) -> None:
        verdict, _ = evaluate(_payload(*(_run(n) for n in REQ)), REQ)
        self.assertEqual(verdict, ci_gate.PASS)

    def test_a_missing_engine_job_refuses(self) -> None:
        """The engine job is the reason the gate exists. A workflow that
        quietly dropped it would pass on the double alone."""
        verdict, lines = evaluate(_payload(_run("unittest"), _run("shell-tests")), REQ)
        self.assertEqual(verdict, ci_gate.REFUSE)
        self.assertIn("engine: no check run for this commit", lines)

    def test_a_failed_job_refuses(self) -> None:
        verdict, _ = evaluate(
            _payload(_run("unittest"), _run("engine", conclusion="failure"), _run("shell-tests")), REQ
        )
        self.assertEqual(verdict, ci_gate.REFUSE)

    def test_a_cancelled_job_refuses(self) -> None:
        """The workflow cancels superseded runs. Cancelled is not green."""
        verdict, _ = evaluate(
            _payload(_run("unittest"), _run("engine", conclusion="cancelled"), _run("shell-tests")), REQ
        )
        self.assertEqual(verdict, ci_gate.REFUSE)

    def test_a_job_in_progress_waits(self) -> None:
        verdict, _ = evaluate(
            _payload(_run("unittest"), _run("engine", status="in_progress", conclusion=None),
                     _run("shell-tests")), REQ
        )
        self.assertEqual(verdict, ci_gate.WAIT)

    def test_a_failure_beats_a_job_still_running(self) -> None:
        """No point waiting on the rest once one job is red."""
        verdict, _ = evaluate(
            _payload(_run("unittest", conclusion="failure"),
                     _run("engine", status="in_progress", conclusion=None),
                     _run("shell-tests")), REQ
        )
        self.assertEqual(verdict, ci_gate.REFUSE)

    def test_a_rerun_counts_by_its_newest_run(self) -> None:
        verdict, _ = evaluate(
            _payload(_run("unittest"), _run("shell-tests"),
                     _run("engine", conclusion="failure", run_id=10),
                     _run("engine", conclusion="success", run_id=11)), REQ
        )
        self.assertEqual(verdict, ci_gate.PASS)
        verdict, _ = evaluate(
            _payload(_run("unittest"), _run("shell-tests"),
                     _run("engine", conclusion="success", run_id=10),
                     _run("engine", conclusion="failure", run_id=11)), REQ
        )
        self.assertEqual(verdict, ci_gate.REFUSE)

    def test_a_commit_github_never_saw_refuses(self) -> None:
        verdict, _ = evaluate(_payload(), REQ)
        self.assertEqual(verdict, ci_gate.REFUSE)


class Gate(unittest.TestCase):
    def _gate(self, payloads, *, wait_s=900.0):
        """`payloads` is what successive fetches return (the last repeats)."""
        calls = []
        clock = [0.0]
        logs = []

        def fetch(repo, sha):
            calls.append((repo, sha))
            item = payloads[min(len(calls) - 1, len(payloads) - 1)]
            if isinstance(item, Exception):
                raise item
            return item

        def sleep(s):
            clock[0] += s

        rc = gate(repo="o/r", sha="abc1234", required=REQ, wait_s=wait_s, poll_s=20.0,
                  fetch=fetch, sleep=sleep, now=lambda: clock[0], log=logs.append)
        return rc, calls, logs

    def test_green_is_gated_on_the_first_ask(self) -> None:
        rc, calls, logs = self._gate([_payload(*(_run(n) for n in REQ))])
        self.assertEqual(rc, GATED)
        self.assertEqual(len(calls), 1)
        self.assertTrue(logs[-1].startswith("ci-gate: PASS"))

    def test_a_run_in_progress_is_waited_for_then_gated(self) -> None:
        running = _payload(_run("unittest"), _run("shell-tests"),
                           _run("engine", status="in_progress", conclusion=None))
        green = _payload(*(_run(n) for n in REQ))
        rc, calls, _ = self._gate([running, running, green])
        self.assertEqual(rc, GATED)
        self.assertEqual(len(calls), 3)

    def test_a_run_still_pending_at_the_deadline_refuses(self) -> None:
        running = _payload(_run("unittest"), _run("shell-tests"),
                           _run("engine", status="queued", conclusion=None))
        rc, calls, logs = self._gate([running], wait_s=60.0)
        self.assertEqual(rc, PENDING)
        self.assertGreaterEqual(len(calls), 3)
        self.assertTrue(logs[-1].startswith("ci-gate: REFUSE"))

    def test_a_red_run_refuses_without_waiting(self) -> None:
        red = _payload(_run("unittest"), _run("shell-tests"), _run("engine", conclusion="failure"))
        rc, calls, _ = self._gate([red])
        self.assertEqual(rc, REFUSED)
        self.assertEqual(len(calls), 1)

    def test_an_unreachable_api_refuses(self) -> None:
        """A gate that passes when it cannot see is not a gate."""
        rc, _, logs = self._gate([urllib.error.URLError("no route to host")])
        self.assertEqual(rc, UNREACHABLE)
        self.assertTrue(logs[-1].startswith("ci-gate: REFUSE"))

    def test_no_commit_refuses(self) -> None:
        for sha in ("", "unknown", "?", "HEAD"):
            rc = gate(repo="o/r", sha=sha, fetch=lambda r, s: _payload(), log=lambda _m: None)
            self.assertEqual(rc, REFUSED, sha)

    def test_exit_codes_other_than_zero_all_mean_do_not_deploy(self) -> None:
        self.assertEqual(GATED, 0)
        self.assertEqual({REFUSED, PENDING, UNREACHABLE} & {0}, set())


if __name__ == "__main__":
    unittest.main()
