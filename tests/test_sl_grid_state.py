"""First-take grid establishment (standard looper workflow)."""

import unittest

from scripts.sooperlooper.sl_grid_state import GridState, derive_tempo


class DeriveTempoTests(unittest.TestCase):
    def test_two_second_take_is_one_bar_at_120(self) -> None:
        bpm, bars = derive_tempo(2.0)
        self.assertAlmostEqual(bpm, 120.0, places=3)
        self.assertEqual(bars, 1)

    def test_prefers_bpm_nearest_the_preferred_tempo(self) -> None:
        # 3.2 s is 2 bars @ 150 or 4 bars @ 75; 150 is nearer 120.
        bpm, bars = derive_tempo(3.2)
        self.assertEqual(bars, 2)
        self.assertAlmostEqual(bpm, 150.0, places=3)

    def test_whole_bars_only(self) -> None:
        for length in (1.7, 2.0, 3.2, 4.0, 5.5, 8.0):
            bpm, bars = derive_tempo(length)
            self.assertEqual(bars, int(bars))
            # bpm and bars must reconstruct the take length exactly
            self.assertAlmostEqual((bars * 4) * 60.0 / bpm, length, places=6)

    def test_stays_in_plausible_range_when_possible(self) -> None:
        for length in (1.6, 2.0, 3.0, 4.0, 6.0, 8.0):
            bpm, _ = derive_tempo(length)
            self.assertGreaterEqual(bpm, 70.0)
            self.assertLessEqual(bpm, 160.0)

    def test_absurd_take_falls_back_rather_than_refusing(self) -> None:
        self.assertIsNotNone(derive_tempo(0.2))
        self.assertIsNotNone(derive_tempo(120.0))

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
