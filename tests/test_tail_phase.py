"""The ring-out phase: when the overdub that captures a decay should stop.

Two of these correspond to failures that actually shipped:

  * every tail peak was dropped by a listener guard, `saw_loud` never set, and
    every ring-out was cut at a fixed window regardless of the note's decay
    (PI5-LOOPER-SEAM-WRAP.md, corrected 2026-08-26)
  * the end was inferred from `sl_state == OVERDUBBING` and fired at the wrap,
    which on a four-bar loop is four bars of live input over the take
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "sooperlooper"))

from tail_phase import (  # noqa: E402
    EXIT_CAP,
    EXIT_DECAY,
    EXIT_SILENT,
    append_trace,
    TailPhase,
    cap_for,
)


def phase(*, cap_s: float = 2.0, ratio: float = 0.1, floor: float = 0.002,
          hold_s: float = 0.08):
    return TailPhase(started_at=0.0, cap_s=cap_s, ratio=ratio, floor=floor,
                     hold_s=hold_s)


class SettleTests(unittest.TestCase):
    """Quiet does not count until something loud has happened."""

    def test_silence_before_the_note_does_not_end_the_tail(self) -> None:
        """At the instant the overdub starts the meter has reported nothing.

        Treating that as "decayed" would cut the ring-out to zero — the exact
        artefact the feature exists to remove.
        """
        p = phase()
        for t in range(20):
            self.assertIsNone(p.peak(0.0, t * 0.01))
        self.assertFalse(p.saw_loud)

    def test_a_loud_peak_arms_the_detector(self) -> None:
        p = phase()
        p.peak(0.5, 0.0)
        self.assertTrue(p.saw_loud)


class DecayTests(unittest.TestCase):
    def test_the_tail_ends_once_it_has_been_quiet_long_enough(self) -> None:
        p = phase(hold_s=0.08)
        p.peak(0.5, 0.00)
        self.assertIsNone(p.peak(0.001, 0.10), "quiet, but not yet for long")
        self.assertIsNone(p.peak(0.001, 0.15))
        self.assertEqual(p.peak(0.001, 0.19), EXIT_DECAY)

    def test_a_gap_between_two_plucks_does_not_end_it(self) -> None:
        """The hold exists so a rest inside the ring-out is not mistaken for
        the end of it."""
        p = phase(hold_s=0.08)
        p.peak(0.5, 0.00)
        p.peak(0.001, 0.10)
        self.assertIsNone(p.peak(0.4, 0.14), "loud again — the tail continues")
        self.assertIsNone(p.peak(0.001, 0.20), "the quiet clock restarts")
        self.assertEqual(p.peak(0.001, 0.29), EXIT_DECAY)

    def test_a_peak_exactly_at_the_noise_floor_counts_as_signal(self) -> None:
        p = phase(floor=0.002)
        p.peak(0.002, 0.0)
        self.assertTrue(p.saw_loud)


class CapTests(unittest.TestCase):
    def test_the_cap_ends_it_when_the_meter_stops_mid_tail(self) -> None:
        """The cap is the exit that does not depend on the peak feed.

        Every tail peak being silently dropped has happened here before. When
        it did, the only thing standing between the player and an endless
        overdub was a fixed window like this one.
        """
        p = phase(cap_s=2.0)
        p.peak(0.5, 0.0)                     # a real tail started
        self.assertIsNone(p.tick(1.99))
        self.assertEqual(p.tick(2.0), EXIT_CAP)

    def test_a_tail_that_never_rises_above_the_floor_stops_early(self) -> None:
        """Silence, or a meter that is not feeding, must not hold a live
        overdub open for a whole bar — that records the room."""
        p = phase(cap_s=2.0)
        self.assertIsNone(p.tick(0.39))
        self.assertEqual(p.tick(0.41), EXIT_SILENT)

    def test_the_cap_holds_while_the_input_is_still_loud(self) -> None:
        """A ring-out longer than a bar is not a ring-out."""
        p = phase(cap_s=2.0)
        for t in range(0, 300):
            p.peak(0.9, t * 0.01)
        self.assertEqual(p.tick(2.5), EXIT_CAP)


class CapLengthTests(unittest.TestCase):
    """The cap is ONE CYCLE.

    Rewritten 2026-08-30. This class used to assert `cap_for(120.0) ==
    (2.0, "one bar")` — a BPM in, a bar out. `looper-timing-model-spec.md` §6
    says the cap is **one cycle**, and §1 says `bar_s` "must never be used to
    place a boundary", so the old assertions contradicted canon at rank 3 and
    were the defect rather than the record. They were written when every first
    take was one bar, which `d06fb08` stopped being true on the same day.

    `bar_seconds` went with them: it was a third home for "one bar" beside
    `GridState.bar_s` and `patch_browser.looper_hud.bar_seconds`, with no
    production caller — a bar-shaped helper sitting in the module whose job is
    cycles, which is how the regression happened in the first place.
    """

    def test_the_cap_is_the_cycle_when_there_is_a_grid(self) -> None:
        self.assertEqual(cap_for(6.939), (6.939, "one cycle"))
        self.assertEqual(cap_for(2.0), (2.0, "one cycle"))

    def test_a_multi_bar_cycle_is_not_divided_by_its_bar_count(self) -> None:
        """The regression, stated as a number.

        A 6.939 s take reads as 4 bars at 138 BPM. Capping at one BAR gives
        1.735 s and truncates three quarters of the ring-out.
        """
        cap, source = cap_for(6.939)
        self.assertAlmostEqual(cap, 6.939)
        self.assertNotAlmostEqual(cap, (60.0 / 138.0) * 4, places=2)
        self.assertEqual(source, "one cycle")

    def test_it_falls_back_to_the_loop_length(self) -> None:
        cap, source = cap_for(None, loop_len=1.5)
        self.assertAlmostEqual(cap, 1.5)
        self.assertIn("no grid", source)

    def test_no_grid_and_no_length_does_not_produce_a_zero_cap(self) -> None:
        """A zero cap would end every ring-out instantly."""
        for cycle in (None, 0.0):
            cap, source = cap_for(cycle, loop_len=0.0)
            self.assertGreater(cap, 0.0)
            self.assertIn("fallback", source)

    def test_the_cap_reports_where_it_came_from(self) -> None:
        """The log said "capped at 4.078s (one bar)" on a take with no grid,
        where a bar would have been 2.0s. A log line that misattributes its own
        number cost real time decoding the first trace off the appliance."""
        cap, source = cap_for(None, loop_len=4.078)
        self.assertAlmostEqual(cap, 4.078)
        self.assertIn("no grid", source)
        _cap, source = cap_for(None, loop_len=0.0)
        self.assertIn("fallback", source)


class RelativeThresholdTests(unittest.TestCase):
    """The exit level comes from the tail's own peak, not from a fixed number.

    From the first trace off the appliance, 2026-08-29: a real ring-out peaked
    at 0.0487 and was cut at 0.0172, still audibly decaying, because the fixed
    threshold of 0.02 was 40% of the signal rather than "quiet".
    """

    def test_the_exit_level_scales_with_the_take(self) -> None:
        loud = phase(ratio=0.1)
        loud.peak(0.8, 0.0)
        self.assertAlmostEqual(loud.exit_level, 0.08)

        quiet = phase(ratio=0.1)
        quiet.peak(0.05, 0.0)
        self.assertAlmostEqual(quiet.exit_level, 0.005)

    def test_the_real_trace_is_not_cut_where_the_old_code_cut_it(self) -> None:
        """Replay of the take Mitch recorded on 2026-08-29.

        The fixed threshold ended this at 0.563s with the signal at 0.0172.
        Against its own peak that is only -9 dB — a third of the way down.
        """
        curve = [
            (0.019, 0.04873), (0.100, 0.04273), (0.201, 0.03591),
            (0.301, 0.02839), (0.402, 0.02241), (0.482, 0.02013),
            (0.563, 0.01718),
        ]
        p = phase(cap_s=4.078)
        for t, v in curve:
            self.assertIsNone(p.peak(v, t), f"cut short at {t}s, value {v}")
        self.assertAlmostEqual(p.peak_max, 0.04873)
        self.assertAlmostEqual(p.exit_level, 0.004873)

    def test_a_quiet_patch_still_arms_the_detector(self) -> None:
        """The failure the fixed threshold hid.

        A take peaking below the old 0.02 never set `saw_loud`, so it never
        decayed — it ran to the cap, silently, which is indistinguishable from
        a dead peak meter.
        """
        p = phase(ratio=0.1, floor=0.002, hold_s=0.08)
        for i in range(10):
            self.assertIsNone(p.peak(0.015, i * 0.02))
        self.assertTrue(p.saw_loud, "0.015 is real signal, just a quiet take")
        self.assertIsNone(p.peak(0.001, 0.30))
        self.assertEqual(p.peak(0.001, 0.40), EXIT_DECAY)

    def test_the_floor_stops_an_asymptotic_decay_running_forever(self) -> None:
        """A decay that flattens out above its own -20 dB would never reach a
        purely relative exit level."""
        p = phase(ratio=0.1, floor=0.002)
        p.peak(0.01, 0.0)
        self.assertAlmostEqual(p.exit_level, 0.002,
                               msg="relative level would be 0.001, below the "
                                   "floor — the floor wins")


class TailTraceTests(unittest.TestCase):
    """The trace exists to replace inherited thresholds with measured ones.

    `TAIL_THRESH` and `TAIL_HOLD_S` came from the seam-weld work, on a
    different signal path. Nothing has confirmed them against the synth
    patches actually being played, and a threshold that is wrong in the quiet
    direction is invisible: every tail simply exits on the cap instead, which
    is exactly what a DEAD peak meter also looks like.
    """

    def test_tracing_is_off_unless_asked_for(self) -> None:
        """Off must cost nothing — no list, no growth during a long tail."""
        tail = TailPhase(started_at=0.0, cap_s=2.0)
        self.assertIsNone(tail.trace)
        for i in range(500):
            tail.peak(0.5, i * 0.025)
        self.assertIsNone(tail.trace)

    def test_a_traced_tail_keeps_every_sample_with_its_offset(self) -> None:
        tail = TailPhase(started_at=10.0, cap_s=2.0, trace=True)
        tail.peak(0.5, 10.0)
        tail.peak(0.1, 10.025)
        self.assertEqual(len(tail.trace), 2)
        self.assertEqual([v for _t, v in tail.trace], [0.5, 0.1])
        self.assertAlmostEqual(tail.trace[0][0], 0.0)
        self.assertAlmostEqual(tail.trace[1][0], 0.025)

    def test_the_written_file_is_readable_as_flat_csv(self) -> None:
        import csv
        import tempfile
        from pathlib import Path as _Path

        tail = TailPhase(started_at=0.0, cap_s=2.0, floor=0.002, trace=True)
        tail.peak(0.5, 0.0)
        tail.peak(0.001, 0.025)
        with tempfile.TemporaryDirectory() as tmp:
            path = str(_Path(tmp) / "tails.csv")
            self.assertIsNone(append_trace(path, tail_id=1, loop=3, tail=tail,
                                           reason=EXIT_DECAY, elapsed=0.1))
            # A second tail appends without repeating the header.
            self.assertIsNone(append_trace(path, tail_id=2, loop=3, tail=tail,
                                           reason=EXIT_CAP, elapsed=2.0))
            with open(path) as handle:
                rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]["loop"], "3")
        self.assertEqual(rows[0]["exit_reason"], EXIT_DECAY)
        self.assertEqual(rows[-1]["exit_reason"], EXIT_CAP)
        self.assertEqual([r["tail_id"] for r in rows], ["1", "1", "2", "2"])

    def test_an_unwritable_path_is_reported_not_swallowed(self) -> None:
        """A trace that silently fails to write is a measurement that lies
        about having been taken."""
        tail = TailPhase(started_at=0.0, cap_s=2.0, trace=True)
        tail.peak(0.5, 0.0)
        failure = append_trace("/proc/nonexistent/tails.csv", tail_id=1, loop=0,
                               tail=tail, reason=EXIT_DECAY, elapsed=0.1)
        self.assertIsNotNone(failure)
        self.assertIn("tails.csv", failure)


if __name__ == "__main__":
    unittest.main()
