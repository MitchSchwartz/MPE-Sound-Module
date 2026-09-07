"""First-take grid establishment (standard looper workflow)."""

import unittest

from scripts.sooperlooper.sl_grid_state import (
    MIN_DEFINING_TAKE_S,
    GridState,
    derive_tempo,
    display_bpm,
)
from scripts.sooperlooper.sl_loop_states import (
    EMPTY_STATES,
    SL_STATE_OFF,
    SL_STATE_OFF_MUTED,
    SL_STATE_PAUSED,
)


class DeriveTempoTests(unittest.TestCase):
    """The take is the base unit; what is chosen is how it DIVIDES.

    Until 2026-08-30 the first take was called one bar unconditionally, so a
    6 s loop was 40 BPM and one of Mitch's real takes became 34.6 BPM. Two
    consequences beyond the silly label: the quantize unit is one bar, so clips
    could only join every 6 s; and SooperLooper doubles `eighth_cycle` below
    60 BPM on its own, so we were driving the engine into its odd corner nearly
    every session.
    """

    def test_the_grid_always_reconstructs_the_take_exactly(self) -> None:
        """Whatever bar count is chosen, the bars span the audio played.

        This is the invariant. Everything else here is a labelling decision;
        this one keeps the grid and the recording the same length.
        """
        for length in (1.0, 1.5, 2.0, 3.2, 4.0, 5.964, 8.0, 12.0, 30.0):
            bpm, bars = derive_tempo(length)
            self.assertAlmostEqual((bars * 4) * 60.0 / bpm, length, places=9,
                                   msg=f"{length}s reconstructs")

    def test_a_take_lands_on_a_plausible_tempo(self) -> None:
        for length in (1.5, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0, 30.0):
            bpm, _bars = derive_tempo(length)
            self.assertGreaterEqual(bpm, 60.0,
                                    f"{length}s: below 60 SL doubles eighth_cycle")
            self.assertLessEqual(bpm, 240.0, f"{length}s")

    def test_the_bar_count_is_the_musical_reading(self) -> None:
        self.assertEqual(derive_tempo(2.0), (120.0, 1))
        self.assertEqual(derive_tempo(4.0), (120.0, 2))
        self.assertEqual(derive_tempo(8.0), (120.0, 4))
        bpm, bars = derive_tempo(6.0)
        self.assertEqual((round(bpm), bars), (80, 2),
                         "a 6s loop is two bars at 80, not one bar at 40")

    def test_mitchs_real_takes_stop_producing_nonsense(self) -> None:
        """Measured on the appliance 2026-08-30. These four takes produced
        73.7, 34.6, 54.9 and 179.3 BPM — a tempo that walked because each one
        redefined the grid."""
        for length, was in ((3.257, 73.7), (6.939, 34.6), (4.373, 54.9)):
            bpm, _bars = derive_tempo(length)
            if was < 60.0:
                self.assertGreater(bpm, was, f"{length}s was {was} BPM")
            self.assertGreaterEqual(bpm, 60.0)

    def test_bar_counts_are_powers_of_two(self) -> None:
        """Nobody plays a three-bar phrase and then wonders why."""
        for length in (1.0, 2.5, 5.0, 7.0, 11.0, 19.0, 30.0):
            _bpm, bars = derive_tempo(length)
            self.assertIn(bars, (1, 2, 4, 8), f"{length}s -> {bars} bars")

    def test_tempo_is_exact_never_rounded(self) -> None:
        """Rounding the engine tempo makes the grid bar differ from the audio."""
        bpm, bars = derive_tempo(5.964)
        self.assertNotEqual(bpm, round(bpm))
        self.assertAlmostEqual((bars * 4) * 60.0 / bpm, 5.964, places=9)

    def test_display_rounds_but_engine_does_not(self) -> None:
        bpm, _bars = derive_tempo(5.964)
        self.assertEqual(display_bpm(bpm), round(bpm))
        self.assertNotEqual(bpm, float(display_bpm(bpm)))

    def test_rejects_nonsense(self) -> None:
        self.assertIsNone(derive_tempo(0.0))
        self.assertIsNone(derive_tempo(-1.0))


