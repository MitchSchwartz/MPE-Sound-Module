"""Unit tests for shutdown measurement helpers."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from patch_browser.shutdown_trace import (
    begin_shutdown_session,
    log_shutdown_event,
    reset_shutdown_session,
    shutdown_splash_disabled,
)
from scripts import shutdown_measure as sm


class ShutdownTraceTests(unittest.TestCase):
    def test_log_shutdown_event_writes_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.jsonl"
            with mock.patch("patch_browser.shutdown_trace.TRACE_PATH", path):
                reset_shutdown_session()
                log_shutdown_event("test_event", foo="bar")
            lines = path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 1)
            row = json.loads(lines[0])
            self.assertEqual(row["event"], "test_event")
            self.assertEqual(row["foo"], "bar")
            self.assertIn("ts_epoch", row)

    def test_elapsed_s_after_session_begin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.jsonl"
            with mock.patch("patch_browser.shutdown_trace.TRACE_PATH", path):
                reset_shutdown_session()
                begin_shutdown_session("test")
                log_shutdown_event("step_two")
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(rows[0]["event"], "shutdown_session_begin")
            self.assertIn("elapsed_s", rows[0])
            self.assertEqual(rows[0]["elapsed_s"], 0.0)
            self.assertIn("elapsed_s", rows[1])

    def test_shutdown_splash_disabled_env(self) -> None:
        with mock.patch.dict("os.environ", {"MPE_SHUTDOWN_SKIP_SPLASH": "1"}):
            self.assertTrue(shutdown_splash_disabled())
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertFalse(shutdown_splash_disabled())


class ShutdownMeasureParserTests(unittest.TestCase):
    def test_build_stop_spans_computes_duration(self) -> None:
        t0 = datetime(2026, 8, 2, 16, 0, 0)
        t1 = datetime(2026, 8, 2, 16, 0, 12)
        lines = [
            sm.JournalLine(t0, "surge-xt-cli.service", "Stopping Surge XT CLI Synthesizer (Headless)..."),
            sm.JournalLine(t1, "surge-xt-cli.service", "Stopped Surge XT CLI Synthesizer (Headless)."),
        ]
        spans = sm._build_stop_spans(lines)
        self.assertEqual(len(spans), 1)
        self.assertAlmostEqual(spans[0].duration_s or 0.0, 12.0)

    def test_build_stop_spans_records_timeout_note(self) -> None:
        t0 = datetime(2026, 8, 2, 16, 0, 0)
        lines = [
            sm.JournalLine(t0, "touch-patch-browser.service", "Stopping Touch Patch Browser UI..."),
            sm.JournalLine(
                datetime(2026, 8, 2, 16, 0, 10),
                "touch-patch-browser.service",
                "touch-patch-browser.service: Timed out stopping.",
            ),
        ]
        spans = sm._build_stop_spans(lines)
        self.assertEqual(len(spans), 1)
        self.assertTrue(any("Timed out" in n for n in spans[0].notes))


if __name__ == "__main__":
    unittest.main()
