"""A deploy that leaves the pads dead must not report success.

WHY THIS EXISTS. On 2026-08-30 `mpe looper deploy` printed its PASS lines and
returned 0 while `mpe-looper-session.service` crashlooped 32 times on
`TypeError: repaint_scenes() got an unexpected keyword argument 'force'`. The
new code was on disk, the SHA in the banner was correct, and the process
driving the pads was dead.

The cause was three characters of shell:

    bash restart-looper-session.sh || {
        echo "looper-deploy: WARN — looper session restart failed;" >&2
    }

A brace block's exit status is its last command's. `echo` succeeds, so the
`||` group succeeded, so `set -e` never fired and the script ran to a clean
exit. The failure was reported to a human reading stdout and to nobody else.

That is the project's recurring shape at the outermost layer: a deploy result
identical whether the instrument came back or not. A failed restart is a FAILED
deploy — worse than no deploy, because the SHA now says one thing and the
instrument does another.

WHAT THIS DOES. Runs the real `scripts/looper-deploy.sh` against a fake
`systemctl` and a fake restart script, and checks the EXIT CODE — not the log
text. Prose cannot fail a build; an exit code can.

WHAT THIS DOES NOT DO. It does not test the Pi, systemd, or the real restart
script. It tests that this script's failure paths reach the caller.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEPLOY = REPO / "scripts" / "looper-deploy.sh"

FAKE_SYSTEMCTL = """#!/usr/bin/env bash
# list-unit-files / cat: the unit exists, so the deploy must not skip.
case "$1" in
    list-unit-files|cat) exit 0 ;;
    is-active)
        # `--quiet` form asks about health at the end; bare form is the
        # "was:" banner. $ACTIVE_AFTER decides what the unit looks like.
        [ "${ACTIVE_AFTER:-yes}" = yes ] || exit 3
        echo active; exit 0 ;;
esac
exit 0
"""

FAKE_RESTART = """#!/usr/bin/env bash
# The marker says the restart was REACHED -- the gate's whole job is to keep
# it from being reached for a commit CI has not passed.
touch "${RESTART_MARKER:?}"
exit ${RESTART_RC:-0}
"""

FAKE_GATE = """#!/usr/bin/env python3
import os, sys
sys.exit(int(os.environ.get("GATE_RC", "0")))
"""


class LooperDeployExitCodeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        scripts = self.tmp / "scripts"
        scripts.mkdir()
        shutil.copy(DEPLOY, scripts / "looper-deploy.sh")
        (scripts / "looper-deploy.sh").chmod(0o755)

        restart = scripts / "restart-looper-session.sh"
        restart.write_text(FAKE_RESTART)
        restart.chmod(0o755)

        self.gate = scripts / "ci_gate.py"
        self.gate.write_text(FAKE_GATE)
        self.marker = self.tmp / "restart-reached"

        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        systemctl = self.bin / "systemctl"
        systemctl.write_text(FAKE_SYSTEMCTL)
        systemctl.chmod(0o755)

    def _run(self, *, restart_rc: int = 0, active_after: str = "yes",
             gate_rc: int = 0, gate_present: bool = True, skip_gate: bool = False):
        env = dict(os.environ)
        env["PATH"] = f"{self.bin}:{env['PATH']}"
        env["RESTART_RC"] = str(restart_rc)
        env["ACTIVE_AFTER"] = active_after
        env["GATE_RC"] = str(gate_rc)
        env["RESTART_MARKER"] = str(self.marker)
        env.pop("MPE_DEPLOY_SKIP_CI_GATE", None)
        if skip_gate:
            env["MPE_DEPLOY_SKIP_CI_GATE"] = "1"
        if not gate_present:
            self.gate.unlink()
        return subprocess.run(
            ["bash", str(self.tmp / "scripts" / "looper-deploy.sh"), "dev"],
            capture_output=True, text=True, env=env, cwd=self.tmp, timeout=60,
        )

    def test_a_healthy_restart_still_succeeds(self) -> None:
        """The positive control.

        Without it, a script that failed unconditionally would pass every
        other test in this file — a guard that reports the appliance broken
        no matter what is as useless as one that reports it fine no matter
        what, and it is the version that gets deleted in frustration.
        """
        r = self._run(restart_rc=0, active_after="yes")
        self.assertEqual(r.returncode, 0, f"clean deploy failed:\n{r.stderr}")

    def test_a_failed_restart_fails_the_deploy(self) -> None:
        r = self._run(restart_rc=1, active_after="yes")
        self.assertNotEqual(
            r.returncode, 0,
            "the restart failed and the deploy reported success — this is the "
            "2026-08-30 crashloop, exactly",
        )

    def test_a_restart_that_succeeds_then_dies_fails_the_deploy(self) -> None:
        """How a crashloop actually presents.

        `systemctl restart` returns success and the process exits milliseconds
        later, so the only honest question is asked at the END, about the unit
        rather than about the command.
        """
        r = self._run(restart_rc=0, active_after="no")
        self.assertNotEqual(
            r.returncode, 0,
            "unit inactive after a 'successful' restart and the deploy still "
            "reported success",
        )


class LooperDeployCiGateTests(LooperDeployExitCodeTests):
    """The gate keeps an unjudged commit away from the restart.

    Same rig as above; what changes is scripts/ci_gate.py's exit code and
    whether the restart script was ever reached.
    """

    def test_a_gated_commit_deploys(self) -> None:
        r = self._run(gate_rc=0)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.marker.exists(), "the restart was never reached")

    def test_a_refused_commit_fails_the_deploy_and_restarts_nothing(self) -> None:
        for rc in (1, 2, 3):
            self.marker.unlink(missing_ok=True)
            r = self._run(gate_rc=rc)
            self.assertNotEqual(r.returncode, 0, f"gate rc={rc} and the deploy succeeded")
            self.assertFalse(self.marker.exists(), f"gate rc={rc} and the restart still ran")
            self.assertIn("ORIG_HEAD", r.stderr, "the refusal must say how to put the checkout back")

    def test_a_missing_gate_fails_the_deploy(self) -> None:
        """A commit without the gate cannot be gated. Silence would be a pass."""
        r = self._run(gate_present=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(self.marker.exists())

    def test_the_gate_can_be_skipped_only_by_saying_so(self) -> None:
        r = self._run(gate_rc=1, skip_gate=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("SKIPPED", r.stdout)
        self.assertTrue(self.marker.exists())


if __name__ == "__main__":
    unittest.main()
