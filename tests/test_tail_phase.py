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
    TailPhase,
    bar_seconds,
)


def phase(*, cap_s: float = 2.0, thresh: float = 0.02, hold_s: float = 0.08):
    return TailPhase(started_at=0.0, cap_s=cap_s, thresh=thresh, hold_s=hold_s)


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

    def test_a_peak_exactly_at_the_threshold_counts_as_loud(self) -> None:
        p = phase(thresh=0.02)
        p.peak(0.02, 0.0)
        self.assertTrue(p.saw_loud)


class CapTests(unittest.TestCase):
    def test_one_bar_ends_it_even_with_no_meter_at_all(self) -> None:
        """The cap is the exit that does not depend on the peak feed.

        Every tail peak being silently dropped has happened here before. When
        it did, the only thing standing between the player and an endless
        overdub was a fixed window like this one.
        """
        p = phase(cap_s=2.0)
        self.assertIsNone(p.tick(1.99))
        self.assertEqual(p.tick(2.0), EXIT_CAP)

    def test_the_cap_holds_while_the_input_is_still_loud(self) -> None:
        """A ring-out longer than a bar is not a ring-out."""
        p = phase(cap_s=2.0)
        for t in range(0, 300):
            p.peak(0.9, t * 0.01)
        self.assertEqual(p.tick(2.5), EXIT_CAP)


class BarLengthTests(unittest.TestCase):
    def test_a_bar_comes_from_the_grid_when_there_is_one(self) -> None:
        self.assertAlmostEqual(bar_seconds(120.0), 2.0)
        self.assertAlmostEqual(bar_seconds(60.0), 4.0)

    def test_it_falls_back_to_the_loop_length(self) -> None:
        self.assertAlmostEqual(bar_seconds(None, loop_len=1.5), 1.5)

    def test_a_nonsense_tempo_does_not_produce_a_zero_cap(self) -> None:
        """A zero cap would end every ring-out instantly."""
        self.assertGreater(bar_seconds(0.0, loop_len=0.0), 0.0)
        self.assertGreater(bar_seconds(None, loop_len=0.0), 0.0)


if __name__ == "__main__":
    unittest.main()
