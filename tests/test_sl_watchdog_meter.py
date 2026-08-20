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
            sl.main(["--once"])
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

    def test_stale_meter_falls_back_to_jack_lsp(self) -> None:
        graph = "mpe-looper:common_out_1\n   system:playback_1\n"
        with mock.patch.object(sl, "jack_reachable_via_meter", return_value=None), \
             mock.patch.object(sl, "looper_client_via_meter", return_value=None), \
             mock.patch.object(sl, "looper_playback_via_meter", return_value=None), \
             mock.patch.object(sl, "jack_graph", return_value=graph):
            snap = sl.read_graph_snapshot()
        self.assertEqual("jack_lsp", snap.source)
        self.assertTrue(snap.looper_client)


class MeterMainLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.alarm = Path(self.enterContext(tempfile.TemporaryDirectory())) / "alarm.json"
        mock.patch.object(sl, "ALARM_FILE", self.alarm).start()
        self.addCleanup(mock.patch.stopall)

    def test_engine_down_via_meter_without_jack_lsp(self) -> None:
        snap = sl.GraphSnapshot(True, False, False, "meter")
        with mock.patch.object(sl, "read_graph_snapshot", return_value=snap), \
             mock.patch.object(sl, "jack_graph") as jg, \
             mock.patch.object(sl, "engine_running", return_value=False), \
             mock.patch.object(sl, "Osc") as osc:
            osc.return_value.start.return_value = mock.MagicMock()
            rc = sl.main(["--once"])
        jg.assert_not_called()
        alarm = json.loads(self.alarm.read_text())
        self.assertEqual("engine-down", alarm["state"])
        self.assertEqual(0, rc)


if __name__ == "__main__":
    unittest.main()
