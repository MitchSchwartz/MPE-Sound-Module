"""Unit tests for patch_browser/session_snapshot.py — Phase 1."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path

from patch_browser import session_snapshot
from patch_browser.session_snapshot import (
    SCHEMA_VERSION,
    STALE_THRESHOLD_S,
    build_services,
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

class SnapshotServicesTests(unittest.TestCase):
    def test_build_includes_services_with_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / "engine.state").write_text(
                "engine=jack\nactive=jack\nstate=ok\nupdated=999\n",
                encoding="utf-8",
            )
            snap = build_snapshot(
                now=1000.0,
                run=run,
                seq=1,
                unit_active=lambda u: u == "mpe-jackd",
                unit_enabled=lambda u: "enabled",
            )
            self.assertIn("mpe-jackd", snap["services"])
            self.assertEqual(snap["services"]["mpe-jackd"]["active"], "active")
            self.assertEqual(snap["services"]["mpe-jackd"]["enabled"], "enabled")
            self.assertFalse(snap["services"]["mpe-jackd"]["stale"])

    def test_skips_not_installed_units(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / "engine.state").write_text(
                "engine=jack\nactive=jack\nstate=ok\nupdated=999\n",
                encoding="utf-8",
            )

            def _enabled(unit: str) -> str | None:
                return "not-found" if unit == "usb-audio-gadget" else "enabled"

            snap = build_snapshot(
                now=1000.0,
                run=run,
                seq=1,
                unit_active=lambda _u: True,
                unit_enabled=_enabled,
            )
            self.assertNotIn("usb-audio-gadget", snap["services"])

    def test_skips_not_found_before_is_active(self) -> None:
        calls: list[str] = []

        def _active(unit: str) -> bool | None:
            calls.append(unit)
            return False

        services = build_services(
            unit_active=_active,
            unit_enabled=lambda u: "not-found" if u == "patch-browser" else "enabled",
        )
        self.assertNotIn("patch-browser", services)
        self.assertNotIn("patch-browser", calls)

    def test_omit_services_when_include_services_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / "engine.state").write_text(
                "engine=jack\nactive=jack\nstate=ok\nupdated=999\n",
                encoding="utf-8",
            )
            snap = build_snapshot(
                now=1000.0,
                run=run,
                seq=1,
                include_services=False,
                unit_active=lambda _u: True,
            )
            self.assertNotIn("services", snap)


class SnapshotLivenessCostTests(unittest.TestCase):
    """Work order task 4 — batched liveness, and cached fields carry their age."""

    def setUp(self) -> None:
        session_snapshot._reset_probe_cache()

    tearDown = setUp

    def test_enabled_ttl_is_longer_than_active_ttl(self) -> None:
        """`enabled` is configuration; sampling it at publish rate was the bug."""
        self.assertGreater(
            session_snapshot.ENABLED_PROBE_TTL_S,
            session_snapshot.SERVICES_PROBE_TTL_S,
        )

    def test_batched_fork_parses_one_line_per_unit(self) -> None:
        units = ("a", "b", "c")
        completed = subprocess.CompletedProcess([], 0, "active\ninactive\nactivating\n", "")
        with mock.patch.object(session_snapshot.subprocess, "run", return_value=completed):
            self.assertEqual(
                session_snapshot._fork_active_states(units),
                {"a": True, "b": False, "c": None},
            )

    def test_batched_fork_refuses_a_short_answer(self) -> None:
        """Fewer lines than units means the mapping is unknown, not partially true."""
        completed = subprocess.CompletedProcess([], 0, "active\n", "")
        with mock.patch.object(session_snapshot.subprocess, "run", return_value=completed):
            self.assertEqual(
                session_snapshot._fork_active_states(("a", "b")),
                {"a": None, "b": None},
            )

    def test_falls_back_to_one_fork_when_dbus_is_unusable(self) -> None:
        with mock.patch.object(session_snapshot, "_dbus_active_states", return_value=None):
            with mock.patch.object(
                session_snapshot, "_fork_active_states", return_value={"a": True}
            ) as forked:
                states, source = session_snapshot.batched_active_states(("a",))
        self.assertEqual((states, source), ({"a": True}, "fork"))
        forked.assert_called_once()

    def test_dbus_result_is_used_without_forking(self) -> None:
        with mock.patch.object(
            session_snapshot, "_dbus_active_states", return_value={"a": False}
        ):
            with mock.patch.object(session_snapshot, "_fork_active_states") as forked:
                states, source = session_snapshot.batched_active_states(("a",))
        self.assertEqual((states, source), ({"a": False}, "dbus"))
        forked.assert_not_called()

    def test_cached_fields_carry_their_age(self) -> None:
        """A cached judgement must be able to say how old it is."""
        units = session_snapshot.STATUS_SERVICE_UNITS
        with mock.patch.object(
            session_snapshot,
            "batched_active_states",
            return_value=({u: True for u in units}, "dbus"),
        ):
            with mock.patch.object(
                session_snapshot,
                "batched_enabled_states",
                return_value=({u: "enabled" for u in units}, "dbus"),
            ):
                services = session_snapshot.build_services()
        self.assertTrue(services)
        for unit, entry in services.items():
            self.assertEqual(entry["active_source"], "dbus", unit)
            self.assertEqual(entry["enabled_source"], "dbus", unit)
            self.assertIn("active_age_s", entry, unit)
            self.assertIn("enabled_age_s", entry, unit)
            self.assertGreaterEqual(entry["active_age_s"], 0.0)

    def test_active_probe_is_batched_not_per_unit(self) -> None:
        """The 202 ms -> 7 ms win is the batching; one call for every unit."""
        units = session_snapshot.STATUS_SERVICE_UNITS
        with mock.patch.object(
            session_snapshot,
            "batched_active_states",
            return_value=({u: True for u in units}, "dbus"),
        ) as batched:
            with mock.patch.object(
                session_snapshot,
                "batched_enabled_states",
                return_value=({u: "enabled" for u in units}, "dbus"),
            ):
                session_snapshot.build_services()
        batched.assert_called_once()

    def test_enabled_probe_is_batched_not_per_unit(self) -> None:
        """220 ms of per-unit is-enabled forks was the rest of the cold-build cost."""
        units = session_snapshot.STATUS_SERVICE_UNITS
        with mock.patch.object(
            session_snapshot,
            "batched_active_states",
            return_value=({u: True for u in units}, "dbus"),
        ):
            with mock.patch.object(
                session_snapshot,
                "batched_enabled_states",
                return_value=({u: "enabled" for u in units}, "dbus"),
            ) as batched:
                session_snapshot.build_services()
        batched.assert_called_once()

    def test_units_absent_from_dbus_reply_read_as_not_found(self) -> None:
        """A unit with no unit file must be skipped, not rendered inactive."""
        with mock.patch.object(session_snapshot, "_dbus_manager") as mgr:
            mgr.return_value.ListUnitFilesByPatterns.return_value = [
                ("/lib/systemd/system/mpe-jackd.service", "enabled"),
            ]
            states = session_snapshot._dbus_enabled_states(("mpe-jackd", "ghost-unit"))
        self.assertEqual(states, {"mpe-jackd": "enabled", "ghost-unit": "not-found"})

    def test_probe_age_is_none_before_the_probe_runs(self) -> None:
        self.assertIsNone(session_snapshot._probe_age_s("active:batch"))


if __name__ == "__main__":
    unittest.main()
