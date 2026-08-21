"""sl-watchdog must not fork jack_lsp when meter.state can answer (E2)."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "sl_watchdog_meter", REPO / "scripts/sooperlooper/sl-watchdog.py")
sl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sl)


def _fresh_meter(**flags: int) -> str:
    body = {
        "jack_online": 1,
        "looper_client": 0,
        "looper_playback": 0,
        "updated": int(time.time()),
        **flags,
    }
    return "\n".join(f"{k}={v}" for k, v in body.items()) + "\n"


class MeterGraphSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.alarm = self.tmp / "alarm.json"
        self.meter = self.tmp / "meter.state"
        self.meter.write_text(f"xruns=0\nupdated={int(time.time())}\n", encoding="utf-8")
        mock.patch.object(sl, "ALARM_FILE", self.alarm).start()
        mock.patch.object(sl, "METER_STATE_FILE", self.meter).start()
        self.addCleanup(mock.patch.stopall)

    def _run_stale_once(self, *, jackd: bool) -> list[str]:
        snap = sl.GraphSnapshot(None, None, None, "meter_stale")
        with mock.patch.object(sl, "GOVERNOR_TARGET", ""), \
             mock.patch.object(sl, "read_graph_snapshot", return_value=snap), \
             mock.patch.object(sl, "jackd_running", return_value=jackd), \
             mock.patch.object(sl, "jack_graph") as jg, \
             mock.patch.object(sl, "engine_running", return_value=None), \
             mock.patch.object(sl, "check_command_path", return_value=(sl.ALIVE, "ok")), \
             mock.patch.object(sl, "Osc") as osc:
            engine = mock.MagicMock()
            engine.get.return_value = "play"
            osc.return_value.start.return_value = engine
            sl.main(["--once", "--skip-source-check"])
        jg.assert_not_called()
        if not self.alarm.exists():
            return []
        return json.loads(self.alarm.read_text()).get("problems", [])

    def test_healthy_meter_avoids_jack_lsp(self) -> None:
        snap = sl.GraphSnapshot(True, True, True, "meter")
        with mock.patch.object(sl, "GOVERNOR_TARGET", ""), \
             mock.patch.object(sl, "read_graph_snapshot", return_value=snap), \
             mock.patch.object(sl, "jack_graph") as jg, \
             mock.patch.object(sl, "engine_running", return_value=True), \
             mock.patch.object(sl, "check_command_path", return_value=(sl.ALIVE, "ok")), \
             mock.patch.object(sl, "Osc") as osc:
            engine = mock.MagicMock()
            engine.get.return_value = "play"
            osc.return_value.start.return_value = engine
            sl.main(["--once", "--skip-source-check"])
        jg.assert_not_called()

    def test_read_graph_snapshot_prefers_meter(self) -> None:
        now = time.time()
        with mock.patch.object(sl, "jack_reachable_via_meter", return_value=True), \
             mock.patch.object(sl, "looper_client_via_meter", return_value=True), \
             mock.patch.object(sl, "looper_playback_via_meter", return_value=True), \
             mock.patch.object(sl, "jack_graph") as jg:
            snap = sl.read_graph_snapshot(now=now)
        self.assertEqual("meter", snap.source)
        jg.assert_not_called()

    def test_stale_meter_jackd_up_reports_meter_stale(self) -> None:
        with mock.patch.object(sl, "jack_reachable_via_meter", return_value=None), \
             mock.patch.object(sl, "looper_client_via_meter", return_value=None), \
             mock.patch.object(sl, "looper_playback_via_meter", return_value=None), \
             mock.patch.object(sl, "jack_graph") as jg:
            snap = sl.read_graph_snapshot()
        self.assertEqual("meter_stale", snap.source)
        self.assertIsNone(snap.jack_reachable)
        jg.assert_not_called()

    def test_stale_meter_alarm_names_meter_when_jackd_up(self) -> None:
        problems = self._run_stale_once(jackd=True)
        self.assertTrue(any("meter fault" in p or "peak-meter stale" in p for p in problems))

    def test_stale_meter_alarm_names_jack_when_jackd_down(self) -> None:
        problems = self._run_stale_once(jackd=False)
        self.assertTrue(any("JACK down" in p for p in problems))


class MeterMainLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.alarm = self.tmp / "alarm.json"
        self.meter = self.tmp / "meter.state"
        self.meter.write_text(f"xruns=0\nupdated={int(time.time())}\n", encoding="utf-8")
        mock.patch.object(sl, "ALARM_FILE", self.alarm).start()
        mock.patch.object(sl, "METER_STATE_FILE", self.meter).start()
        self.addCleanup(mock.patch.stopall)

    def test_engine_down_via_meter_without_jack_lsp(self) -> None:
        snap = sl.GraphSnapshot(True, False, False, "meter")
        with mock.patch.object(sl, "read_graph_snapshot", return_value=snap), \
             mock.patch.object(sl, "jack_graph") as jg, \
             mock.patch.object(sl, "engine_running", return_value=False), \
             mock.patch.object(sl, "Osc") as osc:
            osc.return_value.start.return_value = mock.MagicMock()
            rc = sl.main(["--once", "--skip-source-check"])
        jg.assert_not_called()
        alarm = json.loads(self.alarm.read_text())
        self.assertEqual("engine-down", alarm["state"])
        self.assertEqual(0, rc)


if __name__ == "__main__":
    unittest.main()
