"""T3a — periodic loops must not fork JACK-client probes or journalctl."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "lib"))

from periodic_loop_lint import lint_file, lint_modules, lint_source  # noqa: E402


class PeriodicLoopLintTests(unittest.TestCase):
    def test_production_modules_have_no_jack_or_journal_forks_in_loops(self) -> None:
        findings = lint_modules()
        self.assertEqual(
            [],
            findings,
            "\n".join(f"{f.path}:{f.line} {f.detail}" for f in findings),
        )

    def test_deliberate_fork_in_snippet_fails(self) -> None:
        bad = """
while True:
    subprocess.run(["jack_lsp", "-c"])
"""
        findings = lint_source(bad, path="bad.py")
        self.assertTrue(findings, "jack_lsp in while True must be flagged")
        self.assertIn("subprocess", findings[0].detail)

    def test_deliberate_journalctl_in_loop_fails(self) -> None:
        bad = """
while not stop.is_set():
    journalctl("-u", "mpe-jackd.service")
"""
        findings = lint_source(bad, path="bad.py")
        self.assertTrue(findings)

    def test_deliberate_journalctl_two_calls_deep_fails(self) -> None:
        bad = """
def poll_xruns():
    journalctl("-u", "mpe-jackd.service")

def collect_health():
    poll_xruns()

while not stop.is_set():
    collect_health()
"""
        findings = lint_source(bad, path="bad_nested.py")
        self.assertTrue(
            findings,
            "journalctl two calls deep from periodic loop must be flagged",
        )
        self.assertIn("journalctl", findings[0].detail)

    def test_meter_file_read_in_loop_passes(self) -> None:
        ok = """
while True:
    path.read_text()
    time.sleep(1)
"""
        self.assertEqual([], lint_source(ok, path="ok.py"))

    def test_sl_watchdog_module_passes(self) -> None:
        findings = lint_file(REPO / "scripts" / "sooperlooper" / "sl-watchdog.py")
        self.assertEqual([], findings)


if __name__ == "__main__":
    unittest.main()
