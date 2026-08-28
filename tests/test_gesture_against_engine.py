"""The gesture driven against a stateful engine, across time.

These are the tests that would have caught the defects the unit tests could
not: everything here depends on *when* the engine answers, and a mock always
answers immediately and agreeably.
"""

from __future__ import annotations

from tests import conftest  # noqa: F401 — bare sooperlooper imports (apc_grid, …)

import unittest
from unittest.mock import MagicMock

import scripts.sooperlooper.track_gesture as gesture_mod
from scripts.sooperlooper.track_gesture import TrackGesture
from scripts.sooperlooper.led_table import (
    LED_GREEN,
    LED_GREEN_BLINK,
    LED_OFF,
    LED_RED,
    LED_RED_BLINK,
    LED_YELLOW,
    LED_YELLOW_BLINK,
)
from scripts.sooperlooper.sl_grid_state import GridState
from scripts.sooperlooper.sl_loop_states import (
    SL_STATE_MUTE,
    SL_STATE_OFF,
    SL_STATE_OVERDUBBING,
    SL_STATE_PLAYING,
)
from tests.fake_sl_engine import FakeSlEngine


class TrackGestureOnEngineTests(unittest.TestCase):
    def _rig(self, *, quantized=True, grid=None, loop=0):
        engine = FakeSlEngine(quantized=quantized)
        midi = MagicMock()
        fs = TrackGesture(loop=loop, hold_ms=800, debounce_ms=0,
                            quantized=quantized, grid=grid)
        fs.bind(engine, midi, note=0)
        return engine, fs, midi

    def _led(self, midi):
        """Last velocity actually pushed to the surface."""
        calls = [c.args[0] for c in midi.send_message.call_args_list]
        return calls[-1][2] if calls else None

    def _tap(self, fs):
        fs.on_pad_down()
        fs.on_pad_up()

    # --- the headline bug ------------------------------------------------
    def test_pad_never_goes_solid_green_before_the_engine_confirms(self) -> None:
        """A solid green pad is a promise there is audio in that loop.

        The old bench painted green the moment it *sent* stop-record, so a pad
        sat solid green over a loop that had recorded nothing at all. That is
        the symptom that started this whole investigation.
        """
        engine, fs, midi = self._rig()

        self._tap(fs)                    # arm
        engine.poll(fs)
        self.assertEqual(self._led(midi), LED_RED_BLINK, "armed, not recording")

        engine.boundary()                # recording actually starts
        engine.poll(fs)
        self.assertEqual(self._led(midi), LED_RED)

        fs.on_pad_down()                 # closes the take into an overdub
        engine.poll(fs)
        self.assertNotEqual(self._led(midi), LED_GREEN,
                            "nothing has landed yet — green would be a lie")

        engine.boundary()                # the take lands
        engine.poll(fs)
        self.assertEqual(self._led(midi), LED_GREEN)
        self.assertEqual(engine.state[0], SL_STATE_OVERDUBBING,
                         "the take closed into an overdub capturing the ring-out")
        self.assertGreater(engine.loop_len[0], 0.0, "green means there is audio")

    def test_a_take_that_never_lands_does_not_leave_a_green_pad(self) -> None:
        """If the engine stops answering, the pad must not claim success."""
        engine, fs, midi = self._rig()
        self._tap(fs)
        engine.boundary()
        engine.poll(fs)
        self._tap(fs)                    # stop-record queued
        engine.poll(fs)

        # No boundary ever arrives — a stalled grid clock, or a wedged engine.
        for _ in range(5):
            engine.poll(fs)
        self.assertNotEqual(self._led(midi), LED_GREEN)

    # --- the double-tap idiom the optimistic state used to carry ----------
    def test_double_tap_while_armed_records_exactly_one_cycle(self) -> None:
        """Tap twice fast and you get one cycle, not a cancelled take.

        The second tap arrives before the engine has acknowledged the first, so
        the bench has to know what it asked for. Sending `record` here would
        reach the engine as CANCEL and lose the take entirely.
        """
        engine, fs, _ = self._rig()

        self._tap(fs)
        self._tap(fs)                    # both before any poll
        self.assertNotEqual(engine.state[0], SL_STATE_OFF,
                            "second tap must not have cancelled the arm")

        engine.boundary()                # recording begins; queued stop fires
        engine.poll(fs)
        engine.boundary()                # one cycle later, it lands
        engine.poll(fs)
        self.assertEqual(engine.state[0], SL_STATE_PLAYING)

    # --- polls must not clobber intent -----------------------------------
    def test_polls_during_a_queued_launch_do_not_cancel_the_blink(self) -> None:
        """The launch blink survived exactly one poll before this refactor."""
        engine, fs, midi = self._rig()
        engine.state[0] = SL_STATE_PLAYING
        engine.loop_len[0] = 2.0
        engine.poll(fs)
        self._tap(fs)                    # mute, queued
        engine.boundary()
        engine.poll(fs)                  # now muted

        self._tap(fs)                    # launch, queued to the bar
        for _ in range(4):               # 400ms of polls at the real cadence
            engine.poll(fs)
        self.assertEqual(self._led(midi), LED_GREEN_BLINK)

        engine.boundary()
        engine.poll(fs)
        self.assertEqual(self._led(midi), LED_GREEN)

    def test_an_intent_the_engine_never_honours_expires(self) -> None:
        """A command that vanishes must not latch the pad forever.

        "Engine still says Off" cannot be distinguished from "engine has not
        answered yet" — that ambiguity is the whole reason `pending` exists. So
        the resolution is time, not a contradiction: hold the intent long
        enough for a quantized action at a slow tempo, then defer to the engine
        and let the pad tell the truth. That is what a wedged or orphaned
        engine looks like from up here.
        """
        engine, fs, midi = self._rig()
        self._tap(fs)                     # expect recording
        engine.state[0] = SL_STATE_OFF    # command went nowhere
        engine.poll(fs)
        self.assertEqual(fs.state, "recording", "still inside the grace window")

        fs._pending_since -= gesture_mod.PENDING_TIMEOUT_S + 1
        engine.poll(fs)
        self.assertEqual(fs.state, "idle")
        self.assertEqual(self._led(midi), LED_OFF)

    # --- grid lifetime, end to end ---------------------------------------
    def test_grid_establishes_from_the_first_take_and_drops_with_the_last(self) -> None:
        grid = GridState()
        engine, fs, _ = self._rig(grid=grid, quantized=False)

        self._tap(fs)
        engine.poll(fs)
        self._tap(fs)                    # defining take: tail capture arms
        engine._finish_record(0)         # fake engine: take landed (tail flow is Pi-only)
        engine.poll(fs)
        self.assertTrue(grid.established, "first take defines the grid")

        fs.on_pad_down()
        fs.poll_hold_for_test = None
        fs._clear_loop()
        engine.poll(fs)
        self.assertFalse(grid.established, "no clips, no grid")


if __name__ == "__main__":
    unittest.main()
