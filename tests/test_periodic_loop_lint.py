"""T3a — periodic loops must not fork JACK-client probes or journalctl."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "lib"))

from periodic_loop_lint import (  # noqa: E402
    KNOWN_PERIODIC_MODULES,
    discover_periodic_modules,
    lint_file,
    lint_modules,
    lint_source,
)


class PeriodicLoopLintTests(unittest.TestCase):
    def test_production_modules_have_no_jack_or_journal_forks_in_loops(self) -> None:
        findings = lint_modules()
        self.assertEqual(
            [],
            findings,
            "\n".join(f"{f.path}:{f.line} {f.detail}" for f in findings),
        )

    def test_deliberate_bad_snippets_fail(self) -> None:
        cases = (
            (
                "jack_lsp subprocess in loop",
                """
while True:
    subprocess.run(["jack_lsp", "-c"])
""",
                "bad.py",
                "subprocess",
            ),
            (
                "journalctl in loop",
                """
while not stop.is_set():
    journalctl("-u", "mpe-jackd.service")
""",
                "bad.py",
                "journalctl",
            ),
            (
                "journalctl two calls deep",
                """
def poll_xruns():
    journalctl("-u", "mpe-jackd.service")

def collect_health():
    poll_xruns()

while not stop.is_set():
    collect_health()
""",
                "bad_nested.py",
                "journalctl",
            ),
        )
        for label, bad, path, needle in cases:
            with self.subTest(case=label):
                findings = lint_source(bad, path=path)
                self.assertTrue(findings, f"{label} must be flagged")
                self.assertIn(needle, findings[0].detail)

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


class ScopeAndBlindSpotTests(unittest.TestCase):
    """The 2026-08-30 finding: the guard was reading the same whether it was
    guarding or not. These fix the scope in place so it cannot narrow again."""

    def test_the_wait_loop_shape_is_recognised(self) -> None:
        """`while not X.wait(interval)` was the main loop of four of the nine
        modules this lint was pointed at, and it saw none of them."""
        src = (
            "import subprocess\n"
            "while not self._stop.wait(1.0):\n"
            "    subprocess.run(['jack_lsp'])\n"
        )
        self.assertTrue(
            lint_source(src, path="t.py"),
            "a fork in a `while not X.wait(...)` poll must be caught; matching "
            "only `.is_set()` made four monitors invisible",
        )

    def test_discovery_covers_every_module_the_hand_list_named(self) -> None:
        found = discover_periodic_modules()
        missing = [m for m in KNOWN_PERIODIC_MODULES if m not in found]
        self.assertEqual(missing, [], "discovery must not lose ground the "
                                      "hand-maintained tuple already held")

    def test_discovery_reaches_the_busiest_loop_in_the_system(self) -> None:
        """The bench idles at a measured ~485 Hz and was not in the tuple."""
        self.assertIn("scripts/sooperlooper-apc-bench.py", discover_periodic_modules())

    def test_discovery_is_not_trivially_everything(self) -> None:
        """Positive control: a scope of "every file" would also pass the two
        checks above while meaning nothing."""
        found = discover_periodic_modules()
        self.assertNotIn("scripts/lib/periodic_loop_lint.py", found)
        self.assertGreater(len(found), len(KNOWN_PERIODIC_MODULES))
