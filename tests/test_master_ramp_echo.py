"""A smoothed master move must not walk `user_gain` down.

Regression for a defect found on hardware 2026-08-31: at a *fixed* master of
1.0, loop 0's `wet` read 0.9959, 0.9604, 0.9262, 0.8604 over four master
cycles. Levels sagged on their own, which reads as a hardware fault.

Cause: the sender smooths, so the engine echoes intermediate values. Echo
detection only knew the settled target, so every step of the ramp looked like a
foreign write and `_user_cc_from_composed_wet` divided a mid-ramp `wet` by the
master that had already arrived — implying a column gain far too low.

The test drives the real objects and closes the loop the way the engine does:
whatever the sender emits is fed straight back to `seed_from_engine`.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts" / "sooperlooper"))

from apc_faders import MASTER  # noqa: E402
from loop_mix import CoalescingSender, LoopMix  # noqa: E402

NUM_LOOPS = 4


class MasterRampEchoTests(unittest.TestCase):
    def _rig(self, *, wire_probe: bool):
        """LoopMix + sender with the engine's echo looped back in."""
        mix = LoopMix(num_loops=NUM_LOOPS)
        sent: list[tuple[str, list]] = []

        def _send(path, args):
            sent.append((path, args))

        sender = CoalescingSender(_send, interval_s=0.0)
        if wire_probe:
            mix.echo_probe = sender.was_emitted
        return mix, sender, sent

    def _cycle_master(self, mix, sender, sent, *, start_t: float, seen: list):
        """One master move, ramped to completion, every echo fed back.

        Samples `user_gain[0]` after each echo rather than only at the end: the
        corruption is transient — once the ramp settles, later echoes re-adopt
        and the value recovers. That recovery is exactly why the bug survived,
        and why a test that only inspects the settled state proves nothing.
        """
        t = start_t
        for cc in (38, 127):
            sender.submit(mix.messages_for(MASTER, cc), now=t)
            for _ in range(60):
                t += 0.01
                sender.tick(now=t)
                while sent:
                    path, args = sent.pop(0)
                    loop = int(path.split("/")[2])
                    mix.seed_from_engine(loop, float(args[1]))
                    seen.append(mix.user_gain[0])
        return t

    def _run(self, *, wire_probe: bool) -> tuple[int, list]:
        mix, sender, sent = self._rig(wire_probe=wire_probe)
        start = mix.user_gain[0]
        seen: list[int] = []
        t = 0.0
        for _ in range(4):
            t = self._cycle_master(mix, sender, sent, start_t=t, seen=seen)
        return start, seen

    def test_master_cycles_never_move_user_gain(self):
        start, seen = self._run(wire_probe=True)
        self.assertTrue(seen, "rig produced no echoes — it is testing nothing")
        drifted = sorted({g for g in seen if g != start})
        self.assertEqual(
            drifted, [],
            f"a master move must not touch any column gain (was {start}, "
            f"saw {drifted})",
        )

    def test_without_the_probe_the_drift_reproduces(self):
        """Anti-vacuity: the rig really does provoke the bug it guards."""
        start, seen = self._run(wire_probe=False)
        worst = min(seen)
        self.assertLess(
            worst, start - 2,
            "expected the unguarded path to walk user_gain down; if it no "
            "longer does, this test has stopped proving anything",
        )

    def test_a_genuine_foreign_write_is_still_adopted(self):
        """The fix must not make the composer deaf to real outside changes."""
        mix, sender, sent = self._rig(wire_probe=True)
        sender.submit(mix.messages_for(MASTER, 127), now=0.0)
        sender.tick(now=1.0)
        sent.clear()
        before = mix.user_gain[0]
        # A level nothing in this process ever sent.
        mix.seed_from_engine(0, mix.wet_for(0) * 0.25)
        self.assertNotEqual(
            mix.user_gain[0], before,
            "an unexplained engine level must still be adopted",
        )


if __name__ == "__main__":
    unittest.main()
