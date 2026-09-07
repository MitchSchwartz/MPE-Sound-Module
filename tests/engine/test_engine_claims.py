"""Engine claims, executable.

Every test here replaces a sentence that used to live only in a comment or a
DECISIONS row. The docstring names where the claim came from; the assertion
is what SooperLooper 1.7.9 actually does. When a claim and the engine
disagree, the engine wins and the comment gets rewritten -- never the
assertion bent to fit.

The grid is established the way the appliance establishes it: a free-form
defining take, `GridState.establish` to derive the tempo, and
`apply_established_grid` to send it -- production code, real engine.

    MPE_ENGINE_TESTS=1 python3 -m pytest tests/engine -v

Real time: a clip on the grid costs a cycle of wall clock. See harness.py.
"""

from __future__ import annotations

import time
import unittest

from tests import conftest  # noqa: F401 -- puts scripts/sooperlooper on sys.path

from sl_grid_state import GridState
from sl_grid_sync import apply_established_grid
from sl_loop_states import (
    SL_STATE_OFF,
    SL_STATE_OFF_MUTED,
    SL_STATE_PAUSED,
    SL_STATE_MUTE,
    SL_STATE_PLAYING,
    SL_STATE_RECORDING,
    SL_STATE_WAIT_START,
    SL_STATE_WAIT_STOP,
)
from track_gesture import stop_all_loops
from tests.engine.harness import PERIOD_S, SKIP_REASON, Engine, docker_ok, enabled

RUN = enabled() and docker_ok()

#: How far a measured loop length may sit from the truth: two JACK periods
#: for the engine's own granularity, plus the OSC hop and Python's sleep.
LEN_TOL_S = 2 * PERIOD_S + 0.03
#: How far from the defining loop's bar line a synced clip may begin. The
#: engine's clock phase is zeroed by `set tempo` (engine.cpp:2178), which
#: production sends when the defining take lands, so the offset is the
#: latency of that round trip -- tens of milliseconds, not hundreds.
BAR_TOL_S = 0.10


