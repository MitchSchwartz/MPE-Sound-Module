"""A stopped looper must not be reported as an orphaned one.

Both look identical on the JACK graph — no `mpe-looper:*` client either way —
but they need opposite responses. An orphan is a live process silently
discarding every `/set` and `/hit`, and it needs a human immediately. A stopped
engine is the ordinary state of an appliance nobody has started the looper on.

This matters now that sl-watchdog runs as a boot service: conflating the two
means every boot alarms ORPHAN every 10 s until someone starts the looper, and a
watchdog that cries wolf on a healthy appliance gets ignored on a sick one.
"""

from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "sl_watchdog", REPO / "scripts/sooperlooper/sl-watchdog.py")
sl_watchdog = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sl_watchdog)


GRAPH_WITHOUT_LOOPER = """Surge XT:out_1
   system:playback_1
system:playback_1
   Surge XT:out_1
"""


class EngineDownTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(
            __import__("tempfile").TemporaryDirectory()))
        self.alarm = self.tmp / "alarm.json"
        self.meter = self.tmp / "meter.state"
        self.meter.write_text(
            f"xruns=0\nupdated={int(__import__('time').time())}\n", encoding="utf-8")
        patcher = mock.patch.object(sl_watchdog, "ALARM_FILE", self.alarm)
        patcher.start()
        self.addCleanup(patcher.stop)
        meter_patch = mock.patch.object(sl_watchdog, "METER_STATE_FILE", self.meter)
        meter_patch.start()
        self.addCleanup(meter_patch.stop)

    def _run_once(self, *, engine_up: bool) -> dict:
        snap = sl_watchdog.GraphSnapshot(True, False, False, "meter")
        with mock.patch.object(sl_watchdog, "read_graph_snapshot",
                               return_value=snap), \
             mock.patch.object(sl_watchdog, "engine_running",
                               return_value=engine_up), \
             mock.patch.object(sl_watchdog, "Osc") as osc, \
             mock.patch.object(sl_watchdog, "capture_wedge_diagnostics",
                               return_value={}):
            osc.return_value.start.return_value = mock.MagicMock()
            sl_watchdog.main(["--once", "--skip-source-check"])
        return json.loads(self.alarm.read_text())

    def test_no_process_reports_engine_down_not_orphan(self) -> None:
        alarm = self._run_once(engine_up=False)
        self.assertEqual("engine-down", alarm["state"])

    def test_engine_down_is_not_reported_as_ok(self) -> None:
        """The quiet failure: `state: ok` on an appliance with no looper."""
        alarm = self._run_once(engine_up=False)
        self.assertNotEqual("ok", alarm["state"])

    def test_live_process_without_jack_client_is_still_an_orphan(self) -> None:
        """The fix must not blunt the alarm it was carved out of."""
        alarm = self._run_once(engine_up=True)
        self.assertEqual("orphan", alarm["state"])

    def test_unknown_process_state_alarms_without_asserting_it_is_up(self) -> None:
        """proc scan failing is not evidence the process is alive.

        Still alarms — loud on the control path — but the detail must not claim
        a live process we never confirmed.
        """
        snap = sl_watchdog.GraphSnapshot(True, False, False, "meter")
        with mock.patch.object(sl_watchdog, "read_graph_snapshot",
                               return_value=snap), \
             mock.patch.object(sl_watchdog, "engine_running", return_value=None), \
             mock.patch.object(sl_watchdog, "Osc") as osc, \
             mock.patch.object(sl_watchdog, "capture_wedge_diagnostics",
                               return_value={}):
            osc.return_value.start.return_value = mock.MagicMock()
            sl_watchdog.main(["--once", "--skip-source-check"])
        alarm = json.loads(self.alarm.read_text())
        self.assertEqual("orphan", alarm["state"])
        self.assertIn("UNKNOWN", alarm["detail"])
        self.assertNotIn("process is up", alarm["detail"])

    def test_engine_down_exits_clean(self) -> None:
        """`--once` is a health gate; a deliberately stopped looper isn't a fault."""
        snap = sl_watchdog.GraphSnapshot(True, False, False, "meter")
        with mock.patch.object(sl_watchdog, "read_graph_snapshot",
                               return_value=snap), \
             mock.patch.object(sl_watchdog, "engine_running", return_value=False), \
             mock.patch.object(sl_watchdog, "Osc") as osc:
            osc.return_value.start.return_value = mock.MagicMock()
            self.assertEqual(0, sl_watchdog.main(["--once", "--skip-source-check"]))


if __name__ == "__main__":
    unittest.main()
