#!/usr/bin/env python3
"""Refuse a commit that has not passed CI -- the engine job included.

WHY THIS EXISTS. `mpe looper deploy` fetches origin/dev, resets the Pi's
checkout to it and restarts the session. Nothing in that path ever asked
whether the commit had passed its own tests: a push and a deploy thirty
seconds apart shipped code CI had not finished judging, and a red run on
GitHub changed nothing on the appliance. Since 2026-09-07 the suite includes
the real SooperLooper (tests/engine), the only tests that can contradict a
claim about the engine, so "passed CI" now means something about the
instrument and not only about a hand-written double.

WHAT IT DOES. Asks the GitHub check-runs API for the commit and requires one
completed, successful check run for every job named in --require (default:
the three jobs in .github/workflows/test.yml). A run still in progress is
waited for, up to --wait seconds. Anything else -- a failed job, a missing
job, a commit GitHub has never seen, an API it cannot reach -- is a refusal.
The repo is public, so no token is needed; GITHUB_TOKEN is used if set.

EXIT CODES. 0 gated (deploy). 1 refused. 2 still pending at the deadline.
3 the API could not be asked. Only 0 deploys; the caller treats 1, 2 and 3
alike, because a gate that passes when it cannot see is not a gate.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable

API = "https://api.github.com"
REQUIRED_DEFAULT: tuple[str, ...] = ("unittest", "engine", "shell-tests")
POLL_S = 20.0
WAIT_S = 900.0

GATED, REFUSED, PENDING, UNREACHABLE = 0, 1, 2, 3

PASS = "pass"
WAIT = "pending"
REFUSE = "refuse"


def parse_repo(remote_url: str) -> str | None:
    """`owner/name` from a GitHub remote URL, https or ssh, .git or not."""
    m = re.search(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", remote_url.strip())
    return f"{m.group(1)}/{m.group(2)}" if m else None


def repo_from_git(cwd: str | None = None) -> str | None:
    try:
        out = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, cwd=cwd, timeout=10, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_repo(out)


def fetch_check_runs(repo: str, sha: str, *, token: str | None = None,
                     timeout: float = 20.0) -> dict:
    req = urllib.request.Request(
        f"{API}/repos/{repo}/commits/{sha}/check-runs?per_page=100",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "mpe-ci-gate"},
    )
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def evaluate(payload: dict, required: tuple[str, ...]) -> tuple[str, list[str]]:
    """(PASS | WAIT | REFUSE, one line per required job)."""
    latest: dict[str, dict] = {}
    for run in payload.get("check_runs", []):
        name = str(run.get("name", ""))
        # A re-run adds a second check run with the same name; the newest id
        # is the one that counts.
        if name not in latest or int(run.get("id", 0)) > int(latest[name].get("id", 0)):
            latest[name] = run
    verdict = PASS
    lines: list[str] = []
    for name in required:
        run = latest.get(name)
        if run is None:
            lines.append(f"{name}: no check run for this commit")
            verdict = REFUSE
            continue
        status = str(run.get("status", ""))
        conclusion = run.get("conclusion")
        if status != "completed":
            lines.append(f"{name}: {status}")
            if verdict == PASS:
                verdict = WAIT
            continue
        if conclusion == "success":
            lines.append(f"{name}: success")
        else:
            lines.append(f"{name}: {conclusion or 'no conclusion'}")
            verdict = REFUSE
    return verdict, lines


def gate(
    *,
    repo: str,
    sha: str,
    required: tuple[str, ...] = REQUIRED_DEFAULT,
    wait_s: float = WAIT_S,
    poll_s: float = POLL_S,
    fetch: Callable[[str, str], dict] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
    log: Callable[[str], None] = print,
) -> int:
    """Poll until the commit is gated, refused, or the wait runs out."""
    if not sha or sha in ("unknown", "?", "HEAD"):
        log(f"ci-gate: REFUSE -- no commit to gate ({sha!r})")
        return REFUSED
    fetch = fetch or (lambda r, s: fetch_check_runs(r, s, token=os.environ.get("GITHUB_TOKEN")))
    deadline = now() + wait_s
    announced = False
    while True:
        try:
            payload = fetch(repo, sha)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
            log(f"ci-gate: REFUSE -- could not ask GitHub about {sha} in {repo}: {exc}")
            return UNREACHABLE
        verdict, lines = evaluate(payload, required)
        if verdict == PASS:
            log(f"ci-gate: PASS -- {sha} in {repo}: " + ", ".join(lines))
            return GATED
        if verdict == REFUSE:
            log(f"ci-gate: REFUSE -- {sha} in {repo}: " + ", ".join(lines))
            return REFUSED
        if now() >= deadline:
            log(f"ci-gate: REFUSE -- {sha} still not judged after {wait_s:.0f}s: " + ", ".join(lines))
            return PENDING
        if not announced:
            log(f"ci-gate: waiting for CI on {sha} (" + ", ".join(lines) + ")")
            announced = True
        sleep(poll_s)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--sha", required=True, help="the commit about to be deployed")
    p.add_argument("--repo", default=None, help="owner/name; default: parsed from `git remote get-url origin`")
    p.add_argument("--require", default=",".join(REQUIRED_DEFAULT),
                   help="comma-separated check-run names that must all be green")
    p.add_argument("--wait", type=float, default=WAIT_S, help="seconds to wait for a run in progress")
    p.add_argument("--poll", type=float, default=POLL_S)
    args = p.parse_args(argv)
    repo = args.repo or repo_from_git()
    if not repo:
        print("ci-gate: REFUSE -- cannot tell which GitHub repo this is (no origin remote?)")
        return REFUSED
    required = tuple(name.strip() for name in args.require.split(",") if name.strip())
    return gate(repo=repo, sha=args.sha, required=required, wait_s=args.wait, poll_s=args.poll)


if __name__ == "__main__":
    sys.exit(main())
