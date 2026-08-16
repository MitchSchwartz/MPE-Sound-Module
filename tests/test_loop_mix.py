import unittest

import loop_mix
from apc_faders import CC_MAX, MASTER
from loop_mix import CoalescingSender, LoopMix, fader_taper


def _picked_up(mix, fader):
    """Anchor relative pickup so subsequent moves apply delta."""
    mix.messages_for(fader, mix._pickup_ref.get(fader, CC_MAX))
    return mix


class Taper(unittest.TestCase):
    def test_bottom_of_travel_is_actual_silence(self):
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
    def test_master_scales_every_loop_over_per_loop_wet(self):
        msgs = LoopMix(num_loops=16).messages_for(MASTER, 100)
        self.assertEqual([p for p, _ in msgs], [f"/sl/{n}/set" for n in range(16)])

    def test_master_at_full_is_identity(self):
        mix = LoopMix()
        mix.messages_for(MASTER, CC_MAX)
        self.assertAlmostEqual(mix.wet_for(0), 1.0)

    def test_master_composes_multiplicatively_with_user_gain(self):
        mix = _picked_up(LoopMix(), 0)
        mix.messages_for(0, 100)
        alone = mix.wet_for(0)
        mix.messages_for(MASTER, 100)
        expected = alone * fader_taper(100, floor_db=-40.0, ceil_db=0.0)
        self.assertAlmostEqual(mix.wet_for(0), expected)

    def test_master_is_not_gated_by_column_pickup(self):
        self.assertTrue(LoopMix().messages_for(MASTER, 40))

    def test_master_is_exempt_from_pickup(self):
        self.assertTrue(LoopMix().messages_for(MASTER, 5))


class Pickup(unittest.TestCase):
    def test_first_touch_anchors_without_changing_level(self):
        mix = LoopMix()
        self.assertEqual(mix.messages_for(0, 40), [])
        self.assertEqual(mix.user_gain[0], CC_MAX)

    def test_relative_movement_after_anchor_applies_delta(self):
        mix = LoopMix()
        mix.messages_for(0, 40)  # anchor at 40, ref 127
        msgs = mix.messages_for(0, 30)  # delta -10 → effective 117
        self.assertTrue(msgs)
        self.assertEqual(mix.user_gain[0], 117)

    def test_misaligned_fader_does_not_jump_on_grab(self):
        mix = LoopMix()
        mix.messages_for(0, 10)
        self.assertEqual(mix.user_gain[0], CC_MAX)
        mix.messages_for(0, 5)
        self.assertEqual(mix.user_gain[0], 122)

    def test_suppressed_anchor_does_not_change_stored_gain(self):
        mix = LoopMix()
        mix.messages_for(0, 10)
        self.assertEqual(mix.user_gain[0], CC_MAX)

    def test_engine_seed_rearms_pickup_for_that_column(self):
        mix = _picked_up(LoopMix(), 0)
        mix.messages_for(0, 100)
        mix._pickup_anchor.pop(0, None)  # idle — no hand on fader
        mix.seed_from_engine(8, 0.25)
        self.assertEqual(mix.messages_for(0, 100), [])

    def test_engine_seed_ignored_while_column_fader_is_in_hand(self):
        mix = _picked_up(LoopMix(), 0)
        mix.messages_for(0, 100)
        before = mix.user_gain[0]
        mix.seed_from_engine(8, 0.25)  # lagging echo must not re-arm pickup
        self.assertEqual(mix.user_gain[0], before)
        self.assertTrue(mix.messages_for(0, 90))

    def test_engine_echoing_our_own_value_does_not_rearm_pickup(self):
        mix = _picked_up(LoopMix(), 0)
        mix.messages_for(0, 100)
        for _ in range(20):
            mix.seed_from_engine(0, mix.wet_for(0))
            mix.seed_from_engine(8, mix.wet_for(8))
        self.assertTrue(mix.messages_for(0, 90))

    def test_master_echo_does_not_corrupt_column_fader_gain(self):
        mix = _picked_up(LoopMix(), 0)
        mix.messages_for(0, 100)
        before = mix.user_gain[0]
        mix.messages_for(MASTER, 64)
        for loop in range(mix.num_loops):
            mix.seed_from_engine(loop, mix.wet_for(loop))
        self.assertEqual(mix.user_gain[0], before)
        self.assertTrue(mix.messages_for(0, 90))

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
            lambda path, args: self.sent.append((path, args)),
            interval_s=1.0,
            smooth_tau_s=0.0,
        )

    def test_bursts_are_collapsed_to_one_send_per_path(self):
        for i, value in enumerate([0.1, 0.2, 0.3, 0.4]):
            self.sender.submit([("/sl/0/set", ["wet", value])], now=i * 0.01)
        self.assertEqual(len(self.sent), 1)

    def test_the_endpoint_value_always_survives(self):
        for i, value in enumerate([0.1, 0.2, 0.3, 0.99]):
            self.sender.submit([("/sl/0/set", ["wet", value])], now=i * 0.01)
        self.sender.flush(now=99.0)
        self.assertEqual(self.sent[-1], ("/sl/0/set", ["wet", 0.99]))

    def test_distinct_paths_are_not_collapsed_into_each_other(self):
        self.sender.submit(
            [("/sl/0/set", ["wet", 1.0]), ("/sl/8/set", ["wet", 1.0])], now=0.0
        )
        self.sender.flush(now=5.0)
        self.assertEqual({p for p, _ in self.sent}, {"/sl/0/set", "/sl/8/set"})

    def test_flush_with_nothing_pending_is_silent(self):
        self.sender.flush(now=1.0)
        self.assertEqual(self.sent, [])


class Smoothing(unittest.TestCase):
    def test_ramps_toward_target_over_ticks(self):
        sent = []
        sender = CoalescingSender(
            lambda path, args: sent.append(args[1]),
            interval_s=0.0,
            smooth_tau_s=0.05,
            smooth_snap=0.001,
        )
        sender.submit([("/sl/0/set", ["wet", 0.0])], now=0.0)
        sent.clear()
        sender.submit([("/sl/0/set", ["wet", 1.0])], now=0.0)
        sender.tick(now=0.01)
        sender.tick(now=0.02)
        self.assertTrue(sent)
        self.assertLess(sent[-1], 1.0)
        sender.flush(now=0.2)
        self.assertAlmostEqual(sent[-1], 1.0)

    def test_snap_when_disabled(self):
        sent = []
        sender = CoalescingSender(
            lambda path, args: sent.append(args[1]),
            interval_s=0.0,
            smooth_tau_s=0.0,
        )
        sender.submit([("/sl/0/set", ["wet", 0.75])], now=0.0)
        self.assertEqual(sent[-1], 0.75)

    def test_first_send_ramps_from_seeded_engine_level(self):
        sent = []
        sender = CoalescingSender(
            lambda path, args: sent.append(args[1]),
            interval_s=0.0,
            smooth_tau_s=0.05,
            smooth_snap=0.001,
        )
        sender.seed_current("/sl/0/set", 1.0)
        sender.submit([("/sl/0/set", ["wet", 0.5])], now=0.0)
        sender.tick(now=0.01)
        self.assertTrue(sent)
        self.assertGreater(sent[-1], 0.5)
        self.assertLess(sent[-1], 1.0)


if __name__ == "__main__":
    unittest.main()
