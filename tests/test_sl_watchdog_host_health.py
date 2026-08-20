"""Xrun rate and CPU governor: the two host-health signals the appliance lacked.

Both existed as real faults before they existed as measurements. SooperLooper
was dropping ~66 xruns/min into a log nobody read, and the CPU governor was
falling off `performance` with nothing to re-assert or report it — while the
watchdog's alarm file said `ok` throughout.
"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "sl_watchdog_hh", REPO / "scripts/sooperlooper/sl-watchdog.py")
sl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sl)


class XrunCounterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.log = self.tmp / "engine.log"

    def test_counts_only_new_bytes(self) -> None:
        """A growing log must not be re-counted from the top every 10s."""
        self.log.write_text("got xrun\ngot xrun\n")
        c = sl.XrunCounter(self.log)
        self.assertEqual((2, None), c.poll(100.0))
        self.assertEqual((0, None), c.poll(110.0), "re-counted unchanged file")
        with self.log.open("a") as fh:
            fh.write("got xrun\n")
        self.assertEqual((1, None), c.poll(120.0))

    def test_truncation_resets_offset(self) -> None:
        """A rotated or truncated log must not report a negative/garbage delta."""
        self.log.write_text("got xrun\n" * 5)
        c = sl.XrunCounter(self.log)
        c.poll(100.0)
        self.log.write_text("got xrun\n")  # truncate + one fresh event
        self.assertEqual((1, None), c.poll(110.0))

    def test_missing_log_is_reported_not_crashed(self) -> None:
        c = sl.XrunCounter(self.tmp / "absent.log")
        count, err = c.poll(100.0)
        self.assertEqual(0, count)
        self.assertIsNotNone(err)

    def test_rate_needs_a_span_then_reports_per_minute(self) -> None:
        self.log.write_text("")
        c = sl.XrunCounter(self.log)
        c.poll(0.0)
        self.assertIsNone(c.rate_per_min(0.0), "no rate from a single sample")
        with self.log.open("a") as fh:
            fh.write("got xrun\n" * 10)
        c.poll(30.0)
        self.assertEqual(20.0, c.rate_per_min(30.0))  # 10 in 30s -> 20/min


class GovernorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.alarm = self.tmp / "alarm.json"
        self.gov = self.tmp / "scaling_governor"
        for name, val in (("ALARM_FILE", self.alarm), ("GOVERNOR_PATH", self.gov),
                          ("ENGINE_LOG", self.tmp / "engine.log")):
            p = mock.patch.object(sl, name, val)
            p.start()
            self.addCleanup(p.stop)

    def _run_once(self, *, target="performance", actual="ondemand",
                  repair=(True, "ok"), extra_argv=()):
        self.gov.write_text(actual + "\n")
        graph = f"{sl.JACK_CLIENT}:common_out_1\n   system:playback_1\n" \
                f"system:playback_1\n   {sl.JACK_CLIENT}:common_out_1\n"
        snap = sl.GraphSnapshot(True, True, True, "meter")
        with mock.patch.object(sl, "GOVERNOR_TARGET", target), \
             mock.patch.object(sl, "read_graph_snapshot", return_value=snap), \
             mock.patch.object(sl, "engine_running", return_value=True), \
             mock.patch.object(sl, "repair_governor", return_value=repair) as rep, \
             mock.patch.object(sl, "Osc") as osc:
            engine = mock.MagicMock()
            engine.get.return_value = 0.0
            osc.return_value.start.return_value = engine
            with mock.patch.object(sl, "check_command_path",
                                   return_value=(sl.ALIVE, "fine")):
                sl.main(["--once", *extra_argv])
        return json.loads(self.alarm.read_text()), rep

    def test_drift_is_repaired(self) -> None:
        alarm, rep = self._run_once()
        rep.assert_called_once()
        self.assertEqual("ok", alarm["state"], "a repaired drift is not a fault")

    def test_matching_governor_is_left_alone(self) -> None:
        _alarm, rep = self._run_once(actual="performance")
        rep.assert_not_called()

    def test_unset_target_means_unmanaged(self) -> None:
        """No MPE_CPU_GOVERNOR must not nag an appliance that never opted in."""
        _alarm, rep = self._run_once(target="", actual="ondemand")
        rep.assert_not_called()

    def test_failed_repair_becomes_a_problem_not_a_silent_pass(self) -> None:
        alarm, _rep = self._run_once(repair=(False, "sudo refused"))
        self.assertNotEqual("ok", alarm["state"])
        self.assertIn("sudo refused", json.dumps(alarm))

    def test_no_repair_flag_alarms_instead_of_repairing(self) -> None:
        alarm, rep = self._run_once(extra_argv=("--no-repair",))
        rep.assert_not_called()
        self.assertNotEqual("ok", alarm["state"])

    def test_repeated_repair_escalates_to_an_alarm(self) -> None:
        """Repairing forever would hide a fight. Keep repairing, but say so."""
        graph = f"{sl.JACK_CLIENT}:common_out_1\n   system:playback_1\n" \
                f"system:playback_1\n   {sl.JACK_CLIENT}:common_out_1\n"
        # Drive the REAL continuous loop: the counter lives across cycles, so a
        # per-run --once test could never exercise the escalation at all.
        cycles = {"n": 0}

        def _stop_after_four(_seconds):
            cycles["n"] += 1
            self.gov.write_text("ondemand\n")  # something drifts it right back
            if cycles["n"] >= 4:
                raise KeyboardInterrupt

        snap = sl.GraphSnapshot(True, True, True, "meter")
        with mock.patch.object(sl, "GOVERNOR_TARGET", "performance"), \
             mock.patch.object(sl, "GOVERNOR_FIGHT_LIMIT", 2), \
             mock.patch.object(sl, "read_graph_snapshot", return_value=snap), \
             mock.patch.object(sl, "engine_running", return_value=True), \
             mock.patch.object(sl, "repair_governor", return_value=(True, "ok")), \
             mock.patch.object(sl, "check_command_path",
                               return_value=(sl.ALIVE, "fine")), \
             mock.patch.object(sl.time, "sleep", side_effect=_stop_after_four), \
             mock.patch.object(sl, "Osc") as osc:
            osc.return_value.start.return_value = mock.MagicMock()
            with self.assertRaises(KeyboardInterrupt):
                sl.main([])

        alarm = json.loads(self.alarm.read_text())
        self.assertGreater(alarm["governor_repairs_in_window"], 2)
        self.assertNotEqual("ok", alarm["state"],
                            "a governor being reset repeatedly must not read as ok")
        self.assertIn("masking a fight", json.dumps(alarm))

    def test_metrics_ride_on_a_healthy_alarm_file(self) -> None:
        """The rate must be visible when things are FINE, or it isn't monitoring."""
        alarm, _ = self._run_once(actual="performance")
        self.assertEqual("ok", alarm["state"])
        self.assertIn("xruns_per_min", alarm)
        self.assertIn("governor", alarm)
        self.assertEqual("performance", alarm["governor"])


if __name__ == "__main__":
    unittest.main()
