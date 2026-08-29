"""The ring-out, wired: gesture, listener routing, and the exits.

The unit tests in test_tail_phase.py cover the decision. These cover the parts
that were wrong the last time this existed — where the peaks were routed, and
whether the overdub was left on.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "sooperlooper"))

from track_gesture import TrackGesture  # noqa: E402
from sl_bench_listener import SlBenchStateListener  # noqa: E402
from sl_loop_states import (  # noqa: E402
    SL_STATE_OVERDUBBING,
    SL_STATE_PLAYING,
    SL_STATE_RECORDING,
)


class _Osc:
    def __init__(self) -> None:
        self.sent: list[tuple[str, list]] = []

    def send_message(self, path, args) -> None:
        self.sent.append((path, [args] if isinstance(args, str) else list(args)))

    def hits(self, loop: int = 0) -> list[str]:
        return [a[0] for p, a in self.sent if p == f"/sl/{loop}/hit"]


def gesture(osc: _Osc, *, loop: int = 0) -> TrackGesture:
    fs = TrackGesture(
        loop=loop, hold_ms=2000, debounce_ms=0, multigrid=True, quantized=True
    )
    fs.bind(osc, None, None)
    fs.loop_len = 2.0
    return fs


class TailLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.osc = _Osc()
        self.fs = gesture(self.osc)

    def _decay(self, *, base: float = 0.0) -> None:
        """A loud peak, then quiet for longer than the hold."""
        self.fs.sync_in_peak(0.8, now=base)
        for i in range(1, 40):
            self.fs.sync_in_peak(0.0, now=base + i * 0.01)

    def _enter_tail(self) -> None:
        self.fs.sync_from_sl(SL_STATE_RECORDING)
        self.fs.sync_from_sl(SL_STATE_OVERDUBBING)

    def test_closing_a_take_enters_the_tail(self) -> None:
        self._enter_tail()
        self.assertTrue(self.fs.in_tail)

    def test_a_decayed_note_leaves_the_overdub_once(self) -> None:
        self._enter_tail()
        self.osc.sent.clear()
        self._decay()
        self.assertEqual(
            self.osc.hits().count("overdub"), 1, "exactly one overdub-off"
        )
        self.assertFalse(self.fs.in_tail)

    def test_more_peaks_after_the_end_send_nothing(self) -> None:
        self._enter_tail()
        self._decay()
        self.osc.sent.clear()
        for i in range(50):
            self.fs.sync_in_peak(0.0, now=10.0 + i * 0.01)
        self.assertEqual(self.osc.hits(), [], "the phase is over")

    def test_the_engine_leaving_overdub_does_not_send_an_overdub_off(self) -> None:
        """That would toggle overdub back ON.

        Whatever ended it — the pad, the engine — already did the work.
        """
        self._enter_tail()
        self.osc.sent.clear()
        self.fs.sync_from_sl(SL_STATE_PLAYING)
        self.assertEqual(self.osc.hits(), [])
        self.assertFalse(self.fs.in_tail)

    def test_a_repeated_overdub_report_does_not_re_arm_the_tail(self) -> None:
        """The re-arm race, and it is the dangerous direction.

        `_begin_tail` ran on every OVERDUBBING report rather than on the
        transition, so one repeated or in-flight report re-armed the phase
        after it had already ended. The cap then sent `overdub` a second time —
        and `overdub` is a TOGGLE, so that turns it back ON with nothing armed
        to turn it off: a loop quietly recording the room behind a green pad.
        """
        self._enter_tail()
        self._decay()
        self.assertFalse(self.fs.in_tail)
        self.osc.sent.clear()

        # A stale report for a state the engine has already left.
        self.fs.sync_from_sl(SL_STATE_OVERDUBBING)
        self.assertFalse(self.fs.in_tail, "no transition, no new ring-out")

        # And nothing can now fire a second overdub.
        self.fs.poll_tail(now=1e6)
        self.assertEqual(
            self.osc.hits(), [], "a second overdub would start recording"
        )

    def test_peaks_before_any_tail_are_ignored(self) -> None:
        self.fs.sync_in_peak(0.9)
        self.fs.sync_in_peak(0.0)
        self.assertEqual(self.osc.hits(), [])


class PeakRoutingTests(unittest.TestCase):
    """Where the last implementation lost every peak.

    `on_update` looked the loop up in `_by_loop` and returned on None BEFORE
    reaching the peak branch, so `saw_loud` never set and the ring-out was cut
    at a fixed window regardless of the note's decay.
    """

    def test_a_peak_reaches_the_gesture(self) -> None:
        osc = _Osc()
        fs = gesture(osc)
        listener = SlBenchStateListener({0: fs})
        fs.sync_from_sl(SL_STATE_RECORDING)
        fs.sync_from_sl(SL_STATE_OVERDUBBING)
        listener.on_update("/x", 0, "in_peak_meter", 0.9)
        self.assertTrue(fs._tail.saw_loud, "the peak never arrived")

    def test_a_peak_for_an_unbound_loop_does_not_raise(self) -> None:
        listener = SlBenchStateListener({})
        listener.on_update("/x", 7, "in_peak_meter", 0.9)


if __name__ == "__main__":
    unittest.main()
