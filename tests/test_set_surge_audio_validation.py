"""The buffer validator must actually run — not merely be spelled correctly.

Regression for 2026-09-01. `set-surge-audio.sh` delegated validation to
`mpe_jack_period_is_valid`, but sourced `lib/audio-engine.sh` at line 238 while
calling the validator at line 74. Bash returned 127 for the undefined function,
so `is_valid_buffer` was false for EVERY value and the appliance answered every
buffer change with "invalid buffer size: N". 1905 tests passed, because every
test of this script asserted its TEXT: the delegation was spelled correctly and
was never once executed.

Second defect, independently fatal: `mpe_jack_periods_conf` interpolated an
empty `$MPE_MODULE_REPO` -- unset until the appliance env is sourced 40 lines
later -- yielding "/config/jack-periods.conf", unreadable, so the preset list
was empty and every period was rejected on that path too.

These are functional runs. They invoke the script.
"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "set-surge-audio.sh"
CONF = REPO / "config" / "jack-periods.conf"


def _periods() -> list[str]:
    out = []
    for line in CONF.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line.isdigit():
            out.append(line)
    return out


def _run(*args: str) -> subprocess.CompletedProcess:
    """Run the script for real. Validation precedes every side effect, so this
    reaches the gate without root, /etc/mpe, or a live graph."""
    return subprocess.run(
        [str(SCRIPT), *args],
        capture_output=True, text=True, timeout=30,
        cwd=str(REPO),
    )


class ValidatorActuallyRunsTests(unittest.TestCase):
    def test_no_period_in_the_conf_is_reported_invalid(self):
        """The exact user-visible failure: 48 and 96 rejected, then all of them."""
        self.assertTrue(_periods(), "conf parsed empty — test is not testing anything")
        for period in _periods():
            with self.subTest(period=period):
                r = _run("--buffer", period)
                self.assertNotIn(
                    f"invalid buffer size: {period}", r.stderr,
                    f"{period} is in {CONF.name} and the script rejects it",
                )

    def test_the_validator_is_defined_when_it_is_called(self):
        """127 from an undefined function is indistinguishable from 'invalid'."""
        r = _run("--buffer", "128")
        self.assertNotIn("command not found", r.stderr, r.stderr)

    def test_negative_control_a_value_outside_the_conf_is_still_rejected(self):
        """Without this, a validator that accepts everything would pass above."""
        self.assertNotIn("768", _periods(), "768 is in the conf; pick another control")
        r = _run("--buffer", "768")
        self.assertIn("invalid buffer size: 768", r.stderr)
        self.assertEqual(r.returncode, 1)

    def test_validation_precedes_the_environment_check(self):
        """If the env check ran first, none of the above could be tested at all —
        which is exactly how this bug stayed invisible."""
        bad = _run("--buffer", "768")
        self.assertIn("invalid buffer size", bad.stderr)
        self.assertNotIn("mpe.env not found", bad.stderr)


class UnreadableListIsLoudTests(unittest.TestCase):
    """A validator that cannot find its data must say so, not reject everything.

    Silently returning 'invalid' for every value is a statement about the value
    when the truth is a broken install — the 2026-09-01 defect shape: a reading
    that looks identical whether the thing works or not.
    """

    def test_missing_conf_names_itself_instead_of_blaming_the_value(self):
        r = subprocess.run(
            ["bash", "-c",
             'source scripts/lib/audio-engine.sh; '
             'MPE_JACK_PERIODS_CONF=/nonexistent/jack-periods.conf '
             'mpe_jack_period_is_valid 128'],
            capture_output=True, text=True, cwd=str(REPO), timeout=30,
        )
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("cannot read the JACK period list", r.stderr)
        self.assertIn("/nonexistent/jack-periods.conf", r.stderr)
        self.assertIn("broken install, not an invalid value", r.stderr)


class ConfIsFoundWithoutTheRepoEnvTests(unittest.TestCase):
    """MPE_MODULE_REPO is unset until the appliance env is sourced. The library
    ships beside the conf, so it must not depend on the caller's load order."""

    def test_presets_resolve_with_MPE_MODULE_REPO_unset(self):
        r = subprocess.run(
            ["bash", "-c",
             'unset MPE_MODULE_REPO; source scripts/lib/audio-engine.sh; '
             'mpe_jack_period_presets | tr "\\n" " "'],
            capture_output=True, text=True, cwd=str(REPO), timeout=30,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.split(), _periods())


if __name__ == "__main__":
    unittest.main()
