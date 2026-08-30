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
exit ${RESTART_RC:-0}
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

        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        systemctl = self.bin / "systemctl"
        systemctl.write_text(FAKE_SYSTEMCTL)
        systemctl.chmod(0o755)

    def _run(self, *, restart_rc: int, active_after: str):
        env = dict(os.environ)
        env["PATH"] = f"{self.bin}:{env['PATH']}"
        env["RESTART_RC"] = str(restart_rc)
        env["ACTIVE_AFTER"] = active_after
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


if __name__ == "__main__":
    unittest.main()
