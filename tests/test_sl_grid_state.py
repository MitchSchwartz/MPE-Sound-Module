"""First-take grid establishment (standard looper workflow)."""

import unittest

from scripts.sooperlooper.sl_grid_state import GridState, derive_tempo, display_bpm


class DeriveTempoTests(unittest.TestCase):
    def test_first_take_is_one_bar_by_definition(self) -> None:
        for length in (1.5, 2.0, 3.2, 5.964, 8.0):
            bpm, bars = derive_tempo(length)
            self.assertEqual(bars, 1, f"{length}s should be one bar")
            self.assertAlmostEqual(4 * 60.0 / bpm, length, places=6)

    def test_tempo_is_exact_never_rounded(self) -> None:
        """Rounding the engine tempo makes the grid bar differ from the audio."""
        bpm, _ = derive_tempo(5.964)
        self.assertNotEqual(bpm, round(bpm))
        self.assertAlmostEqual(bpm, 40.24145, places=4)
        # the bar must reconstruct the take exactly, or clips drift apart
        self.assertAlmostEqual(4 * 60.0 / bpm, 5.964, places=9)

    def test_display_rounds_but_engine_does_not(self) -> None:
        bpm, _ = derive_tempo(5.964)
        self.assertEqual(display_bpm(bpm), 40)
        self.assertAlmostEqual(bpm, 40.24145, places=4)

    def test_absurdly_long_take_falls_back_to_more_bars(self) -> None:
        bpm, bars = derive_tempo(30.0)
        self.assertGreater(bars, 1)
        self.assertGreaterEqual(bpm, 20.0)
        # the cycle still equals the whole take
        self.assertAlmostEqual((bars * 4) * 60.0 / bpm, 30.0, places=6)

    def test_rejects_nonsense(self) -> None:
        self.assertIsNone(derive_tempo(0.0))
        self.assertIsNone(derive_tempo(-1.0))


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


class GridSurvivesEmptyPadsTests(unittest.TestCase):
    """The base unit belongs to the session, not to whichever clips exist.

    This class asserted the opposite until 2026-08-30 ("no clips, no grid").
    Mitch overruled it, about his own instrument:

        "Even if we stop all clips, and even if the second track is two bars
        compared to the established base unit length that the first recorded
        clip establishes, we still need to reinitialize with those original
        settings. They should never be cleared away."

    The old policy is why a tempo went 73.7 -> 34.6 -> 54.9 -> 179.3 BPM across
    four consecutive takes: each one cleared the pads, dropped the grid, and
    redefined the base unit from whatever was played next.
    """

    def _established(self):
        g = GridState()
        g.arm(0)
        g.establish(0, 2.0)
        g.note_loop_content(0, True)
        return g

    def test_clearing_the_last_clip_keeps_the_grid(self) -> None:
        g = self._established()
        self.assertFalse(g.note_loop_content(0, False),
                         "clearing a pad must not drop the grid")
        self.assertTrue(g.established)
        self.assertEqual(g.bpm, 120.0)

    def test_grid_survives_while_any_clip_remains(self) -> None:
        g = self._established()
        g.note_loop_content(1, True)
        self.assertFalse(g.note_loop_content(0, False))
        self.assertTrue(g.established)

    def test_clearing_every_pad_one_by_one_still_keeps_the_tempo(self) -> None:
        g = self._established()
        for loop in (1, 2):
            g.note_loop_content(loop, True)
        for loop in (0, 1, 2):
            g.note_loop_content(loop, False)
        self.assertTrue(g.established, "an empty session still has its tempo")
        self.assertEqual(g.bpm, 120.0)
        self.assertFalse(g.arm(4),
                         "a later take cannot redefine the grid — it counts in "
                         "to the one that exists")

    def test_only_an_explicit_reset_clears_it(self) -> None:
        """Track reset is the one gesture that means "start over"."""
        g = self._established()
        g.note_loop_content(0, False)
        g.reset()
        self.assertFalse(g.established)
        self.assertIsNone(g.bpm)
        self.assertIsNone(g.phase_zero_at)
        self.assertTrue(g.arm(4), "after a reset the next take defines a grid")


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
        self.assertAlmostEqual(g.bar_s, 2.0)
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
