"""Unit tests for patch_browser/session_snapshot.py — Phase 1."""

from __future__ import annotations

import json
import os
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
    next_seq,
    publish_snapshot,
    read_seq,
    read_snapshot,
    set_maintenance_flag,
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
                unit_active=lambda _u: True,
            )
            self.assertEqual(snap["schema"], SCHEMA_VERSION)
            self.assertEqual(snap["looper"]["policy"]["value"], "eval")
            self.assertEqual(snap["looper"]["guard"]["value"], "guarded")
            self.assertFalse(snap["engine"]["stale"])

    def test_stale_engine_when_surge_inactive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / "engine.state").write_text(
                "engine=jack\nactive=jack\nstate=ok\nupdated=990\n",
                encoding="utf-8",
            )
            snap = build_snapshot(
                now=1000.0,
                run=run,
                seq=2,
                unit_active=lambda _u: False,
            )
            self.assertTrue(snap["engine"]["stale"])
            self.assertIsNone(snap["engine"]["value"])

    def test_maintenance_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            set_maintenance_flag(run=run, source="test", deadline_s=3600.0, pid=os.getpid())
            snap = build_snapshot(now=time.time(), run=run, seq=3, unit_active=lambda _u: True)
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
            path = publish_snapshot(run=run, now=100.0, unit_active=lambda _u: True)
            loaded = read_snapshot(path, now=100.0)
            self.assertEqual(loaded["seq"], 1)
            path = publish_snapshot(run=run, now=101.0, unit_active=lambda _u: True)
            loaded = read_snapshot(path, now=101.0)
            self.assertEqual(loaded["seq"], 2)
            self.assertEqual(read_seq(run=run), 2)


class LooperPolicyTests(unittest.TestCase):
    def test_default_eval(self) -> None:
        self.assertEqual(looper_policy(looper_policy_env=""), "eval")

    def test_guard_off_by_default(self) -> None:
        self.assertEqual(looper_guard_label(looper_enabled="0"), "off")

    def test_guard_blocked_when_enabled(self) -> None:
        self.assertEqual(looper_guard_label(looper_enabled="1"), "guarded")



class SnapshotRealisticAgeTests(unittest.TestCase):
    def test_hours_old_started_not_stale_when_units_active(self) -> None:
        """Pi state files use transition-only timestamps — age must not null values."""
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            started = 1_000_000.0
            now = started + 16_200.0  # 4.5 hours later
            (run / "engine.state").write_text(
                f"engine=jack\nactive=jack\nstate=ok\nupdated={started}\n",
                encoding="utf-8",
            )
            (run / "jack.state").write_text(
                f"device=hw:0\nstarted={started}\n",
                encoding="utf-8",
            )
            (run / "surge.state").write_text(
                f"engine=jack\nstarted={started}\n",
                encoding="utf-8",
            )
            (run / "engine-reconcile.state").write_text(
                "last_restart=0\ncount=0\n",
                encoding="utf-8",
            )
            snap = build_snapshot(now=now, run=run, seq=1, unit_active=lambda _u: True)
            self.assertFalse(snap["engine"]["stale"])
            self.assertIsNotNone(snap["engine"]["value"])
            self.assertFalse(snap["jack"]["stale"])
            self.assertFalse(snap["surge"]["stale"])
            self.assertFalse(snap["reconcile"]["stale"])
            self.assertEqual(snap["mode"], "ok")


class DeriveModeTests(unittest.TestCase):
    def test_unknown_state_becomes_failed(self) -> None:
        from patch_browser.session_snapshot import derive_mode

        self.assertEqual(derive_mode({"state": "banana"}), "failed")

class SnapshotLivenessUnknownTests(unittest.TestCase):
    def test_unknown_liveness_marks_fields_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            now = 2_000_000.0
            (run / "engine.state").write_text(
                f"engine=jack\nactive=jack\nstate=ok\nupdated={now - 1}\n",
                encoding="utf-8",
            )
            (run / "jack.state").write_text(f"device=hw:0\nstarted={now - 1}\n", encoding="utf-8")
            (run / "surge.state").write_text(f"engine=jack\nstarted={now - 1}\n", encoding="utf-8")
            snap = build_snapshot(now=now, run=run, seq=1, unit_active=lambda _u: None)
            self.assertTrue(snap["engine"]["stale"])
            self.assertTrue(snap["jack"]["stale"])
            self.assertTrue(snap["surge"]["stale"])


class SnapshotSeqTests(unittest.TestCase):
    def test_write_snapshot_does_not_regress_seq(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            first = {"schema": SCHEMA_VERSION, "seq": next_seq(run=run), "published_at": 1.0, "mode": "ok"}
            second = {"schema": SCHEMA_VERSION, "seq": next_seq(run=run), "published_at": 2.0, "mode": "ok"}
            write_snapshot(first, run=run)
            write_snapshot(second, run=run)
            self.assertEqual(read_seq(run=run), 2)

class SnapshotEngineRecoveryTests(unittest.TestCase):
    def test_engine_fresh_when_surge_down_but_watchdog_active(self) -> None:
        """Surge dead + watchdog publishing recovering — engine.value must carry reason."""
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            now = 3_000_000.0
            (run / "engine.state").write_text(
                f"engine=jack\nactive=jack\nstate=recovering\nreason=surge-exited\nupdated={now - 2}\n",
                encoding="utf-8",
            )

            def _liveness(unit: str) -> bool | None:
                if unit == "surge-xt-cli":
                    return False
                if unit == "surge-watchdog":
                    return True
                return True

            snap = build_snapshot(now=now, run=run, seq=1, unit_active=_liveness)
            self.assertFalse(snap["engine"]["stale"])
            self.assertIsNotNone(snap["engine"]["value"])
            self.assertEqual(snap["engine"]["value"]["state"], "recovering")
            self.assertEqual(snap["engine"]["value"]["reason"], "surge-exited")
            self.assertEqual(snap["mode"], "recovering")


if __name__ == "__main__":
    unittest.main()
