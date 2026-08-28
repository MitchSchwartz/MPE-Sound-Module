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
    PROBE_CONTROLS,
    UNREACHABLE,
    check_loops_writable,
    probe_restore_for,
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


class TimingOutEngine:
    """A loop that takes the write but whose read-back times out.

    Live-possible: `settle_s` is 0.12 s and the engine is under audio load. The
    write lands; the reply does not arrive in time.
    """

    def __init__(self, *, times_out: set[int]) -> None:
        self.times_out = times_out
        self.values: dict[tuple[int, str], float] = {}
        self.reads = 0

    def get(self, loop: int, ctrl: str):
        self.reads += 1
        if loop in self.times_out and self.reads % 2 == 0:
            return None  # the read-back, not the initial read
        return self.values.get((loop, ctrl), 0.0)

    def send(self, loop: int, ctrl: str, value: float) -> None:
        self.values[(loop, ctrl)] = value


class RestoreLeakTests(unittest.TestCase):
    """The probe writes a live engine every 10 s. Anything it fails to put back
    stays on the instrument until the next wire-jack pass."""

    def test_a_timed_out_read_back_still_restores(self) -> None:
        """The leak, exactly.

        The old code restored only on the success branch: `if after is None or
        mismatch -> phantoms.append(loop)` with the restore in the `else`. So a
        loop whose read-back timed out was called a phantom AND left holding
        the probe value — ~0.3-0.46 on `dry`, which is a permanent second copy
        of the player's signal at the speakers.
        """
        engine = TimingOutEngine(times_out={0})
        check_loops_writable(engine.get, engine.send, num_loops=1, settle_s=0.0)
        for (loop, ctrl), value in engine.values.items():
            self.assertAlmostEqual(
                value, probe_restore_for(ctrl), places=3,
                msg=f"loop {loop} left holding a probe value on {ctrl}",
            )


class PerControlEngine:
    """Loops below `usable` accept writes, but only on `writable_control`."""

    def __init__(self, *, usable: int, writable_control: str) -> None:
        self.usable = usable
        self.writable_control = writable_control
        self.values: dict[tuple[int, str], float] = {}
        self.writes: list[tuple[int, str, float]] = []

    def get(self, loop: int, ctrl: str) -> float:
        return self.values.get((loop, ctrl), 1.0)

    def send(self, loop: int, ctrl: str, value: float) -> None:
        self.writes.append((loop, ctrl, value))
        if loop < self.usable and ctrl == self.writable_control:
            self.values[(loop, ctrl)] = value

    def controls_written(self):
        return {ctrl for _, ctrl, _ in self.writes}


class ControlChainTests(unittest.TestCase):
    def test_an_unsupported_control_does_not_make_every_loop_a_phantom(self) -> None:
        """Otherwise the remedy printed is "lower MPE_SL_LOOPS to 0"."""
        engine = PerControlEngine(usable=15, writable_control="dry")
        verdict, phantoms, _ = check_loops_writable(
            engine.get, engine.send, num_loops=15, settle_s=0.0
        )
        self.assertEqual(verdict, ALIVE)
        self.assertEqual(phantoms, [])

    def test_a_real_phantom_still_reads_as_one_through_the_chain(self) -> None:
        engine = PerControlEngine(usable=15, writable_control="dry")
        verdict, phantoms, detail = check_loops_writable(
            engine.get, engine.send, num_loops=16, settle_s=0.0
        )
        self.assertEqual(verdict, PHANTOM)
        self.assertEqual(phantoms, [15])
        self.assertIn("MPE_SL_LOOPS", detail)

    def test_the_working_control_is_reused_and_the_audio_path_left_alone(self) -> None:
        """Having found a control the engine takes, it must not keep retrying
        the others on all fifteen loops — that is 15 extra writes to `dry`."""
        head = PROBE_CONTROLS[0]
        engine = PerControlEngine(usable=15, writable_control=head)
        check_loops_writable(engine.get, engine.send, num_loops=15, settle_s=0.0)
        self.assertEqual(engine.controls_written(), {head})


if __name__ == "__main__":
    unittest.main()
