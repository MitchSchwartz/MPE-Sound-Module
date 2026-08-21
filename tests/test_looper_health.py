"""Tests for looper/graph health metrics and HUD badge (salvaged from phase0 f39d0a6)."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from patch_browser.looper_health import (
    JackCpuLoadReader,
    JackGraphHealth,
    JournalXrunCounter,
    LooperHealth,
    collect_jack_graph_health,
    jackd_journal_xruns_since,
    read_jack_cpu_load_pct,
)
from patch_browser.looper_hud import health_badge

BUDGET_S = 512 / 48000  # 10.67 ms


class LooperHealthTests(unittest.TestCase):
    def _health(self) -> LooperHealth:
        return LooperHealth(period_budget_s=BUDGET_S, window_s=1.0)

    def test_reports_nothing_before_first_window_closes(self) -> None:
        health = self._health()
        health.record_period(0.001, 0.0)
        snap = health.snapshot()
        self.assertIsNone(snap["max_pct"])
        self.assertEqual(snap["over_budget"], 0)

    def test_worst_case_is_reported_as_percent_of_budget(self) -> None:
        health = self._health()
        health.record_period(BUDGET_S / 2, 0.0)
        health.record_period(BUDGET_S, 0.5)
        health.record_period(0.0001, 1.5)
        snap = health.snapshot()
        self.assertAlmostEqual(snap["max_pct"], 100.0, delta=4.0)
        self.assertEqual(snap["over_budget"], 0)

    def test_counts_periods_over_budget(self) -> None:
        health = self._health()
        for i in range(3):
            health.record_period(BUDGET_S * 1.4, i * 0.1)
        health.record_period(0.0001, 2.0)
        snap = health.snapshot()
        self.assertEqual(snap["over_budget"], 3)
        self.assertGreater(snap["max_pct"], 100.0)

    def test_snapshot_carries_xruns(self) -> None:
        self.assertEqual(self._health().snapshot(xruns=7)["xruns"], 7)


class JackGraphHealthTests(unittest.TestCase):
    def test_tracks_session_xruns_and_cpu_peak(self) -> None:
        tracker = JackGraphHealth(window_s=1.0, started_at=1000.0)
        tracker.sample(cpu_load_pct=40.0, xruns_total=2, now_s=0.0)
        tracker.sample(cpu_load_pct=85.0, xruns_total=2, now_s=0.5)
        tracker.sample(cpu_load_pct=10.0, xruns_total=2, now_s=1.1)
        snap = tracker.snapshot()
        self.assertEqual(snap["xruns"], 2)
        self.assertAlmostEqual(snap["max_pct"], 85.0, delta=0.1)

    def test_collect_jack_graph_health(self) -> None:
        tracker = JackGraphHealth(window_s=0.0, started_at=1000.0)
        tracker.cpu_reader = SimpleNamespace(read=lambda: 22.5, close=lambda: None)
        tracker.xrun_counter = SimpleNamespace(poll=lambda: 3)
        snap = collect_jack_graph_health(tracker)
        self.assertEqual(snap["xruns"], 3)


class JackProbeTests(unittest.TestCase):
    @patch("patch_browser.looper_health.subprocess.run")
    def test_read_jack_cpu_load_pct(self, run_mock) -> None:
        run_mock.return_value.stdout = "jack DSP load 35.666183\njack DSP load 40.1\n"
        run_mock.return_value.returncode = 0
        self.assertAlmostEqual(read_jack_cpu_load_pct(), 40.1)

    @patch("patch_browser.looper_health.subprocess.run")
    def test_read_jack_cpu_load_pct_escalates_to_sigkill(self, run_mock) -> None:
        """jack_cpu_load ignores SIGTERM; without -k the client leaks onto the graph."""
        run_mock.return_value.stdout = ""
        run_mock.return_value.returncode = 0
        read_jack_cpu_load_pct()
        argv = run_mock.call_args[0][0]
        self.assertIn("-k", argv)
        self.assertLess(argv.index("-k"), argv.index("jack_cpu_load"))

    @patch("patch_browser.looper_health.subprocess.run")
    def test_jackd_journal_xruns_since(self, run_mock) -> None:
        run_mock.return_value.stdout = "jackd: xrun of at least 128 msecs\nok\n"
        run_mock.return_value.returncode = 0
        self.assertEqual(jackd_journal_xruns_since(1_700_000_000.0), 1)


class JournalXrunCounterTests(unittest.TestCase):
    """One ``journalctl -f`` for the life of the monitor — no fork per poll."""

    def test_spawns_follow_once_across_many_polls(self) -> None:
        import io
        import time

        spawns: list[list[str]] = []

        class FakeProc:
            def __init__(self, argv: list[str]) -> None:
                spawns.append(argv)
                self.stdout = io.StringIO("jackd: xrun of at least 128 msecs\nfine\n")
                self.killed = False

            def poll(self):
                return None

            def kill(self) -> None:
                self.killed = True

            def wait(self, timeout=None):
                return 0

        counter = JournalXrunCounter(1_700_000_000.0)
        with patch(
            "patch_browser.looper_health.subprocess.Popen",
            side_effect=lambda argv, **kw: FakeProc(argv),
        ):
            time.sleep(0.05)
            self.assertEqual(counter.poll(), 1)
            for _ in range(20):
                self.assertEqual(counter.poll(), 1)
            counter.close()

        self.assertEqual(len(spawns), 1, f"respawned {len(spawns)}x — forks per tick")
        self.assertIn("-f", spawns[0])
        self.assertIn("--since", spawns[0])

    def test_quiet_poll_keeps_running_total(self) -> None:
        import io
        import time

        counter = JournalXrunCounter(1_700_000_000.0)
        with patch(
            "patch_browser.looper_health.subprocess.Popen",
            side_effect=lambda argv, **kw: type(
                "FakeProc",
                (),
                {
                    "stdout": io.StringIO("jackd: xrun\n"),
                    "poll": lambda self: None,
                    "kill": lambda self: None,
                    "wait": lambda self, timeout=None: 0,
                },
            )(),
        ):
            time.sleep(0.05)
            self.assertEqual(counter.poll(), 1)
            self.assertEqual(counter.poll(), 1)
            counter.close()

    def test_missing_journalctl_is_never_retried(self) -> None:
        counter = JournalXrunCounter(1_700_000_000.0)
        with patch(
            "patch_browser.looper_health.subprocess.Popen", side_effect=FileNotFoundError
        ) as popen:
            self.assertIsNone(counter.poll())
            self.assertIsNone(counter.poll())
        self.assertEqual(popen.call_count, 1)


class JackCpuLoadReaderTests(unittest.TestCase):
    """One held client, not a fork per sample — each spawn reorders jackd's graph."""

    def test_spawns_once_across_many_reads(self) -> None:
        spawns: list[list[str]] = []

        class FakeProc:
            def __init__(self, argv):
                spawns.append(argv)
                self.stdout = iter(["jack DSP load 12.5\n", "jack DSP load 19.0\n"])
                self.killed = False
                FakeProc.last = self

            def poll(self):
                return None

            def terminate(self):
                raise AssertionError("SIGTERM is ignored by jack_cpu_load — must SIGKILL")

            def kill(self):
                self.killed = True

            def wait(self, timeout=None):
                return 0

        reader = JackCpuLoadReader()
        with patch(
            "patch_browser.looper_health.subprocess.Popen",
            side_effect=lambda argv, **kw: FakeProc(argv),
        ):
            reader.read()
            for _ in range(20):
                reader.read()
            reader.close()

        self.assertEqual(len(spawns), 1, f"respawned {len(spawns)}x — reorders the graph")
        self.assertTrue(
            FakeProc.last.killed, "close() must SIGKILL or the client leaks onto the graph"
        )

    def test_missing_binary_is_never_retried(self) -> None:
        reader = JackCpuLoadReader()
        with patch(
            "patch_browser.looper_health.subprocess.Popen", side_effect=FileNotFoundError
        ) as popen:
            for _ in range(5):
                self.assertIsNone(reader.read())
        self.assertEqual(popen.call_count, 1)


class HealthBadgeTests(unittest.TestCase):
    def test_silent_when_healthy(self) -> None:
        self.assertIsNone(health_badge({"health": {"max_pct": 12.0, "xruns": 0}}))

    def test_xruns_win_over_utilization(self) -> None:
        badge = health_badge({"health": {"max_pct": 12.0, "xruns": 3}})
        self.assertEqual(badge, ("!3", "danger"))

    def test_warns_before_budget(self) -> None:
        self.assertEqual(health_badge({"health": {"max_pct": 80.0}}), ("80%", "warn"))


if __name__ == "__main__":
    unittest.main()
