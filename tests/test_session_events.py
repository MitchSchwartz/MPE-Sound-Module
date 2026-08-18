"""Unit tests for patch_browser/session_events.py — Phase 2."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from patch_browser.session_events import (
    EVENT_NAMES,
    emit_event,
    event_line,
    format_event,
    parse_event_line,
    read_events,
    trim_ring_buffer,
)


class SessionEventFormatTests(unittest.TestCase):
    def test_format_known_event(self) -> None:
        ev = format_event("engine.started", detail="jack", source="test", ts=100.0)
        self.assertEqual(ev["event"], "engine.started")
        self.assertEqual(ev["ts"], 100.0)
        self.assertEqual(ev["detail"], "jack")

    def test_unknown_event_rejected(self) -> None:
        with self.assertRaises(ValueError):
            format_event("not.real")

    def test_event_line_is_one_json_object(self) -> None:
        line = event_line(format_event("buffer.changed", ts=1.0, source="t"))
        parsed = json.loads(line)
        self.assertIn("buffer.changed", EVENT_NAMES)
        self.assertEqual(parsed["event"], "buffer.changed")

    def test_parse_invalid_line_returns_none(self) -> None:
        self.assertIsNone(parse_event_line("not json"))
        self.assertIsNone(parse_event_line(""))


class SessionEventEmitTests(unittest.TestCase):
    def test_emit_appends_and_reads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            emit_event("engine.started", detail="jack", source="unit-test", ts=10.0, run=run)
            emit_event("engine.exited", detail="failed", source="unit-test", ts=11.0, run=run)
            events = read_events(run=run)
            self.assertEqual(len(events), 2)
            self.assertEqual(events[0]["event"], "engine.started")
            self.assertEqual(events[1]["event"], "engine.exited")

    def test_ring_buffer_trims(self) -> None:
        lines = [f'{{"event":"e{i}"}}' for i in range(5)]
        trimmed = trim_ring_buffer(lines, max_events=3)
        self.assertEqual(len(trimmed), 3)
        self.assertIn("e4", trimmed[-1])

    def test_filter_by_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            emit_event("engine.started", source="t", ts=1.0, run=run)
            emit_event("buffer.changed", source="t", ts=2.0, run=run)
            started = read_events(run=run, name="engine.started")
            self.assertEqual(len(started), 1)



class SessionEventReadTests(unittest.TestCase):
    def test_read_events_limit_zero_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            emit_event("engine.started", source="t", ts=1.0, run=run)
            self.assertEqual(read_events(run=run, limit=0), [])


if __name__ == "__main__":
    unittest.main()
