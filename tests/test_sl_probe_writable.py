"""check_loops_writable — the check that catches a phantom loop.

The bug it exists for: SooperLooper reports the loop count you asked for, and
every index answers `get` with defaults, so a read-based check cannot tell a
real loop from one that discards every write.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "sooperlooper"))

from sl_probe import (  # noqa: E402
    ALIVE,
    PHANTOM,
    PROBE_CONTROL,
    UNREACHABLE,
    check_loops_writable,
)


class FakeEngine:
    """Loops below `usable` store writes; the rest read defaults and drop them —
    exactly what SooperLooper 1.7.9 does above index 14."""

    def __init__(self, *, usable: int, default: float = 1.0) -> None:
        self.usable = usable
        self.values: dict[int, float] = {}
        self.default = default
        self.writes: list[tuple[int, float]] = []

    def get(self, loop: int, _ctrl: str) -> float:
        return self.values.get(loop, self.default)

    def send(self, loop: int, _ctrl: str, value: float) -> None:
        self.writes.append((loop, value))
        if loop < self.usable:
            self.values[loop] = value


class CheckLoopsWritableTests(unittest.TestCase):
    def _run(self, engine, num_loops):
        return check_loops_writable(engine.get, engine.send,
                                    num_loops=num_loops, settle_s=0.0)

    def test_all_usable_reads_alive(self) -> None:
        verdict, phantoms, detail = self._run(FakeEngine(usable=15), 15)
        self.assertEqual(verdict, ALIVE)
        self.assertEqual(phantoms, [])
        self.assertIn("15", detail)

    def test_the_real_engine_shape_is_caught(self) -> None:
        """15 usable, asked for 16 — the exact 2026-08-27 configuration."""
        verdict, phantoms, detail = self._run(FakeEngine(usable=15), 16)
        self.assertEqual(verdict, PHANTOM)
        self.assertEqual(phantoms, [15])
        self.assertIn("ignore writes", detail)

    def test_detail_names_the_setting_to_change(self) -> None:
        """A check that only says "broken" gets ignored at 8am."""
        _, _, detail = self._run(FakeEngine(usable=15), 20)
        self.assertIn("MPE_SL_LOOPS", detail)
        self.assertIn("15", detail)

    def test_every_phantom_is_reported_not_just_the_first(self) -> None:
        _, phantoms, _ = self._run(FakeEngine(usable=15), 20)
        self.assertEqual(phantoms, [15, 16, 17, 18, 19])

    def test_a_silent_engine_is_unreachable_not_phantom(self) -> None:
        """Distinct verdicts: "wrong loop count" and "engine is gone" have
        different remedies."""
        engine = FakeEngine(usable=4)
        verdict, _, detail = check_loops_writable(
            lambda _l, _c: None, engine.send, num_loops=4, settle_s=0.0
        )
        self.assertEqual(verdict, UNREACHABLE)
        self.assertIn(PROBE_CONTROL, detail)

    def test_probe_restores_what_it_touched(self) -> None:
        """It writes to a live engine, so it must not leave the probe value
        behind on a loop the player is about to use."""
        from sl_probe import PROBE_RESTORE

        engine = FakeEngine(usable=3)
        self._run(engine, 3)
        self.assertEqual(engine.values, {0: PROBE_RESTORE, 1: PROBE_RESTORE,
                                         2: PROBE_RESTORE})
