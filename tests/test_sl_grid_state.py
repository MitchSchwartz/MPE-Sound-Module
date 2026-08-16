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


if __name__ == "__main__":
    unittest.main()


class GridDroppedWhenEmptyTests(unittest.TestCase):
    """No clips, no grid — however they were cleared."""

    def _established(self):
        g = GridState()
        g.arm(0)
        g.establish(0, 2.0)
        g.note_loop_content(0, True)
        return g

    def test_clearing_the_last_clip_drops_the_grid(self) -> None:
        g = self._established()
        self.assertTrue(g.note_loop_content(0, False))
        self.assertFalse(g.established)
        self.assertIsNone(g.bpm)

    def test_grid_survives_while_any_clip_remains(self) -> None:
        g = self._established()
        g.note_loop_content(1, True)
        self.assertFalse(g.note_loop_content(0, False))
        self.assertTrue(g.established)

    def test_clearing_pads_one_by_one_matches_a_track_reset(self) -> None:
        g = self._established()
        g.note_loop_content(1, True)
        g.note_loop_content(2, True)
        g.note_loop_content(1, False)
        g.note_loop_content(2, False)
        self.assertTrue(g.note_loop_content(0, False))
        self.assertFalse(g.established)
        self.assertTrue(g.arm(4), "next take must be free to define a new grid")

    def test_does_not_drop_while_a_defining_take_is_pending(self) -> None:
        g = GridState()
        g.arm(0)
        self.assertFalse(g.note_loop_content(0, False))
        self.assertTrue(g.is_pending(0))
