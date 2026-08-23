"""Tests for poly governor v2 ramp-aware limit."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from patch_browser.governor_load import LoadTracker
from patch_browser.governor_v2 import (
    adaptive_poll_interval,
    continuous_target_limit,
    rate_limited_target,
    rise_bias,
    smoothstep,
)
from patch_browser.surge_poly_governor import SurgePolyGovernor, governor_v2_active


class GovernorV2CurveTests(unittest.TestCase):
    def test_smoothstep_endpoints(self) -> None:
        self.assertEqual(smoothstep(0.0), 0.0)
        self.assertEqual(smoothstep(1.0), 1.0)

    def test_continuous_target_at_soft_and_hard(self) -> None:
        self.assertEqual(
            continuous_target_limit(
                50.0, ceiling=12, floor=4, soft_start=58.0, hard=82.0
            ),
            12,
        )
        self.assertEqual(
            continuous_target_limit(
                90.0, ceiling=12, floor=4, soft_start=58.0, hard=82.0
            ),
            4,
        )

    def test_continuous_target_monotonic_midband(self) -> None:
        prev = 12
        for load in range(59, 82):
            target = continuous_target_limit(
                float(load), ceiling=12, floor=4, soft_start=58.0, hard=82.0
            )
            self.assertLessEqual(target, prev)
            prev = target

    def test_rise_bias_zero_when_flat(self) -> None:
        self.assertEqual(rise_bias(0.0, full_rate=40.0, max_bias=12.0), 0.0)
        self.assertEqual(rise_bias(-5.0, full_rate=40.0, max_bias=12.0), 0.0)

    def test_rise_bias_scales_with_rate(self) -> None:
        self.assertAlmostEqual(rise_bias(20.0, full_rate=40.0, max_bias=12.0), 6.0)
        self.assertAlmostEqual(rise_bias(40.0, full_rate=40.0, max_bias=12.0), 12.0)

    def test_rate_limiter_blocks_fast_step_down(self) -> None:
        now = 100.0
        blocked = rate_limited_target(
            12,
            4,
            last_step_down_at=now - 0.1,
            now=now,
            step_interval_s=0.25,
        )
        self.assertIsNone(blocked)
        allowed = rate_limited_target(
            12,
            4,
            last_step_down_at=now - 0.3,
            now=now,
            step_interval_s=0.25,
        )
        self.assertEqual(allowed, 11)

    def test_adaptive_poll_fast_when_rising(self) -> None:
        self.assertEqual(
            adaptive_poll_interval(
                load=50.0,
                dload_dt=10.0,
                soft_start=58.0,
                fast_s=0.05,
                slow_s=0.15,
            ),
            0.05,
        )
        self.assertEqual(
            adaptive_poll_interval(
                load=40.0,
                dload_dt=0.0,
                soft_start=58.0,
                fast_s=0.05,
                slow_s=0.15,
            ),
            0.15,
        )


class GovernorV2IntegrationTests(unittest.TestCase):
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

    def test_v2_rate_limited_step_down(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "poly.json"
            self._write_state(state_path, effective=12, ceiling=12)
            osc = mock.Mock()
            monitor = mock.Mock()
            monitor.check_health.return_value = (True, None)
            sends: list[int] = []

            def fake_send(_client, limit: int) -> bool:
                sends.append(limit)
                return True

            with mock.patch.dict(
                os.environ,
                {
                    "MPE_POLY_GOVERNOR_V2": "1",
                    "MPE_POLY_LIMIT_MODE": "progressive",
                    "MPE_POLY_LIMIT_SOFT_START": "58",
                    "MPE_POLY_LIMIT_HARD": "82",
                },
                clear=False,
            ):
                governor = SurgePolyGovernor(osc, surge_monitor=monitor)
                self.assertTrue(governor_v2_active())
                with (
                    self._patch_state_file(state_path),
                    mock.patch(
                        "patch_browser.surge_poly_governor.governor_active", return_value=True
                    ),
                    mock.patch(
                        "patch_browser.surge_poly_governor.send_polylimit",
                        side_effect=fake_send,
                    ),
                    mock.patch("builtins.print"),
                ):
                    governor._last_patch = "Lead"
                    governor._warm_preempt_done = True
                    governor._refresh_patch_state()
                    tracker = governor._load_tracker
                    with mock.patch.object(
                        tracker,
                        "sample",
                        return_value=mock.Mock(
                            load=70.0,
                            raw_load=70.0,
                            source="jack",
                            dload_dt=50.0,
                            xruns=0,
                            xrun_delta=0,
                        ),
                    ):
                        governor._tick_v2()
            self.assertEqual(sends, [11])


if __name__ == "__main__":
    unittest.main()
