"""The command-path probe must not manufacture a wedge under contention.

A false WEDGED verdict is not a cosmetic bug. Its documented remedy is
`sl-restart`, which destroys every recorded loop — so a monitoring race that
produces one is a data-loss path.
"""

from __future__ import annotations

import unittest

from scripts.sooperlooper.sl_probe import (
    ALIVE,
    PROBE_RESTORE,
    PROBE_CONTROL,
    UNREACHABLE,
    WEDGED,
    check_command_path,
    probe_target,
)


class FakeEngine:
    """Holds a value. Optionally ignores writes, or lets someone else win."""

    def __init__(self, value=0.0, *, accept=True, interloper=None):
        self.value = value
        self.accept = accept
        self.interloper = interloper
        self.writes = []

    def get(self, _ctrl):
        return self.value

    def send(self, _ctrl, val):
        self.writes.append(val)
        if self.accept:
            self.value = val
        if self.interloper is not None:
            # Another prober's `set` lands after ours, as it would in the gap.
            self.value = self.interloper


class ProbeTargetTests(unittest.TestCase):
    def test_different_probers_pick_different_values(self) -> None:
        """Fixed alternation between two constants is what made them collide."""
        self.assertNotEqual(probe_target("sl-health", 0.0),
                            probe_target("sl-watchdog", 0.0))

    def test_never_asks_for_the_value_already_there(self) -> None:
        """Asking for the current value proves nothing about the write path."""
        for seed in ("sl-health", "sl-watchdog", "x"):
            t = probe_target(seed, None)
            self.assertNotAlmostEqual(probe_target(seed, t), t, places=2)


class CommandPathTests(unittest.TestCase):
    def _run(self, engine, seed="test"):
        return check_command_path(engine.get, engine.send, seed=seed, settle_s=0.0)

    def test_a_landed_write_is_alive(self) -> None:
        verdict, _ = self._run(FakeEngine(0.0))
        self.assertEqual(verdict, ALIVE)

    def test_an_ignored_write_is_wedged(self) -> None:
        verdict, detail = self._run(FakeEngine(0.0, accept=False))
        self.assertEqual(verdict, WEDGED)
        self.assertIn(PROBE_CONTROL, detail)

    def test_another_probers_write_landing_is_ALIVE_not_wedged(self) -> None:
        """The bug this file exists for.

        sl-health asked for one value; sl-watchdog wrote a different one in the
        gap; health read it back, saw "not what I asked", and declared WEDGED.
        A value that moved somewhere we did not put it is direct evidence the
        non-realtime queue is draining.
        """
        engine = FakeEngine(0.0, accept=False, interloper=0.75)
        verdict, detail = self._run(engine)
        self.assertEqual(verdict, ALIVE)
        self.assertIn("another prober", detail)

    def test_an_unreadable_engine_is_unreachable_not_wedged(self) -> None:
        """Cannot read is a different fault from can read but cannot write."""
        engine = FakeEngine(0.0)
        engine.get = lambda _ctrl: None
        verdict, _ = self._run(engine)
        self.assertEqual(verdict, UNREACHABLE)

    def test_a_wedge_verdict_takes_more_than_one_miss(self) -> None:
        """One transient miss must never trigger a loop-destroying remedy."""
        engine = FakeEngine(0.0, accept=False)
        check_command_path(engine.get, engine.send, seed="t", settle_s=0.0, retries=1)
        self.assertGreaterEqual(len(engine.writes), 2)

    def test_restores_to_the_policy_value_not_to_what_it_found(self) -> None:
        """Two probers restoring each other's leftovers converge on pollution.

        Found live: loop 0 sat at dry=0.41 while every other loop read 0.0 —
        Surge passing through the looper and doubling at the speakers.
        """
        from scripts.sooperlooper.sl_probe import PROBE_RESTORE

        engine = FakeEngine(0.41)  # already polluted by the other prober
        self._run(engine)
        self.assertAlmostEqual(engine.value, PROBE_RESTORE, places=3)

    def test_a_wedge_verdict_also_leaves_the_policy_value(self) -> None:
        engine = FakeEngine(0.41, accept=False)
        self._run(engine)
        self.assertEqual(engine.writes[-1], PROBE_RESTORE)


if __name__ == "__main__":
    unittest.main()
