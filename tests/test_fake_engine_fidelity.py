"""The harness must be able to SEE the bugs that actually shipped.

Every entry here corresponds to a defect that reached the appliance while the
suite stayed green, because `FakeSlEngine` could not represent the thing that
was wrong. A fake that agrees with whatever it is sent is not a test double,
it is a mirror.
"""

from __future__ import annotations

import unittest

from tests.fake_sl_engine import FakeSlEngine
from scripts.sooperlooper.sl_loop_states import (
    SL_STATE_OFF,
    SL_STATE_PAUSED,
    SL_STATE_PLAYING,
)


class BufferOpVisibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = FakeSlEngine()

    def test_a_three_argument_load_reaches_the_buffer(self) -> None:
        self.engine.send_message("/sl/0/load_loop", ["/clips/a.wav", "", ""])
        self.assertEqual(self.engine.loaded.get(0), "/clips/a.wav")

    def test_a_one_argument_load_is_discarded(self) -> None:
        """The bug that cost a debugging session on 2026-08-27.

        SooperLooper registers the handler as `s:filename s:return_url
        s:error_path`. A one-argument message does not match the signature and
        is dropped with no reply and no error — so the bench's model advanced,
        both pads repainted, and the engine had simply never been told. The
        fake has to drop it too, or the test cannot tell the two apart.
        """
        self.engine.send_message("/sl/0/load_loop", "/clips/a.wav")
        self.assertEqual(self.engine.loaded, {})

    def test_a_load_leaves_the_clip_resident_but_stopped(self) -> None:
        self.engine.send_message("/sl/0/load_loop", ["/clips/a.wav", "", ""])
        self.assertEqual(self.engine.state[0], SL_STATE_PAUSED)
        self.assertGreater(self.engine.loop_len[0], 0.0)

    def test_a_save_is_visible_without_grepping_raw_osc(self) -> None:
        self.engine.send_message("/sl/0/save_loop", ["/clips/b.wav", "", ""])
        self.assertEqual(self.engine.saved.get(0), "/clips/b.wav")

    def test_loading_does_not_disturb_another_loop(self) -> None:
        self.engine.state[3] = SL_STATE_PLAYING
        self.engine.send_message("/sl/0/load_loop", ["/clips/a.wav", "", ""])
        self.assertEqual(self.engine.state[3], SL_STATE_PLAYING)
        self.assertNotIn(3, self.engine.loaded)


class PlayheadTests(unittest.TestCase):
    """A wrap is the bench's only quantize boundary. The fake had no playhead
    at all, which is why a slot switch firing at press time passed the suite.
    """

    class _Gesture:
        def __init__(self, loop: int) -> None:
            self.loop = loop
            self.states: list[int] = []
            self.positions: list[float] = []

        def sync_from_sl(self, state: int) -> None:
            self.states.append(state)

        def sync_loop_len(self, length: float) -> None:
            self.length = length

        def sync_loop_pos(self, pos: float) -> None:
            self.positions.append(pos)

    def test_a_playing_loop_wraps_at_the_boundary(self) -> None:
        engine = FakeSlEngine()
        engine.state[0] = SL_STATE_PLAYING
        engine.loop_len[0] = 2.0
        engine.boundary()
        gesture = self._Gesture(0)
        engine.poll(gesture)
        self.assertEqual(len(gesture.positions), 2, "near the end, then zero")
        self.assertGreater(gesture.positions[0], gesture.positions[1])

    def test_a_stopped_loop_does_not_wrap(self) -> None:
        engine = FakeSlEngine()
        engine.state[0] = SL_STATE_OFF
        engine.loop_len[0] = 2.0
        engine.boundary()
        gesture = self._Gesture(0)
        engine.poll(gesture)
        self.assertEqual(gesture.positions, [])

    def test_a_take_closing_into_its_ring_out_has_a_full_pass_to_go(self) -> None:
        """Only loops ALREADY running before a boundary have completed a pass.

        Marking a loop wrapped in the same breath that started its ring-out
        overdub would end the overdub instantly — one poll of tail instead of
        one pass.
        """
        engine = FakeSlEngine()
        engine.send_message("/sl/0/hit", "record")
        engine.boundary()          # WAIT_START -> RECORDING
        engine.send_message("/sl/0/hit", "overdub")
        engine.boundary()          # take closes, overdub begins HERE
        gesture = self._Gesture(0)
        engine.poll(gesture)
        self.assertEqual(gesture.positions, [], "the tail pass has not run yet")
        engine.boundary()          # now a full pass has elapsed
        engine.poll(gesture)
        self.assertEqual(len(gesture.positions), 2)


if __name__ == "__main__":
    unittest.main()