@unittest.skipUnless(RUN, SKIP_REASON)
class EngineClaims(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.e = Engine.shared()

    def setUp(self) -> None:
        self.e.reset()

    # --- helpers -----------------------------------------------------------
    def _take(self, loop: int, seconds: float) -> float:
        """Free-form: record, hold, record. Returns the wall-clock hold."""
        e = self.e
        t0 = time.monotonic()
        e.hit(loop, "record")
        self.assertTrue(e.wait_state(loop, SL_STATE_RECORDING, 2.0), "record never started")
        time.sleep(max(0.0, seconds - (time.monotonic() - t0)))
        e.hit(loop, "record")
        held = time.monotonic() - t0
        self.assertTrue(e.wait_state(loop, SL_STATE_PLAYING, 2.0), "take never landed")
        return held

    def _establish(self, seconds: float = 2.0) -> GridState:
        """The appliance's first take: free-form on loop 0, then the grid from it."""
        e = self.e
        grid = GridState()
        self.assertTrue(grid.arm(0), "the grid would not take loop 0 as its defining take")
        self._take(0, seconds)
        self.assertIsNotNone(grid.establish(0, e.loop_len(0)), "derive_tempo refused the take")
        apply_established_grid(e.send, grid, num_loops=e.num_loops,
                               now=time.monotonic(), arm_loops=True)
        return grid

    def _wait_mid_bar(self, loop: int = 0, lo: float = 0.7, hi: float = 1.1) -> float:
        """Block until the defining loop is well inside its bar; return the position."""
        deadline = time.monotonic() + 6.0
        while time.monotonic() < deadline:
            pos = self.e.get(loop, "loop_pos")
            if pos is not None and lo <= pos <= hi:
                return pos
            time.sleep(0.02)
        self.fail("loop 0 never reported a mid-bar position")

    # --- empty loops ---------------------------------------------------------
    def test_a_muted_empty_loop_reads_off_muted_and_only_mute_off_clears_it(self) -> None:
        """sl_loop_states.EMPTY_STATES: a muted empty loop reports 20, not 0.

        MEASURED on the Pi 2026-09-06 (loops 1-14 after Stop All); measured
        here too, plus the part the Pi did not show: undo_all does not lift
        the mute, so a clear after Stop All leaves the pad at 20. That is why
        EMPTY_STATES has two members.
        """
        e = self.e
        self.assertEqual(e.state(0), SL_STATE_OFF)
        e.hit(0, "mute_on")
        self.assertTrue(e.wait_state(0, SL_STATE_OFF_MUTED, 2.0))
        e.hit(0, "undo_all")
        time.sleep(0.3)
        self.assertEqual(e.state(0), SL_STATE_OFF_MUTED, "undo_all lifted the mute")
        e.hit(0, "mute_off")
        self.assertTrue(e.wait_state(0, SL_STATE_OFF, 2.0))

    # --- length -----------------------------------------------------------
    def test_a_free_form_take_is_as_long_as_it_was_held(self) -> None:
        """sl_grid_state.derive_tempo: the first take is free-form and the
        engine keeps whatever length it was handed.

        The 7-bar session of 2026-09-06 began with a 1.084 s take. Nothing in
        the engine sanity-checks a defining take; if a guard exists it is ours.
        """
        e = self.e
        held = self._take(0, 1.084)
        self.assertAlmostEqual(e.loop_len(0), held, delta=LEN_TOL_S)
        # Off the grid the loop is its own cycle.
        self.assertAlmostEqual(e.get(0, "cycle_len"), e.loop_len(0), delta=PERIOD_S)

    def test_a_clip_hit_mid_bar_waits_for_the_bar_and_lands_one_cycle_long(self) -> None:
        """sl_grid_sync.set_grid_active(active=True): 'clips count in to the
        next bar (sync) and their length snaps to one cycle (quantize)'.

        fake_sl_engine.boundary(length=2.0) hands the fake both halves of
        this. Here the engine produces them from the tempo production derived
        from the defining take. This is also the behaviour Mitch saw on
        2026-09-06 as 'starting late' -- correct on a live grid, wrong on a
        stale one; whether the grid should still exist is the Python layer's
        business, pinned in test_sl_grid_state.py.
        """
        e = self.e
        grid = self._establish(2.0)
        cycle = grid.cycle_s
        self._wait_mid_bar()
        e.hit(1, "record")
        self.assertEqual(e.wait_state_in(1, {SL_STATE_WAIT_START, SL_STATE_RECORDING}, 1.0),
                         SL_STATE_WAIT_START, "a mid-bar hit must arm, not record")
        self.assertTrue(e.wait_state(1, SL_STATE_RECORDING, 2 * cycle + 1.0))
        pos = e.get(0, "loop_pos")
        self.assertTrue(pos < BAR_TOL_S or pos > cycle - BAR_TOL_S,
                        f"loop 1 began recording at loop 0 position {pos:.3f}s")
        time.sleep(0.3)
        e.hit(1, "record")
        self.assertEqual(e.wait_state_in(1, {SL_STATE_WAIT_STOP, SL_STATE_PLAYING}, 1.0),
                         SL_STATE_WAIT_STOP, "the stop must wait for the cycle")
        self.assertTrue(e.wait_state(1, SL_STATE_PLAYING, 2 * cycle + 1.0))
        self.assertAlmostEqual(e.loop_len(1), cycle, delta=2 * PERIOD_S)

    def test_a_record_hit_while_armed_is_ignored(self) -> None:
        """test_gesture_against_engine.test_double_tap_while_armed says a
        second `record` while WAIT_START 'would reach the engine as CANCEL
        and lose the take entirely'. MEASURED 2026-09-07: it does not. The
        arm survives, the take starts on the bar, and the second hit is not
        kept as the stop either -- it is simply gone. The bench's rule (do not
        send record while an intent is pending) stands; its reason changes.
        """
        e = self.e
        grid = self._establish(2.0)
        cycle = grid.cycle_s
        self._wait_mid_bar()
        e.hit(1, "record")
        self.assertTrue(e.wait_state(1, SL_STATE_WAIT_START, 1.0))
        e.hit(1, "record")
        time.sleep(0.3)
        self.assertEqual(e.state(1), SL_STATE_WAIT_START, "the second hit cancelled the arm")
        self.assertTrue(e.wait_state(1, SL_STATE_RECORDING, 2 * cycle + 1.0))
        time.sleep(cycle + 0.3)
        self.assertEqual(e.state(1), SL_STATE_RECORDING,
                         "the second hit was kept as the stop after all")

    # --- pause / trigger --------------------------------------------------------
    def test_pause_on_holds_a_loop_and_trigger_lifts_it(self) -> None:
        """track_gesture: 'pause_on is idempotent; trigger lifts a pause'.

        Stop All sends pause_on; a launch on a stopped pad sends trigger.
        """
        e = self.e
        self._take(0, 0.5)
        e.hit(0, "pause_on")
        self.assertTrue(e.wait_state(0, SL_STATE_PAUSED, 2.0))
        e.hit(0, "pause_on")
        time.sleep(0.3)
        self.assertEqual(e.state(0), SL_STATE_PAUSED, "pause_on is not a toggle")
        e.hit(0, "trigger")
        self.assertTrue(e.wait_state(0, SL_STATE_PLAYING, 2.0))

    def test_pause_is_a_toggle(self) -> None:
        """track_gesture: 'pause is a toggle' -- why the bench never sends it bare."""
        e = self.e
        self._take(0, 0.5)
        e.hit(0, "pause")
        self.assertTrue(e.wait_state(0, SL_STATE_PAUSED, 2.0))
        e.hit(0, "pause")
        self.assertTrue(e.wait_state(0, SL_STATE_PLAYING, 2.0))

    # --- clear ------------------------------------------------------------------
    def test_undo_all_empties_a_playing_loop(self) -> None:
        """slot_runtime clears with undo_all; sl_grid_state expects OFF, no length."""
        e = self.e
        self._take(0, 0.5)
        e.hit(0, "undo_all")
        self.assertTrue(e.wait_state(0, SL_STATE_OFF, 2.0))
        self.assertLess(e.loop_len(0), PERIOD_S)

    def test_undo_all_empties_a_paused_loop_too(self) -> None:
        """DECISIONS 2026-09-06, trap 2: stop then clear must still drop the
        grid, which needs a paused loop to read OFF after undo_all."""
        e = self.e
        self._take(0, 0.5)
        e.hit(0, "pause_on")
        self.assertTrue(e.wait_state(0, SL_STATE_PAUSED, 2.0))
        e.hit(0, "undo_all")
        self.assertTrue(e.wait_state(0, SL_STATE_OFF, 2.0))

    # --- Stop All -----------------------------------------------------------------
    def test_stop_all_pauses_the_clip_and_mutes_the_empty_pads(self) -> None:
        """track_gesture.stop_all_loops, driven for real. verify_stop_all's
        premise: the empty pads read an EMPTY state afterwards -- 20, because
        Stop All's mute_on reaches them too -- so they are never counted as
        'still active'. MEASURED on the Pi 2026-09-06; measured here."""
        e = self.e
        self._take(0, 0.5)
        stop_all_loops(e, num_loops=e.num_loops, gestures=[])
        self.assertTrue(e.wait_state(0, SL_STATE_PAUSED, 2.0))
        empties = {loop: e.state(loop) for loop in range(1, e.num_loops)}
        self.assertEqual(set(empties.values()), {SL_STATE_OFF_MUTED}, empties)

    def _stop_all_sends(self, *, trigger: bool) -> None:
        """The Stop All burst, with or without the `trigger` it carried until 2026-09-07."""
        e = self.e
        e.send_message("/sl/-1/set", ["mute_quantized", 0.0])
        e.send_message("/sl/-1/set", ["quantize", 0.0])
        e.send_message("/sl/-1/hit", "mute_on")
        if trigger:
            e.send_message("/sl/-1/hit", "trigger")
        e.send_message("/sl/-1/hit", "pause_on")

    def test_stop_all_pauses_a_clip_on_the_grid(self) -> None:
        """Mitch, 2026-09-06: 'After I stop all clips, it just resumes again.'

        `stop_all_loops` as production sends it, at a clip on an established
        grid, mid-bar. MEASURED 2026-09-07, five bar phases: with the `trigger`
        the burst carried until that day the loop never read PAUSED at any
        20 ms sample, 0 of 5 (the next test keeps that reading); without it,
        5 of 5. This test was expectedFailure until the trigger came out.
        """
        e = self.e
        self._establish(2.0)
        time.sleep(0.5)
        stop_all_loops(e, num_loops=e.num_loops, gestures=[])
        time.sleep(0.4)
        self.assertEqual(e.state(0), SL_STATE_PAUSED)

    def test_a_trigger_in_the_stop_all_burst_keeps_the_clip_playing(self) -> None:
        """The known-bad burst, kept so the reading that removed the trigger
        stays on record and a reintroduction fails here: mute_on, trigger,
        pause_on with both quantizers at 0 leaves a grid clip PLAYING.
        MEASURED 2026-09-07, 5 of 5 bar phases, still playing a cycle later.
        """
        e = self.e
        grid = self._establish(2.0)
        time.sleep(0.5)
        self._stop_all_sends(trigger=True)
        time.sleep(0.4)
        self.assertEqual(e.state(0), SL_STATE_PLAYING)
        time.sleep(grid.cycle_s)
        self.assertEqual(e.state(0), SL_STATE_PLAYING)

    def test_stop_all_without_its_trigger_pauses_and_relaunches_from_the_top(self) -> None:
        """MEASURED 2026-09-07, five bar phases: the burst minus `trigger`
        leaves the clip PAUSED within 0.4 s, still PAUSED after the settle's
        quantize restore a second later, and `trigger` from PAUSED plays from
        the top (loop_pos < 0.05 s). The rewind the burst's `trigger` was
        added for on 2026-08-30 is already what `trigger` does at launch.
        """
        e = self.e
        grid = self._establish(2.0)
        time.sleep(0.9)                      # mid-bar, where the rewind would matter
        self._stop_all_sends(trigger=False)
        time.sleep(0.4)
        self.assertEqual(e.state(0), SL_STATE_PAUSED, "did not pause")
        e.send_message("/sl/-1/set", ["quantize", 1.0])     # settle_stop_all's restore
        e.send_message("/sl/-1/set", ["mute_quantized", 1.0])
        time.sleep(grid.cycle_s + 0.3)
        self.assertEqual(e.state(0), SL_STATE_PAUSED, "resumed after the restore")
        e.hit(0, "trigger")
        self.assertTrue(e.wait_state(0, SL_STATE_PLAYING, grid.cycle_s + 1.0))
        self.assertLess(e.get(0, "loop_pos"), 0.05, "relaunch did not start from the top")

    def test_trigger_lifts_a_mute_on_the_bar_from_the_top(self) -> None:
        """loop_model: a per-clip stop is `mute_on` (the loop keeps running,
        locked to the grid) and the launch is `trigger`. So the launch the
        pads send most often starts from MUTE, not PAUSED. MEASURED
        2026-09-07: with mute_quantized=1 the mute waits for the bar; from
        MUTE, `trigger` waits for the bar and plays from the top.
        """
        e = self.e
        grid = self._establish(2.0)
        time.sleep(0.9)
        e.hit(0, "mute_on")
        self.assertEqual(e.wait_state_in(0, {SL_STATE_MUTE, SL_STATE_PLAYING}, 0.3),
                         SL_STATE_PLAYING, "a quantized mute must not land mid-bar")
        self.assertTrue(e.wait_state(0, SL_STATE_MUTE, grid.cycle_s + 1.0))
        time.sleep(0.9)                      # mid-bar again, muted and running
        e.hit(0, "trigger")
        time.sleep(0.15)
        self.assertEqual(e.state(0), SL_STATE_MUTE, "trigger did not wait for the bar")
        self.assertTrue(e.wait_state(0, SL_STATE_PLAYING, grid.cycle_s + 1.0))
        self.assertLess(e.get(0, "loop_pos"), 0.1, "trigger did not start from the top")

    def test_trigger_from_paused_waits_for_the_bar_but_pause_off_does_not(self) -> None:
        """The launch a stopped pad wants: silent until the bar, then from the
        top. MEASURED 2026-09-07 with quantize=1 on a clip paused mid-bar:
        `trigger` alone stays PAUSED until the cycle boundary and then plays
        from loop_pos ~0.03; `pause_off` resumes at once from where it
        stopped (the 2026-08-30 'came back mid-loop' measurement, explained).
        """
        e = self.e
        grid = self._establish(2.0)
        time.sleep(1.2)
        self._stop_all_sends(trigger=False)
        time.sleep(0.3)
        e.send_message("/sl/-1/set", ["quantize", 1.0])
        time.sleep(0.2)
        stopped_at = e.get(0, "loop_pos")
        self.assertEqual(e.state(0), SL_STATE_PAUSED)
        self.assertGreater(stopped_at, 0.5, "the clip must be stopped mid-bar for this to mean anything")

        e.hit(0, "trigger")
        time.sleep(0.15)
        self.assertEqual(e.state(0), SL_STATE_PAUSED, "trigger did not wait for the bar")
        self.assertTrue(e.wait_state(0, SL_STATE_PLAYING, grid.cycle_s + 1.0))
        self.assertLess(e.get(0, "loop_pos"), 0.1, "trigger did not start from the top")

        # And the other verb, from a mid-bar stop again.
        time.sleep(1.0)
        self._stop_all_sends(trigger=False)
        time.sleep(0.3)
        e.send_message("/sl/-1/set", ["quantize", 1.0])
        time.sleep(0.2)
        e.hit(0, "pause_off")
        self.assertTrue(e.wait_state(0, SL_STATE_PLAYING, 0.5), "pause_off did not resume at once")
        self.assertGreater(e.get(0, "loop_pos"), 0.2, "pause_off restarted from the top")


if __name__ == "__main__":
    unittest.main()
