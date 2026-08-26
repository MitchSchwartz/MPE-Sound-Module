"""Tests for the restart bench result reader (#112)."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from patch_browser.restart_bench import FRESH_WINDOW_S, read_result

OK_RESULT = """started=1000
unit.mpe-pressure-remap=ok
unit.mpe-jackd=ok
unit.surge-xt-cli=ok
patch_reload=delegated-to-browser-startup
finished=1011
result=ok
"""

PARTIAL_RESULT = """started=1000
unit.mpe-pressure-remap=ok
unit.no-such-unit=restart-failed
unit.surge-watchdog=ok
patch_reload=delegated-to-browser-startup
finished=1001
result=partial
"""

# The sequence writes as it goes, so this is a reachable state: it was
# interrupted by the very failure it was trying to repair.
HALF_WRITTEN = """started=1000
unit.mpe-pressure-remap=ok
unit.mpe-jackd=ok
"""


class RestartBenchResultTests(unittest.TestCase):
    def _write(self, text: str) -> Path:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "restart-bench.result"
        path.write_text(text, encoding="utf-8")
        return path

    def test_ok_result(self) -> None:
        res = read_result(self._write(OK_RESULT))
        assert res is not None
        self.assertTrue(res.complete)
        self.assertEqual(res.result, "ok")
        self.assertEqual(res.failed_units, [])
        self.assertEqual(res.summary(), "Everything restarted")

    def test_partial_names_the_failed_unit(self) -> None:
        """A summary that says only 'partial' is useless while something is broken."""
        res = read_result(self._write(PARTIAL_RESULT))
        assert res is not None
        self.assertEqual(res.result, "partial")
        self.assertEqual(res.failed_units, ["no-such-unit"])
        self.assertIn("no-such-unit", res.summary())

    def test_half_written_is_not_complete(self) -> None:
        res = read_result(self._write(HALF_WRITTEN))
        assert res is not None
        self.assertFalse(res.complete)
        self.assertIn("did not finish", res.summary())

    def test_missing_file_returns_none(self) -> None:
        res = read_result(Path("/nonexistent/restart-bench.result"))
        self.assertIsNone(res)

    def test_empty_file_returns_none(self) -> None:
        self.assertIsNone(read_result(self._write("   \n")))

    def test_malformed_lines_are_skipped(self) -> None:
        res = read_result(self._write("garbage\nstarted=1\nresult=ok\nfinished=2\n"))
        assert res is not None
        self.assertTrue(res.complete)

    def test_non_numeric_timestamps_do_not_raise(self) -> None:
        res = read_result(self._write("started=abc\nfinished=xyz\nresult=ok\n"))
        assert res is not None
        self.assertFalse(res.complete)

    def test_freshness_window(self) -> None:
        res = read_result(self._write(OK_RESULT))
        assert res is not None
        self.assertTrue(res.is_fresh(res.finished + 1))
        self.assertFalse(res.is_fresh(res.finished + FRESH_WINDOW_S + 1))

    def test_stale_result_is_not_fresh(self) -> None:
        """A reboot hours later must not resurrect an old toast."""
        res = read_result(self._write(OK_RESULT))
        assert res is not None
        self.assertFalse(res.is_fresh(res.finished + 86_400))

    def test_multiple_failures_are_counted(self) -> None:
        res = read_result(
            self._write(
                "started=1\nunit.a=restart-failed\nunit.b=not-ready\n"
                "unit.c=ok\nfinished=2\nresult=partial\n"
            )
        )
        assert res is not None
        self.assertEqual(res.failed_units, ["a", "b"])
        self.assertIn("2 services", res.summary())


if __name__ == "__main__":
    unittest.main()
