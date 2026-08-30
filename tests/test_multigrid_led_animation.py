"""Multigrid must animate a blink sequence, not freeze on its first frame.

Reported from the instrument, 2026-08-30:

    "we're getting red blinking during the tail capture rather than red and
     green blinking"

`led_for` returns a SEQUENCE; length 1 means hold it steady. `current_led`,
which is the multigrid paint path and therefore the one the appliance runs
(`MPE_SL_MULTIGRID=1`), returned `seq[0]` unconditionally. So every multi-phase
sequence collapsed to its first frame:

    TAIL_CAPTURE   = (RED, GREEN)             -> stuck RED
    RECORD_TO_PLAY = (OFF, RED, OFF, GREEN)   -> stuck OFF

Both are states the player acts on mid-take. The ring-out alternates red/green
precisely because that pattern appears nowhere else on the surface, and
`RECORD_TO_PLAY` alternates to say "recording is STILL RUNNING" while the take
closes — the thing Ableton drops and this looper deliberately does not.

Single-clip mode animated the same sequences correctly through `poll_led`, so
the surface disagreed with itself depending on a mode nobody changes — and the
half that was wrong was the half in use.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "sooperlooper"))

from led_table import (  # noqa: E402
    LED_GREEN,
    LED_RED,
    RECORD_TO_PLAY,
    TAIL_CAPTURE,
    led_for,
)
from sl_loop_states import SL_STATE_OVERDUBBING, SL_STATE_WAIT_STOP  # noqa: E402
import track_gesture as tg  # noqa: E402


class _Stub(tg.TrackGesture):
    """A gesture with no engine and no surface, painted by hand."""

    def __init__(self, sl_state: int, *, tail: bool) -> None:
        self.sl_state = sl_state
        self._pending = None
        self._tail = object() if tail else None
        self._led_transition = None
        self._pad_down = False
        self._hold_fired = False
        self._pad_down_at = 0.0
        self.hold_s = 2.0
        self.hold_blink_start_s = 0.5


def _frames_over_time(gesture) -> set[int]:
    """Every colour the pad shows across one full blink period."""
    seen = set()
    for tick in range(24):
        with mock.patch.object(
            tg.time, "monotonic",
            return_value=tick * tg.TRANSITION_BLINK_S,
        ):
            seen.add(gesture.current_led())
    return seen


class MultigridAnimatesSequencesTests(unittest.TestCase):
    def test_the_ring_out_alternates_red_and_green(self) -> None:
        seen = _frames_over_time(_Stub(SL_STATE_OVERDUBBING, tail=True))
        self.assertEqual(
            seen, {LED_RED, LED_GREEN},
            "the ring-out showed a single colour; it is the one alternating "
            "pattern on the surface and the player reads it mid-take",
        )

    def test_recording_to_playing_shows_both_colours(self) -> None:
        seen = _frames_over_time(_Stub(SL_STATE_WAIT_STOP, tail=False))
        self.assertIn(LED_RED, seen, "lost the 'still recording' half")
        self.assertIn(LED_GREEN, seen, "lost the 'will play' half")

    def test_a_steady_colour_stays_steady(self) -> None:
        """The other half of the contract: length 1 means hold.

        Without this a fix that animated everything would pass the two tests
        above while making every ordinary playing clip flicker.
        """
        gesture = _Stub(SL_STATE_OVERDUBBING, tail=False)
        seen = _frames_over_time(gesture)
        self.assertEqual(len(seen), 1, f"a steady state flickered: {seen}")

    def test_the_sequences_this_relies_on_really_are_multi_phase(self) -> None:
        """Positive control on the fixtures.

        If TAIL_CAPTURE ever became single-phase these tests would pass by
        asserting nothing about animation at all.
        """
        self.assertGreater(len(TAIL_CAPTURE), 1)
        self.assertGreater(len(RECORD_TO_PLAY), 1)
        self.assertEqual(led_for(SL_STATE_OVERDUBBING, tail=True), TAIL_CAPTURE)


if __name__ == "__main__":
    unittest.main()
