"""Tests for SurgePolyGovernor hysteresis and instrumentation."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from patch_browser.surge_poly_governor import (
    DEFAULT_CPU_EMERGENCY_THRESHOLD,
    DEFAULT_CPU_HIGH_HOLD_S,
    DEFAULT_CPU_HIGH_THRESHOLD,
    DEFAULT_CPU_LOW_HOLD_S,
    DEFAULT_CPU_LOW_THRESHOLD,
    DEFAULT_CPU_SPIKE_THRESHOLD,
    DEFAULT_CPU_WARM_THRESHOLD,
    DEFAULT_PATCH_WARM_WINDOW_S,
    DEFAULT_POLL_INTERVAL_S,
    DEFAULT_STEP_DOWN,
    DEFAULT_STEP_DOWN_SPIKE,
    DEFAULT_STEP_DOWN_WARM,
    DEFAULT_STEP_UP,
    PolyGovernorJournal,
    SurgePolyGovernor,
    load_governor_config,
)


class FakeOscClient:
    def __init__(self) -> None:
        self.messages: list[tuple[str, float]] = []

    def send_message(self, address: str, value: float) -> None:
        self.messages.append((address, value))


class FakeCpuMonitor:
    def __init__(self, percent: float | None, *, raw: float | None = None) -> None:
        self._percent = percent
        self._raw = raw if raw is not None else percent

    def snapshot(self) -> dict:
        return {
            "online": self._percent is not None,
            "percent": self._percent,
            "raw_percent": self._raw,
            "source": "proc",
        }


class SurgePolyGovernorTests(unittest.TestCase):
    @contextmanager
    def _patch_state_file(self, state_path: Path):
        with (
            mock.patch("patch_browser.surge_playback.POLY_STATE_FILE", state_path),
            mock.patch("patch_browser.surge_poly_governor.POLY_STATE_FILE", state_path),
        ):
            yield

    def _write_state(self, path: Path, *, effective: int = 12, ceiling: int = 12) -> None:
        path.write_text(
            json.dumps(
                {
                    "patch": "Lead",
                    "native_poly": 16,
                    "ceiling_poly": ceiling,
                    "effective_poly": effective,
                    "reuse_single": True,
                }
            ),
            encoding="utf-8",
        )

    def test_load_governor_config_defaults(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            cfg = load_governor_config()
        self.assertEqual(cfg.poll_interval_s, DEFAULT_POLL_INTERVAL_S)
        self.assertEqual(cfg.cpu_emergency_threshold, DEFAULT_CPU_EMERGENCY_THRESHOLD)
        self.assertEqual(cfg.cpu_spike_threshold, DEFAULT_CPU_SPIKE_THRESHOLD)
        self.assertEqual(cfg.cpu_high_threshold, DEFAULT_CPU_HIGH_THRESHOLD)
        self.assertEqual(cfg.cpu_warm_threshold, DEFAULT_CPU_WARM_THRESHOLD)
        self.assertEqual(cfg.cpu_low_threshold, DEFAULT_CPU_LOW_THRESHOLD)
        self.assertEqual(cfg.cpu_high_hold_s, DEFAULT_CPU_HIGH_HOLD_S)
        self.assertEqual(cfg.cpu_low_hold_s, DEFAULT_CPU_LOW_HOLD_S)
        self.assertEqual(cfg.patch_warm_window_s, DEFAULT_PATCH_WARM_WINDOW_S)
        self.assertEqual(cfg.step_down, DEFAULT_STEP_DOWN)
        self.assertEqual(cfg.step_down_spike, DEFAULT_STEP_DOWN_SPIKE)
        self.assertEqual(cfg.step_down_warm, DEFAULT_STEP_DOWN_WARM)
        self.assertEqual(cfg.step_up, DEFAULT_STEP_UP)

    def test_steps_down_when_cpu_high(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "poly.json"
            self._write_state(state_path, effective=12, ceiling=12)
            osc = FakeOscClient()
            monitor = mock.Mock()
            monitor.check_health.return_value = (True, None)
            journal = PolyGovernorJournal()
            governor = SurgePolyGovernor(
                osc,
                surge_monitor=monitor,
                cpu_monitor=FakeCpuMonitor(55.0),
                journal=journal,
            )
            with self._patch_state_file(state_path):
                with mock.patch("patch_browser.surge_poly_governor.governor_active", return_value=True):
                    governor._last_patch = "Lead"
                    governor._warm_preempt_done = True
                    governor._high_since = time.monotonic() - 2.0
                    governor._refresh_patch_state()
                    with mock.patch("builtins.print") as mock_print:
                        governor._tick()
            self.assertTrue(osc.messages)
            self.assertEqual(osc.messages[-1][1], 10.0)
            logged = " ".join(str(c) for c in mock_print.call_args_list)
            self.assertIn("12 -> 10", logged)
            self.assertIn("reason=high", logged)

    def test_emergency_slam_at_90(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "poly.json"
            self._write_state(state_path, effective=9, ceiling=12)
            osc = FakeOscClient()
            monitor = mock.Mock()
            monitor.check_health.return_value = (True, None)
            governor = SurgePolyGovernor(osc, surge_monitor=monitor, cpu_monitor=FakeCpuMonitor(92.0))
            with self._patch_state_file(state_path):
                with mock.patch("patch_browser.surge_poly_governor.governor_active", return_value=True):
                    governor._last_patch = "Lead"
                    governor._warm_preempt_done = True
                    governor._refresh_patch_state()
                    governor._tick()
            self.assertTrue(osc.messages)
            self.assertEqual(osc.messages[-1][1], 3.0)

    def test_spike_steps_down_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "poly.json"
            self._write_state(state_path, effective=12, ceiling=12)
            osc = FakeOscClient()
            monitor = mock.Mock()
            monitor.check_health.return_value = (True, None)
            governor = SurgePolyGovernor(osc, surge_monitor=monitor, cpu_monitor=FakeCpuMonitor(88.0))
            with self._patch_state_file(state_path):
                with mock.patch("patch_browser.surge_poly_governor.governor_active", return_value=True):
                    governor._last_patch = "Lead"
                    governor._refresh_patch_state()
                    governor._warm_preempt_done = True
                    governor._tick()
            self.assertTrue(osc.messages)
            self.assertEqual(osc.messages[-1][1], 8.0)

    def test_warm_preempt_after_patch_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "poly.json"
            self._write_state(state_path, effective=9, ceiling=12)
            osc = FakeOscClient()
            monitor = mock.Mock()
            monitor.check_health.return_value = (True, None)
            governor = SurgePolyGovernor(
                osc,
                surge_monitor=monitor,
                cpu_monitor=FakeCpuMonitor(64.0, raw=64.8),
            )
            with self._patch_state_file(state_path):
                with mock.patch("patch_browser.surge_poly_governor.governor_active", return_value=True):
                    governor._last_patch = "Other"
                    governor._refresh_patch_state()
                    with mock.patch("builtins.print") as mock_print:
                        governor._tick()
            self.assertTrue(osc.messages)
            self.assertEqual(osc.messages[-1][1], 7.0)
            logged = " ".join(str(c) for c in mock_print.call_args_list)
            self.assertIn("reason=warm", logged)
            self.assertIn("raw=64.8", logged)

    def test_disabled_skips_adjustment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "poly.json"
            self._write_state(state_path)
            osc = FakeOscClient()
            monitor = mock.Mock()
            monitor.check_health.return_value = (True, None)
            governor = SurgePolyGovernor(osc, surge_monitor=monitor, cpu_monitor=FakeCpuMonitor(90.0))
            governor._enabled = False
            with self._patch_state_file(state_path):
                with mock.patch("builtins.print") as mock_print:
                    governor._refresh_patch_state()
                    governor._tick()
            self.assertEqual(osc.messages, [])
            mock_print.assert_not_called()

    def test_unchanged_limit_logs_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "poly.json"
            self._write_state(state_path, effective=4, ceiling=12)
            osc = FakeOscClient()
            monitor = mock.Mock()
            monitor.check_health.return_value = (True, None)
            governor = SurgePolyGovernor(osc, surge_monitor=monitor, cpu_monitor=FakeCpuMonitor(55.0))
            with self._patch_state_file(state_path):
                with mock.patch("patch_browser.surge_poly_governor.governor_active", return_value=True):
                    governor._high_since = time.monotonic() - 2.0
                    governor._refresh_patch_state()
                    with mock.patch("builtins.print") as mock_print:
                        governor._tick()
            self.assertEqual(osc.messages, [])
            mock_print.assert_not_called()

    def test_spam_guard_suppresses_after_threshold(self) -> None:
        journal = PolyGovernorJournal()
        journal._window_start = 0.0
        with mock.patch("builtins.print") as mock_print, mock.patch(
            "patch_browser.surge_poly_governor.time.monotonic", return_value=0.5
        ):
            for i in range(12):
                journal.log_transition(
                    old_limit=12 - i,
                    new_limit=11 - i,
                    reason="high",
                    cpu=61.2,
                    raw_cpu=64.8,
                    patch="Lead",
                    held_s=0.3,
                )
        lines = [str(c.args[0]) for c in mock_print.call_args_list if c.args]
        transition_lines = [line for line in lines if "->" in line and "reason=" in line]
        self.assertEqual(len(transition_lines), 10)
        self.assertEqual(journal._suppressed, 2)

    def test_spam_guard_emits_summary_on_window_roll(self) -> None:
        journal = PolyGovernorJournal()
        journal._window_start = 0.0
        journal._suppressed = 7
        with mock.patch("builtins.print") as mock_print:
            journal._flush_suppressed()
        mock_print.assert_called_once()
        self.assertIn("suppressed=7", str(mock_print.call_args))

    def test_startup_log_once(self) -> None:
        osc = FakeOscClient()
        monitor = mock.Mock()
        journal = PolyGovernorJournal()
        governor = SurgePolyGovernor(osc, surge_monitor=monitor, journal=journal)
        with mock.patch("builtins.print") as mock_print:
            governor.start()
            governor.start()
        startup_calls = [
            c for c in mock_print.call_args_list if c.args and str(c.args[0]).startswith("poly-governor: startup")
        ]
        self.assertEqual(len(startup_calls), 1)


if __name__ == "__main__":
    unittest.main()
