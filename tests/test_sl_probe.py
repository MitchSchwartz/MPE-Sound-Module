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
    PROBE_CONTROLS,
    UNREACHABLE,
    WEDGED,
    AUDIO_PATH_CONTROLS,
    check_command_path,
    probe_restore_for,
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


class MultiControlEngine:
    """Per-control support, so "engine does not know this control" and "engine
    is wedged" can be told apart in a test the way they must be live."""

    def __init__(self, *, writable=(), readable=None, value=0.0):
        self.writable = set(writable)
        self.readable = set(readable) if readable is not None else None
        self.values = {}
        self.value = value
        self.writes = []

    def get(self, ctrl):
        if self.readable is not None and ctrl not in self.readable:
            return None
        return self.values.get(ctrl, self.value)

    def send(self, ctrl, val):
        self.writes.append((ctrl, val))
        if ctrl in self.writable:
            self.values[ctrl] = val

    def controls_written(self):
        return {ctrl for ctrl, _ in self.writes}


class ProbeControlChainTests(unittest.TestCase):
    """The probe must not write the audio path to ask a health question.

    `dry` is audible: the JACK graph is parallel (Surge -> playback direct AND
    Surge -> every loop input), so the looper is silent on the passthrough only
    because dry is pinned to 0. Probing it lifts a second copy of the player's
    live signal for the settle window, every watchdog interval. Reported as
    random 1-2 s volume swells while not even looping, 2026-08-27.
    """

    def test_the_audio_path_control_is_the_last_resort(self) -> None:
        self.assertEqual(PROBE_CONTROLS[-1], "dry")
        self.assertGreater(len(PROBE_CONTROLS), 1,
                           "a chain of one degrades to the old audible probe")

    def test_a_healthy_engine_never_writes_the_audio_path(self) -> None:
        """The whole point: on a working engine, dry is never touched."""
        engine = MultiControlEngine(writable=PROBE_CONTROLS)
        verdict, _ = check_command_path(
            engine.get, engine.send, seed="t", settle_s=0.0
        )
        self.assertEqual(verdict, ALIVE)
        self.assertNotIn("dry", engine.controls_written())

    def test_an_unsupported_first_candidate_falls_back_not_wedges(self) -> None:
        """A control this engine build does not take must never be reported as
        a wedge — the remedy for a wedge destroys every recorded loop."""
        engine = MultiControlEngine(writable={"dry"})
        verdict, _ = check_command_path(
            engine.get, engine.send, seed="t", settle_s=0.0
        )
        self.assertEqual(verdict, ALIVE)

    def test_wedged_only_after_every_candidate_refuses(self) -> None:
        engine = MultiControlEngine(writable=set())
        verdict, detail = check_command_path(
            engine.get, engine.send, seed="t", settle_s=0.0
        )
        self.assertEqual(verdict, WEDGED)
        for control in PROBE_CONTROLS:
            self.assertIn(control, detail)

    def test_every_control_it_wrote_is_restored(self) -> None:
        engine = MultiControlEngine(writable=PROBE_CONTROLS, value=0.41)
        check_command_path(engine.get, engine.send, seed="t", settle_s=0.0)
        for control in engine.controls_written():
            self.assertAlmostEqual(engine.values[control],
                                   probe_restore_for(control), places=3)

    def test_an_unreadable_control_is_skipped_not_fatal(self) -> None:
        """Reading an unknown control returns nothing; that is not an outage."""
        engine = MultiControlEngine(writable={"dry"}, readable={"dry"})
        verdict, _ = check_command_path(
            engine.get, engine.send, seed="t", settle_s=0.0
        )
        self.assertEqual(verdict, ALIVE)

    def test_a_raising_engine_still_restores(self) -> None:
        """The old code restored at each return, so an exception mid-probe left
        the probe value on a live control."""
        engine = MultiControlEngine(writable=PROBE_CONTROLS)
        calls = {"n": 0}
        real_get = engine.get

        def exploding_get(ctrl):
            calls["n"] += 1
            if calls["n"] == 2:  # the read-back, after the write landed
                raise RuntimeError("engine went away")
            return real_get(ctrl)

        with self.assertRaises(RuntimeError):
            check_command_path(exploding_get, engine.send, seed="t", settle_s=0.0)
        head = PROBE_CONTROLS[0]
        self.assertEqual(engine.writes[-1], (head, probe_restore_for(head)))

    def test_falling_back_to_the_audio_path_says_so_out_loud(self) -> None:
        """ALIVE and audible at once is the dangerous combination.

        If rec_thresh is not accepted by this engine build the chain still
        works — by writing dry, which is what made the instrument swell. That
        must not read as an ordinary healthy line in the watchdog log.
        """
        engine = MultiControlEngine(writable={"dry"})
        verdict, detail = check_command_path(
            engine.get, engine.send, seed="t", settle_s=0.0
        )
        self.assertEqual(verdict, ALIVE)
        self.assertIn("AUDIBLE", detail)
        self.assertIn(PROBE_CONTROLS[0], detail)

    def test_the_quiet_path_carries_no_warning(self) -> None:
        engine = MultiControlEngine(writable=PROBE_CONTROLS)
        _, detail = check_command_path(
            engine.get, engine.send, seed="t", settle_s=0.0
        )
        self.assertNotIn("AUDIBLE", detail)

    def test_dry_is_known_to_be_in_the_audio_path(self) -> None:
        self.assertIn("dry", AUDIO_PATH_CONTROLS)


if __name__ == "__main__":
    unittest.main()
