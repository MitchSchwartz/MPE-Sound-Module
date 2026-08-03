"""Tests for SurgePolyGovernor hysteresis."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from contextlib import contextmanager
from unittest import mock

from patch_browser.surge_poly_governor import SurgePolyGovernor


class FakeOscClient:
    def __init__(self) -> None:
        self.messages: list[tuple[str, float]] = []

    def send_message(self, address: str, value: float) -> None:
        self.messages.append((address, value))


class FakeCpuMonitor:
    def __init__(self, percent: float | None) -> None:
        self._percent = percent

    def snapshot(self) -> dict:
        return {
            "online": self._percent is not None,
            "percent": self._percent,
            "raw_percent": self._percent,
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

    def test_steps_down_when_cpu_high(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "poly.json"
            self._write_state(state_path, effective=12, ceiling=12)
            osc = FakeOscClient()
            monitor = mock.Mock()
            monitor.check_health.return_value = (True, None)
            governor = SurgePolyGovernor(osc, surge_monitor=monitor, cpu_monitor=FakeCpuMonitor(90.0))
            with self._patch_state_file(state_path):
                with mock.patch("patch_browser.surge_poly_governor.governor_active", return_value=True):
                    governor._high_since = time.monotonic() - 2.0
                    governor._refresh_patch_state()
                    governor._tick()
            self.assertTrue(osc.messages)
            self.assertEqual(osc.messages[-1][1], 10.0)

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
            governor = SurgePolyGovernor(osc, surge_monitor=monitor, cpu_monitor=FakeCpuMonitor(64.0))
            with self._patch_state_file(state_path):
                with mock.patch("patch_browser.surge_poly_governor.governor_active", return_value=True):
                    governor._last_patch = "Other"
                    governor._refresh_patch_state()
                    governor._tick()
            self.assertTrue(osc.messages)
            self.assertEqual(osc.messages[-1][1], 7.0)

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
                governor._refresh_patch_state()
                governor._tick()
            self.assertEqual(osc.messages, [])


if __name__ == "__main__":
    unittest.main()
