"""Unit tests for patch_browser/session_snapshot.py — Phase 1."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from patch_browser.session_snapshot import (
    SCHEMA_VERSION,
    STALE_THRESHOLD_S,
    build_snapshot,
    field_age_stale,
    looper_guard_label,
    looper_policy,
    publish_snapshot,
    read_seq,
    read_snapshot,
    write_snapshot,
)


class SnapshotStalenessTests(unittest.TestCase):
    def test_field_stale_when_missing_updated(self) -> None:
        now = 1000.0
        self.assertTrue(field_age_stale(0.0, now=now))

    def test_field_fresh_within_threshold(self) -> None:
        now = 1000.0
        self.assertFalse(field_age_stale(now - 1.0, now=now, threshold=STALE_THRESHOLD_S))

    def test_field_stale_beyond_threshold(self) -> None:
        now = 1000.0
        self.assertTrue(field_age_stale(now - STALE_THRESHOLD_S, now=now, threshold=STALE_THRESHOLD_S))


class SnapshotBuildTests(unittest.TestCase):
    def test_build_includes_looper_policy_and_guard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / "engine.state").write_text(
                "engine=jack\nactive=jack\nstate=ok\nlooper=off\nupdated=999\n",
                encoding="utf-8",
            )
            snap = build_snapshot(
                now=1000.0,
                run=run,
                looper_policy_env="eval",
                looper_enabled="1",
                seq=1,
            )
            self.assertEqual(snap["schema"], SCHEMA_VERSION)
            self.assertEqual(snap["looper"]["policy"]["value"], "eval")
            self.assertEqual(snap["looper"]["guard"]["value"], "guarded")
            self.assertFalse(snap["engine"]["stale"])

    def test_stale_engine_suppresses_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / "engine.state").write_text(
                "engine=jack\nactive=jack\nstate=ok\nupdated=990\n",
                encoding="utf-8",
            )
            snap = build_snapshot(now=1000.0, run=run, seq=2)
            self.assertTrue(snap["engine"]["stale"])
            self.assertIsNone(snap["engine"]["value"])

    def test_maintenance_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / "maintenance").write_text("1\n", encoding="utf-8")
            snap = build_snapshot(now=time.time(), run=run, seq=3)
            self.assertEqual(snap["mode"], "maintenance")


class SnapshotReadWriteTests(unittest.TestCase):
    def test_write_read_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            snap = build_snapshot(now=1000.0, run=run, seq=7)
            path = write_snapshot(snap, run=run)
            loaded = read_snapshot(path, now=1000.5)
            self.assertEqual(loaded["seq"], 7)
            self.assertEqual(loaded["schema"], SCHEMA_VERSION)

    def test_unknown_schema_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.snapshot.json"
            path.write_text(json.dumps({"schema": 99, "seq": 1}) + "\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                read_snapshot(path, max_schema=SCHEMA_VERSION)

    def test_publish_writes_monotonic_seq_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            publish_snapshot(run=run, now=100.0, seq=1)
            publish_snapshot(run=run, now=101.0, seq=2)
            path = publish_snapshot(run=run, now=102.0, seq=3)
            loaded = read_snapshot(path, now=102.0)
            self.assertEqual(loaded["seq"], 3)
            self.assertEqual(read_seq(run=run), 3)


class LooperPolicyTests(unittest.TestCase):
    def test_default_eval(self) -> None:
        self.assertEqual(looper_policy(looper_policy_env=""), "eval")

    def test_guard_off_by_default(self) -> None:
        self.assertEqual(looper_guard_label(looper_enabled="0"), "off")

    def test_guard_blocked_when_enabled(self) -> None:
        self.assertEqual(looper_guard_label(looper_enabled="1"), "guarded")


if __name__ == "__main__":
    unittest.main()
