"""Stop All, then a pad: the clip starts now and BOTH clocks agree it is beat 1.

Mitch, about his own instrument:

    "When I've stopped all and I start a clip, we've reset the phase to zero ...
     it should also mean that start happens immediately."

`SlotRuntime` does start it immediately, and marks the bench's downbeat. The
defect this file pins is the other half: the bench used to call
`grid.mark_phase_zero()` directly, which moves OUR bar line and leaves the
engine's wherever `set_tempo` last put it (engine.cpp:2174-2178). Every clip
launched afterwards is then quantized against a bar line the engine does not
share — placed off the beat, with the surface vouching for it. Silent, and only
audible as "the second clip doesn't line up," which is the report this whole
area keeps generating.

`sl_grid_sync.apply_established_grid` pairs the two: the tempo send IS the
engine's phase reset, and it marks ours in the same call. The rule lives beside
that seam rather than in a bench closure so it can be tested without
`run_bench`, which no test can execute.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "sooperlooper"))

from sl_grid_state import GridState  # noqa: E402
from sl_grid_sync import mark_immediate_downbeat  # noqa: E402


class ImmediateDownbeatTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sent: list[tuple[str, list]] = []

    def _send(self, prefix: str, args: list) -> None:
        self.sent.append((prefix, list(args)))

    def _established(self) -> GridState:
        g = GridState()
        g.arm(0)
        g.establish(0, 2.0)          # 120 BPM, one 2.0s cycle
        g.mark_phase_zero(100.0)
        return g

    def _tempo_sends(self):
        return [a for _p, a in self.sent if a and a[0] == "tempo"]

    def test_the_engine_is_told_where_the_downbeat_moved(self) -> None:
        """The regression. Without this the engine never hears about it."""
        grid = self._established()
        applied = mark_immediate_downbeat(
            self._send, grid, num_loops=15, now=500.0
        )
        self.assertTrue(applied)
        self.assertTrue(
            self._tempo_sends(),
            "no tempo sent — the engine's phase was never reset, so its bar "
            "line still sits wherever set_tempo last put it while ours moved",
        )

    def test_our_clock_moves_too(self) -> None:
        grid = self._established()
        mark_immediate_downbeat(self._send, grid, num_loops=15, now=500.0)
        self.assertAlmostEqual(grid.phase_zero_at, 500.0)
        self.assertAlmostEqual(grid.next_boundary(500.0), 502.0)

    def test_both_halves_happen_or_neither(self) -> None:
        """The pairing is the point — a test that checked one would pass on
        exactly the code this fix replaced."""
        grid = self._established()
        mark_immediate_downbeat(self._send, grid, num_loops=15, now=500.0)
        self.assertTrue(self._tempo_sends())
        self.assertAlmostEqual(grid.phase_zero_at, 500.0)

    def test_the_cycle_is_unchanged_by_the_downbeat(self) -> None:
        """Moving beat 1 must not re-length the phrase."""
        grid = self._established()
        before = grid.cycle_s
        mark_immediate_downbeat(self._send, grid, num_loops=15, now=500.0)
        self.assertAlmostEqual(grid.cycle_s, before)

    def test_no_grid_means_nothing_is_sent_and_nothing_raises(self) -> None:
        """With no tempo there is no bar line to move.

        `apply_established_grid` raises rather than zero the engine against a
        grid nobody agreed on, so the guard has to be here — and a launch into
        silence before the first take is an ordinary thing to do.
        """
        grid = GridState()
        applied = mark_immediate_downbeat(
            self._send, grid, num_loops=15, now=500.0
        )
        self.assertFalse(applied)
        self.assertEqual(self.sent, [])

    def test_it_does_not_re_arm_the_loops(self) -> None:
        """arm_loops=False: ~90 quantize/sync messages into a clip that just
        started is latency on the one gesture that must feel instant."""
        grid = self._established()
        mark_immediate_downbeat(self._send, grid, num_loops=15, now=500.0)
        armed = [a for _p, a in self.sent if a and a[0] in ("quantize", "sync")]
        self.assertEqual(armed, [], f"re-armed loops on an immediate start: {armed}")


if __name__ == "__main__":
    unittest.main()