class DefiningTakeGuardTests(unittest.TestCase):
    """A take too short to be a bar must not become the bar.

    2026-09-06, from the journal: a 1.084 s first take was accepted as
    "1 bar @ 221.4 BPM" and the next take, held for 7.59 s, landed as
    exactly seven bars. The engine was right; the bar was wrong. The only
    guard was 20-300 BPM.
    """

    def test_the_seven_bar_take_is_refused_with_its_reason(self) -> None:
        g = GridState()
        self.assertTrue(g.arm(0))
        self.assertIsNone(g.establish(0, 1.084))
        self.assertFalse(g.established)
        self.assertIn("221.4 BPM", g.refused or "")
        self.assertIn("1.084s", g.refused or "")

    def test_a_refused_take_hands_the_grid_to_the_next_take(self) -> None:
        g = GridState()
        g.arm(0)
        g.establish(0, 1.084)
        self.assertFalse(g.is_pending(0), "still pending: every later arm would be refused")
        self.assertTrue(g.arm(1), "the next take could not arm")
        self.assertIsNone(g.refused, "arm must clear the stale reason")
        self.assertEqual(g.establish(1, 2.0), (120.0, 1))

    def test_the_evening_of_09_06_take_by_take(self) -> None:
        """Four of the five grids that evening are refused; the 1.802 s and
        3.697 s ones stand. The threshold is one bar of four at 160 BPM."""
        for take, accepted in ((1.285, False), (1.084, False), (1.802, True),
                               (1.225, False), (3.697, True)):
            g = GridState()
            g.arm(0)
            self.assertEqual(g.establish(0, take) is not None, accepted, take)

    def test_the_threshold_itself_is_accepted(self) -> None:
        g = GridState()
        g.arm(0)
        self.assertEqual(g.establish(0, MIN_DEFINING_TAKE_S), (160.0, 1))

    def test_derive_tempo_is_untouched(self) -> None:
        """The guard is about DEFINING. The arithmetic still reads a short
        take as one bar; a song restored with such a grid is `restore`'s
        business, not this one's."""
        self.assertEqual(derive_tempo(1.084)[1], 1)


class GridStateTests(unittest.TestCase):
    def test_first_take_establishes_then_grid_is_independent(self) -> None:
        g = GridState()
        self.assertFalse(g.established)
        self.assertTrue(g.arm(0))
        self.assertEqual(g.establish(0, 2.0), (120.0, 1))
        self.assertTrue(g.established)
        self.assertEqual(g.defined_by, 0)

    def test_any_clip_can_define_the_grid_not_just_clip_zero(self) -> None:
        g = GridState()
        self.assertTrue(g.arm(5))
        self.assertIsNotNone(g.establish(5, 2.0))
        self.assertEqual(g.defined_by, 5)

    def test_only_one_take_can_be_pending(self) -> None:
        g = GridState()
        self.assertTrue(g.arm(0))
        self.assertFalse(g.arm(1))

    def test_second_clip_cannot_redefine_an_established_grid(self) -> None:
        g = GridState()
        g.arm(0)
        g.establish(0, 2.0)
        self.assertFalse(g.arm(1))
        self.assertIsNone(g.establish(1, 5.0))
        self.assertAlmostEqual(g.bpm, 120.0)

    def test_cancelled_take_frees_the_grid_for_the_next_one(self) -> None:
        g = GridState()
        g.arm(0)
        g.cancel(0)
        self.assertTrue(g.arm(1))

    def test_reset_returns_to_no_grid(self) -> None:
        g = GridState()
        g.arm(0)
        g.establish(0, 2.0)
        g.reset()
        self.assertFalse(g.established)
        self.assertIsNone(g.bpm)
        self.assertTrue(g.arm(3))


