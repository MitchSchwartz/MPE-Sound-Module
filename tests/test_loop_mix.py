import unittest

import loop_mix
from apc_faders import CC_MAX, MASTER
from loop_mix import CoalescingSender, LoopMix, fader_taper


def _picked_up(mix, fader):
    """Move a fader onto its stored value so pickup stops suppressing it."""
    mix.messages_for(fader, mix.user_gain[fader])
    return mix


class Taper(unittest.TestCase):
    def test_bottom_of_travel_is_actual_silence(self):
        # A log law cannot reach zero; without the snap the fader bottoms out
        # audible, which reads as a bug rather than as a taper.
        self.assertEqual(fader_taper(0, floor_db=-40.0, ceil_db=0.0), 0.0)

    def test_top_of_travel_is_unity(self):
        self.assertAlmostEqual(fader_taper(CC_MAX, floor_db=-40.0, ceil_db=0.0), 1.0)

    def test_monotonic_across_travel(self):
        values = [fader_taper(r, floor_db=-40.0, ceil_db=0.0) for r in range(128)]
        self.assertEqual(values, sorted(values))

    def test_even_db_per_step_in_the_middle(self):
        def db(raw):
            import math

            return 20.0 * math.log10(fader_taper(raw, floor_db=-40.0, ceil_db=0.0))

        first = db(64) - db(32)
        second = db(96) - db(64)
        self.assertAlmostEqual(first, second, places=6)


class ColumnMapping(unittest.TestCase):
    def test_fader_writes_both_loops_in_its_column(self):
        mix = _picked_up(LoopMix(), 3)
        paths = [p for p, _ in mix.messages_for(3, 100)]
        self.assertEqual(paths, ["/sl/3/set", "/sl/11/set"])

    def test_fader_zero_writes_loops_zero_and_eight(self):
        mix = _picked_up(LoopMix(), 0)
        paths = [p for p, _ in mix.messages_for(0, 100)]
        self.assertEqual(paths, ["/sl/0/set", "/sl/8/set"])

    def test_loops_beyond_num_loops_are_not_addressed(self):
        mix = _picked_up(LoopMix(num_loops=8), 2)
        paths = [p for p, _ in mix.messages_for(2, 100)]
        self.assertEqual(paths, ["/sl/2/set"])

    def test_out_of_range_fader_emits_nothing(self):
        self.assertEqual(LoopMix().messages_for(9, 100), [])


class Master(unittest.TestCase):
    def test_master_writes_the_engine_global_not_a_loop(self):
        msgs = LoopMix().messages_for(MASTER, CC_MAX)
        self.assertEqual(len(msgs), 1)
        path, args = msgs[0]
        self.assertEqual(path, "/set")
        self.assertEqual(args[0], loop_mix.SL_MASTER_CONTROL)
        self.assertAlmostEqual(args[1], 1.0)

    def test_master_is_exempt_from_pickup(self):
        # There is no per-loop truth to seed it from, so suppressing it would
        # leave the master permanently inert.
        self.assertTrue(LoopMix().messages_for(MASTER, 5))


class Pickup(unittest.TestCase):
    def test_fader_is_ignored_until_it_crosses_the_stored_value(self):
        mix = LoopMix()  # every loop seeded at CC_MAX
        self.assertEqual(mix.messages_for(0, 10), [])
        self.assertEqual(mix.messages_for(0, 60), [])

    def test_fader_writes_once_it_has_crossed(self):
        mix = LoopMix()
        self.assertEqual(mix.messages_for(0, 10), [])
        self.assertTrue(mix.messages_for(0, CC_MAX))
        self.assertTrue(mix.messages_for(0, 10))  # now live

    def test_suppressed_movement_does_not_change_stored_gain(self):
        mix = LoopMix()
        mix.messages_for(0, 10)
        self.assertEqual(mix.user_gain[0], CC_MAX)

    def test_engine_seed_rearms_pickup_for_that_column(self):
        mix = _picked_up(LoopMix(), 0)
        self.assertTrue(mix.messages_for(0, 100))
        mix.seed_from_engine(8, 0.25)  # loop 8 is column 0
        self.assertEqual(mix.messages_for(0, 100), [])

    def test_engine_seed_round_trips_through_the_taper(self):
        mix = LoopMix()
        mix.seed_from_engine(0, 0.5)
        self.assertAlmostEqual(mix.wet_for(0), 0.5, places=2)


class Composition(unittest.TestCase):
    def test_wet_is_user_gain_when_the_law_is_off(self):
        mix = _picked_up(LoopMix(), 0)
        mix.messages_for(0, CC_MAX)
        self.assertAlmostEqual(mix.wet_for(0), 1.0)

    def test_law_scales_every_loop_and_never_reads_back(self):
        mix = LoopMix()
        mix.active_loops = 4
        with _law_enabled():
            self.assertAlmostEqual(mix.wet_for(0), 0.25)
            # Recomputed from user_gain each time, so it does not compound.
            self.assertAlmostEqual(mix.wet_for(0), 0.25)

    def test_loop_count_change_reemits_every_loop(self):
        mix = LoopMix(num_loops=16)
        msgs = mix.note_active_loops(3)
        self.assertEqual(len(msgs), 16)

    def test_unchanged_loop_count_emits_nothing(self):
        mix = LoopMix()
        mix.note_active_loops(3)
        self.assertEqual(mix.note_active_loops(3), [])

    def test_wet_stays_in_range(self):
        mix = LoopMix()
        mix.active_loops = 1
        with _law_enabled():
            self.assertLessEqual(mix.wet_for(0), 1.0)
            self.assertGreaterEqual(mix.wet_for(0), 0.0)


class _law_enabled:
    def __enter__(self):
        self._prev = loop_mix.AUTO_LAW_ENABLED
        loop_mix.AUTO_LAW_ENABLED = True

    def __exit__(self, *exc):
        loop_mix.AUTO_LAW_ENABLED = self._prev
        return False


class Coalescing(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.sender = CoalescingSender(
            lambda path, args: self.sent.append((path, args)), interval_s=1.0
        )

    def test_bursts_are_collapsed_to_one_send_per_path(self):
        for i, value in enumerate([10, 20, 30, 40]):
            self.sender.submit([("/sl/0/set", ["wet", value])], now=i * 0.01)
        self.assertEqual(len(self.sent), 1)

    def test_the_endpoint_value_always_survives(self):
        # Dropping the last value leaves the fader physically lying about the
        # level, which is worse than the flood the throttle prevents.
        for i, value in enumerate([10, 20, 30, 99]):
            self.sender.submit([("/sl/0/set", ["wet", value])], now=i * 0.01)
        self.sender.flush(now=99.0)
        self.assertEqual(self.sent[-1], ("/sl/0/set", ["wet", 99]))

    def test_distinct_paths_are_not_collapsed_into_each_other(self):
        self.sender.submit(
            [("/sl/0/set", ["wet", 1]), ("/sl/8/set", ["wet", 1])], now=0.0
        )
        self.sender.flush(now=5.0)
        self.assertEqual({p for p, _ in self.sent}, {"/sl/0/set", "/sl/8/set"})

    def test_flush_with_nothing_pending_is_silent(self):
        self.sender.flush(now=1.0)
        self.assertEqual(self.sent, [])


if __name__ == "__main__":
    unittest.main()
