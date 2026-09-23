"""The live-path arm must work in the configuration the appliance actually runs.

`mpe-live-monitor` takes the direct Surge -> playback connection out of the
graph while it is inserted, and puts it back when it stops. If it dies without
putting it back, you cannot hear yourself play and nothing else in the system
notices — so `sl-watchdog.py` repairs it.

That arm was written three times before it ran. The first version needed
`MPE_PEAK_METER=1`, which is off by default. The second added a sensor that
works without the meter — and `read_graph_snapshot` threw its answer away
whenever `meter.state` was absent, which is also the default. Every test of the
sensor passed both times, because none of them went through the snapshot.

These do.
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from patch_browser import audio_engine  # noqa: E402
_spec = importlib.util.spec_from_file_location(
    "sl_watchdog_live_path", REPO / "scripts/sooperlooper/sl-watchdog.py")
sl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sl)


class LivePathReachesTheSnapshot(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.meter = self.tmp / "meter.state"          # deliberately not written
        self.live = self.tmp / "live-monitor.state"
        # Both sensors read their paths from `patch_browser.audio_engine`, which
        # is where the watchdog's imported helpers resolve them.
        mock.patch.object(sl, "METER_STATE_FILE", self.meter).start()
        mock.patch.object(audio_engine, "METER_STATE_FILE", self.meter).start()
        mock.patch.object(audio_engine, "LIVE_MONITOR_STATE_FILE", self.live).start()
        mock.patch.object(sl, "live_monitor_enabled", lambda: True).start()
        # Each test gets its own watcher: staleness is a property of how the
        # file has moved since this watcher started looking.
        mock.patch.object(
            audio_engine, "_live_monitor_watcher",
            audio_engine.LiveMonitorWatcher(path=self.live),
        ).start()
        self.addCleanup(mock.patch.stopall)

    def _live_state(self, *, detached: int, carrying: int, age_s: float) -> None:
        self.live.write_text(
            f"carrying={carrying}\ndetached={detached}\n"
            f"updated={int(time.time() - age_s)}\n",
            encoding="utf-8",
        )

    def _snapshot_after(self, seconds: float):
        """Take a snapshot `seconds` later on the watchdog's monotonic clock.

        The sensor decides staleness by the file standing still, so it needs two
        looks separated by time — the Pi has no RTC and a wall-clock comparison
        condemns a healthy insert every time NTP steps the clock.
        """
        base = time.monotonic()
        with mock.patch.object(sl.time, "monotonic", lambda: base):
            sl.read_graph_snapshot()
        with mock.patch.object(sl.time, "monotonic", lambda: base + seconds):
            return sl.read_graph_snapshot()

    def test_the_default_configuration_still_answers(self):
        """No meter.state at all — the case that was silently unhandled twice."""
        self._live_state(detached=1, carrying=1, age_s=0)  # insert then stops writing
        snap = self._snapshot_after(audio_engine.LIVE_MONITOR_STATE_MAX_AGE_S + 2)
        self.assertFalse(self.meter.exists(), "this test is only meaningful with no meter")
        self.assertIs(snap.surge_playback, False)

    def test_a_healthy_insert_is_not_repaired(self):
        self._live_state(detached=1, carrying=1, age_s=0)
        self.assertIs(sl.read_graph_snapshot().surge_playback, True)

    def test_the_watcher_state_does_not_leak_between_polls_of_other_paths(self):
        """The shared watcher must rebind when the file it watches changes."""
        self._live_state(detached=1, carrying=1, age_s=0)
        self.assertIs(sl.read_graph_snapshot().surge_playback, True)
        other = self.tmp / "elsewhere.state"
        other.write_text("carrying=0\ndetached=0\nupdated=5\n", encoding="utf-8")
        self.assertIs(audio_engine.live_path_via_monitor_state(path=other), True)

    def test_nothing_is_concluded_when_the_monitor_is_off(self):
        """With no insert, nothing removes the direct path and there is no alarm."""
        mock.patch.object(sl, "live_monitor_enabled", lambda: False).start()
        self._live_state(detached=1, carrying=1, age_s=0)
        self.assertIsNone(
            self._snapshot_after(audio_engine.LIVE_MONITOR_STATE_MAX_AGE_S + 2).surge_playback
        )

    def test_a_file_that_stopped_moving_but_never_detached_is_not_an_alarm(self):
        self._live_state(detached=0, carrying=0, age_s=0)
        snap = self._snapshot_after(audio_engine.LIVE_MONITOR_STATE_MAX_AGE_S + 2)
        self.assertIsNone(snap.surge_playback)

    def test_a_healthy_insert_is_never_repaired_however_long_it_runs(self):
        """Two minutes of a live insert must never read as the silent case."""
        base = time.monotonic()
        for tick in range(1, 60):
            self.live.write_text(
                f"carrying=1\ndetached=1\nupdated={1000 + tick * 2}\n",
                encoding="utf-8",
            )
            with mock.patch.object(sl.time, "monotonic", lambda t=tick: base + t * 2.0):
                self.assertIs(
                    sl.read_graph_snapshot().surge_playback, True,
                    f"a healthy insert read as silent at t={tick * 2}s",
                )

    def test_the_meter_still_wins_when_it_is_on(self):
        self.meter.write_text(
            f"surge_playback=1\njack_online=1\nlooper_client=1\nlooper_playback=1\n"
            f"updated={int(time.time())}\n",
            encoding="utf-8",
        )
        self._live_state(detached=1, carrying=1, age_s=60)  # stale, would say False
        self.assertIs(sl.read_graph_snapshot().surge_playback, True)


class TheRepairDecision(unittest.TestCase):
    """The decision itself, executed — not its position in the file.

    This arm was wrong three times (nested under a meter-only field, fed by a
    snapshot that discarded the sensor, gated on a sensor that could not see its
    own subject) and every version passed every test, because the tests asserted
    on the sensor and on source text. These call the decision.
    """

    def _snap(self, **fields):
        base = dict(jack_reachable=None, looper_client=None, looper_playback=None,
                    source="meter_stale", surge_playback=None)
        base.update(fields)
        return sl.GraphSnapshot(**base)

    def test_the_default_configuration_repairs(self):
        """Meter off — every field None except the one with its own sensor."""
        self.assertTrue(
            sl.live_path_needs_repair(
                self._snap(surge_playback=False), orphan=False, stopped=False)
        )

    def test_a_healthy_live_path_is_left_alone(self):
        self.assertFalse(
            sl.live_path_needs_repair(
                self._snap(surge_playback=True), orphan=False, stopped=False)
        )

    def test_not_knowing_is_not_a_reason_to_rewire(self):
        """Reconnecting on a hunch puts two copies of the live signal out."""
        self.assertFalse(
            sl.live_path_needs_repair(
                self._snap(surge_playback=None), orphan=False, stopped=False)
        )

    def test_nothing_is_repaired_while_orphaned_or_stopped(self):
        for kwargs in ({"orphan": True, "stopped": False},
                       {"orphan": False, "stopped": True}):
            with self.subTest(**kwargs):
                self.assertFalse(
                    sl.live_path_needs_repair(self._snap(surge_playback=False), **kwargs)
                )

    def test_nothing_is_repaired_when_jack_is_known_to_be_down(self):
        self.assertFalse(
            sl.live_path_needs_repair(
                self._snap(surge_playback=False, jack_reachable=False),
                orphan=False, stopped=False)
        )

    def test_an_unknown_jack_does_not_block_the_repair(self):
        """`jack_reachable` is meter-answered, so None is the normal case."""
        self.assertTrue(
            sl.live_path_needs_repair(
                self._snap(surge_playback=False, jack_reachable=None),
                orphan=False, stopped=False)
        )


if __name__ == "__main__":
    unittest.main()