class StopKeepsTheGridClearDropsItTests(unittest.TestCase):
    """Stop and clear are different gestures and must have different effects.

    This class has been inverted twice, so both readings are kept visible.

    2026-08-30 it asserted "the grid survives an empty session", on this from
    Mitch:

        "Even if we stop all clips, and even if the second track is two bars
        compared to the established base unit length that the first recorded
        clip establishes, we still need to reinitialize with those original
        settings. They should never be cleared away."

    2026-09-06, the same instrument, the same owner:

        "That's wrong. If we clear all clips, then the grid should be cleared.
        If we stop all clips, that's different. That might have been where the
        confusion came from."

    It was. The first quote says STOP ALL and the code applied it to CLEAR. So
    the rule is neither "always keep" nor "always drop": stop keeps, clear
    drops, and the engine state is what tells them apart.
    """

    def _established(self):
        g = GridState()
        g.arm(0)
        g.establish(0, 2.0)
        g.note_loop_content(0, True)
        return g

    # --- clear drops -------------------------------------------------------
    def test_clearing_the_last_clip_drops_the_grid(self) -> None:
        g = self._established()
        self.assertTrue(g.note_loop_content(0, False),
                        "clearing the last clip must drop the grid")
        self.assertFalse(g.established)
        self.assertIsNone(g.bpm)

    def test_after_a_clear_the_next_take_defines_a_new_grid(self) -> None:
        """The point of dropping it: the next take is free-form again.

        This is the symptom Mitch hit on 2026-09-06 — a cleared session whose
        next 'first' clip was still counted in and quantized to a 1.084 s bar.
        """
        g = self._established()
        g.note_loop_content(0, False)
        self.assertTrue(g.arm(0), "a cleared session takes a new defining take")

    def test_grid_survives_while_any_clip_remains(self) -> None:
        g = self._established()
        g.note_loop_content(1, True)
        self.assertFalse(g.note_loop_content(0, False))
        self.assertTrue(g.established)

    def test_clearing_pads_one_by_one_drops_only_on_the_last(self) -> None:
        g = self._established()
        for loop in (1, 2):
            g.note_loop_content(loop, True)
        self.assertFalse(g.note_loop_content(0, False))
        self.assertFalse(g.note_loop_content(1, False))
        self.assertTrue(g.note_loop_content(2, False), "the last one drops it")
        self.assertFalse(g.established)

    # --- stop keeps --------------------------------------------------------
    def test_stop_all_keeps_the_grid(self) -> None:
        """Paused loops still hold audio, so the session still has a tempo.

        `stop_all_loops` pauses rather than clearing, so a loop with a take in
        it reports PAUSED (14) — occupied. This is the case the 2026-08-30
        quote was actually about.
        """
        g = self._established()
        self.assertFalse(
            g.note_loop_content(0, SL_STATE_PAUSED not in EMPTY_STATES),
            "Stop All must not drop the grid",
        )
        self.assertTrue(g.established)
        self.assertEqual(g.bpm, 120.0)

    def test_stop_all_does_not_make_empty_pads_look_occupied(self) -> None:
        """The trap that would have wedged the grid on forever.

        Stop All sends `mute_on` to /sl/-1, so a loop that never held a take
        reports OFF_MUTED (20), not OFF (0). Classifying occupancy as
        `!= SL_STATE_OFF` would count all fourteen unused pads as occupied and
        no clear could ever empty the session again.
        """
        self.assertIn(SL_STATE_OFF_MUTED, EMPTY_STATES)
        self.assertIn(SL_STATE_OFF, EMPTY_STATES)
        self.assertNotIn(SL_STATE_PAUSED, EMPTY_STATES)

        g = self._established()
        for empty_loop in range(1, 15):
            g.note_loop_content(empty_loop, SL_STATE_OFF_MUTED not in EMPTY_STATES)
        self.assertTrue(g.note_loop_content(0, False),
                        "the only real clip was cleared — the grid must drop")

    def test_stop_then_clear_still_drops(self) -> None:
        """The full gesture Mitch described: stop everything, then clear it."""
        g = self._established()
        g.note_loop_content(0, SL_STATE_PAUSED not in EMPTY_STATES)
        self.assertTrue(g.established, "stop alone keeps it")
        self.assertTrue(g.note_loop_content(0, SL_STATE_OFF not in EMPTY_STATES))
        self.assertFalse(g.established, "the clear after the stop drops it")

    # --- reset is unchanged ------------------------------------------------
    def test_an_explicit_reset_still_clears_it(self) -> None:
        g = self._established()
        g.reset()
        self.assertFalse(g.established)
        self.assertIsNone(g.bpm)
        self.assertIsNone(g.phase_zero_at)
        self.assertTrue(g.arm(4), "after a reset the next take defines a grid")


