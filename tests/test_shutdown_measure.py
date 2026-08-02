"""Unit tests for shutdown measurement helpers."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from patch_browser.shutdown_trace import log_shutdown_event
from scripts import shutdown_measure as sm


class ShutdownTraceTests(unittest.TestCase):
    def test_log_shutdown_event_writes_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.jsonl"
            with mock.patch("patch_browser.shutdown_trace.TRACE_PATH", path):
                log_shutdown_event("test_event", foo="bar")
            lines = path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 1)
            row = json.loads(lines[0])
            self.assertEqual(row["event"], "test_event")
            self.assertEqual(row["foo"], "bar")
            self.assertIn("ts_epoch", row)


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