class TheCycleIsTheFirstTakeTests(unittest.TestCase):
    """The quantize unit is the take itself, whatever tempo we call it.

    Nearly shipped broken on 2026-08-30. Fitting the BPM to a plausible range
    means a 6.939 s take reads as 4 bars at 138 BPM — and if the boundary is
    then computed as ONE BAR, clips join four times inside the loop the player
    thinks of as a single unit. Mitch caught it in review:

        "A six second clip reading as 138 BPM in four bars — I don't know why
        it's inherently four bars. If it's my first clip, it should still be
        one bar. I just want to make sure we're not misaligning again."

    Bar count and BPM describe the cycle. They do not divide it.
    """

    def _established(self, take: float) -> GridState:
        g = GridState()
        g.arm(0)
        g.establish(0, take)
        g.mark_phase_zero(0.0)
        return g

    def test_the_boundary_is_the_take_however_many_bars_it_reads_as(self) -> None:
        for take in (2.0, 3.257, 4.0, 6.0, 6.939, 8.0, 12.0, 30.0):
            g = self._established(take)
            self.assertAlmostEqual(
                g.next_boundary(0.0), take, places=6,
                msg=f"{take}s take read as {g.bars} bars @ {g.bpm:.1f} BPM",
            )

    def test_the_engine_cycle_matches_the_bench_boundary(self) -> None:
        """SL computes cycle = eighth_per_cycle * 30 / bpm. Left at a fixed 8
        while the tempo rises, the ENGINE quantizes to a fraction of the take
        even if the bench does not — the two would disagree silently."""
        for take in (2.0, 4.0, 6.939, 12.0, 30.0):
            g = self._established(take)
            engine_cycle = g.eighth_per_cycle * 30.0 / g.bpm
            self.assertAlmostEqual(engine_cycle, take, places=6,
                                   msg=f"{take}s -> {g.bars} bars")

    def test_a_bar_is_still_reported_for_display(self) -> None:
        g = self._established(8.0)          # 120 BPM, 4 bars
        self.assertAlmostEqual(g.bar_s, 2.0)
        self.assertAlmostEqual(g.cycle_s, 8.0)
        self.assertNotAlmostEqual(g.bar_s, g.cycle_s,
                                  msg="the two are different things")


class GridClockTests(unittest.TestCase):
    """The grid can name its own bar line, with nothing playing.

    Before this the only boundary the bench had was a playing loop's wrap. So
    after Stop All there was no boundary at all and launches fired instantly,
    while the tempo sat there, known, unused.
    """

    def _running(self):
        g = GridState()
        g.arm(0)
        g.establish(0, 2.0)      # 120 BPM, one 2.0s bar
        g.mark_phase_zero(100.0)
        return g

    def test_the_next_bar_line_comes_from_the_tempo(self) -> None:
        g = self._running()
        self.assertAlmostEqual(g.cycle_s, 2.0)
        self.assertAlmostEqual(g.next_boundary(100.0), 102.0)
        self.assertAlmostEqual(g.next_boundary(101.9), 102.0)
        self.assertAlmostEqual(g.next_boundary(102.0), 104.0)
        self.assertAlmostEqual(g.next_boundary(107.5), 108.0)

    def test_no_boundary_without_a_grid(self) -> None:
        self.assertIsNone(GridState().next_boundary(100.0))

    def test_no_boundary_until_the_phase_is_known(self) -> None:
        """A tempo with no downbeat cannot place a bar line, and guessing one
        would put every launch on an arbitrary offset from the music."""
        g = GridState()
        g.arm(0)
        g.establish(0, 2.0)
        self.assertIsNone(g.next_boundary(100.0))


class PendingTakeTests(unittest.TestCase):
    def test_does_not_drop_while_a_defining_take_is_pending(self) -> None:
        g = GridState()
        g.arm(0)
        self.assertFalse(g.note_loop_content(0, False))
        self.assertTrue(g.is_pending(0))


if __name__ == "__main__":
    unittest.main()
