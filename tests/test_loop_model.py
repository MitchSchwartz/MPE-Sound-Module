"""The gesture vocabulary and the pad colours, as pure functions."""

from __future__ import annotations

from tests import conftest  # noqa: F401 — bare sooperlooper imports (sl_loop_states, …)

import unittest

from scripts.sooperlooper.led_table import (
    LED_GREEN,
    LED_GREEN_BLINK,
    LED_OFF,
    LED_RED,
    LED_RED_BLINK,
    LED_YELLOW,
    LED_YELLOW_BLINK,
    RECORD_TO_PLAY,
    led_for,
)
from scripts.sooperlooper.loop_model import (
    STATE_IDLE,
    STATE_PLAYING,
    STATE_RECORDING,
    STATE_STOPPED,
    derive_state,
    effective_state,
    pending_resolved,
    plan_gesture,
    plan_tap,
)
from scripts.sooperlooper.sl_loop_states import (
    SL_STATE_MUTE,
    SL_STATE_OFF,
    SL_STATE_OFF_MUTED,
    SL_STATE_PAUSED,
    SL_STATE_PLAYING,
    SL_STATE_RECORDING,
    SL_STATE_WAIT_START,
    SL_STATE_WAIT_STOP,
)


class DeriveStateTests(unittest.TestCase):
    def test_every_engine_state_has_a_bench_meaning(self) -> None:
        from sl_loop_states import SL_STATE_OVERDUBBING

        self.assertEqual(derive_state(SL_STATE_OFF), STATE_IDLE)
        self.assertEqual(derive_state(SL_STATE_RECORDING), STATE_RECORDING)
        self.assertEqual(derive_state(SL_STATE_WAIT_START), STATE_RECORDING)
        self.assertEqual(derive_state(SL_STATE_WAIT_STOP), STATE_RECORDING)
        self.assertEqual(derive_state(SL_STATE_PLAYING), STATE_PLAYING)
        self.assertEqual(derive_state(SL_STATE_OVERDUBBING), STATE_PLAYING)
        self.assertEqual(derive_state(SL_STATE_PAUSED), STATE_STOPPED)
        self.assertEqual(derive_state(SL_STATE_MUTE), STATE_STOPPED)
        self.assertEqual(derive_state(SL_STATE_OFF_MUTED), STATE_IDLE)

    def test_an_unknown_code_does_not_take_the_surface_down(self) -> None:
        self.assertEqual(derive_state(99), STATE_IDLE)

    def test_pending_wins_until_the_engine_catches_up(self) -> None:
        self.assertEqual(effective_state(SL_STATE_OFF, STATE_RECORDING), STATE_RECORDING)
        self.assertEqual(effective_state(SL_STATE_OFF, None), STATE_IDLE)

    def test_an_intent_the_engine_has_reached_is_resolved(self) -> None:
        self.assertTrue(pending_resolved(SL_STATE_RECORDING, STATE_RECORDING))
        self.assertFalse(pending_resolved(SL_STATE_OFF, STATE_RECORDING))
        self.assertTrue(pending_resolved(SL_STATE_OFF, None))

    def test_the_engine_going_elsewhere_also_resolves_it(self) -> None:
        """Not an error to suppress — the engine is the authority."""
        self.assertTrue(pending_resolved(SL_STATE_PLAYING, STATE_PLAYING))


class PlanTapTests(unittest.TestCase):
    def _plan(self, sl_state, **kw):
        kw.setdefault("pending", None)
        kw.setdefault("grid_established", True)
        kw.setdefault("is_defining", False)
        kw.setdefault("quantized", True)
        return plan_tap(sl_state=sl_state, **kw)

    def _gesture(self, edge, sl_state, **kw):
        kw.setdefault("pending", None)
        kw.setdefault("grid_established", True)
        kw.setdefault("is_defining", False)
        kw.setdefault("quantized", True)
        return plan_gesture(edge=edge, sl_state=sl_state, **kw)

    def test_idle_records_on_pad_down_not_up(self) -> None:
        down = self._gesture("down", SL_STATE_OFF)
        up = self._gesture("up", SL_STATE_OFF)
        self.assertEqual(down.commands, ("record",))
        self.assertEqual(up.commands, ())

    def test_idle_records(self) -> None:
        p = self._plan(SL_STATE_OFF)
        self.assertEqual(p.commands, ("record",))
        self.assertEqual(p.expect, STATE_RECORDING)
        self.assertFalse(p.arm_grid)

    def test_the_first_take_arms_the_grid_and_does_not_count_in(self) -> None:
        p = self._plan(SL_STATE_OFF, grid_established=False)
        self.assertTrue(p.arm_grid)
        self.assertEqual(p.commands, ("record",))

    def test_a_second_tap_before_the_engine_answers_queues_the_stop(self) -> None:
        """`record` into an armed loop is a CANCEL — never send it blind."""
        p = self._gesture("down", SL_STATE_OFF, pending=STATE_RECORDING)
        self.assertEqual(p.commands, ())
        self.assertTrue(p.queue_stop)
        self.assertEqual(p.expect, STATE_RECORDING, "still recording, as far as the player knows")

    def test_a_second_tap_while_armed_queues_the_stop(self) -> None:
        p = self._gesture("down", SL_STATE_WAIT_START, pending=STATE_RECORDING)
        self.assertTrue(p.queue_stop)
        self.assertEqual(p.commands, ())

    def test_confirmed_recording_stops_and_waits_for_the_bar(self) -> None:
        p = self._gesture("down", SL_STATE_RECORDING)
        self.assertEqual(p.commands, ("record",))
        self.assertTrue(p.begin_quantize_wait)
        self.assertIsNone(p.expect, "the boundary decides, not us")

    def test_the_defining_take_does_not_wait_for_a_bar_that_does_not_exist(self) -> None:
        p = self._gesture("down", SL_STATE_RECORDING, is_defining=True)
        self.assertFalse(p.begin_quantize_wait)
        self.assertEqual(p.expect, STATE_PLAYING)

    def test_defining_take_stop_uses_stop_then_weld_when_enabled(self) -> None:
        p = self._gesture(
            "down",
            SL_STATE_RECORDING,
            is_defining=True,
            tail_capture_enabled=True,
        )
        self.assertTrue(p.begin_tail_capture)
        self.assertEqual(p.commands, ("record",))
        self.assertEqual(p.expect, STATE_PLAYING)
        self.assertFalse(p.tail_deferred)

    def test_grid_clip_stop_arms_deferred_tail_weld(self) -> None:
        p = self._gesture(
            "down",
            SL_STATE_RECORDING,
            grid_established=True,
            is_defining=False,
            tail_capture_enabled=True,
        )
        self.assertTrue(p.begin_tail_capture)
        self.assertTrue(p.begin_quantize_wait)
        self.assertTrue(p.tail_deferred)
        self.assertEqual(p.commands, ("record",))

    def test_defining_take_stop_off_muted_enters_tail_not_queue_stop(self) -> None:
        """Pi reported sl=20 (OffMuted) while pending=recording — was queue_stop deadlock."""
        p = self._gesture(
            "down",
            SL_STATE_OFF_MUTED,
            pending=STATE_RECORDING,
            is_defining=True,
            tail_capture_enabled=True,
        )
        self.assertTrue(p.begin_tail_capture)
        self.assertFalse(p.queue_stop)

    def test_free_form_loops_never_arm_a_quantize_wait(self) -> None:
        p = self._gesture("down", SL_STATE_RECORDING, quantized=False)
        self.assertFalse(p.begin_quantize_wait)

    def test_tapping_after_a_timed_out_stop_keeps_recording(self) -> None:
        p = self._gesture("down", SL_STATE_WAIT_STOP)
        self.assertEqual(p.commands, ("record",))
        self.assertFalse(p.queue_stop)

    def test_playing_mutes_on_pad_up(self) -> None:
        down = self._gesture("down", SL_STATE_PLAYING)
        up = self._gesture("up", SL_STATE_PLAYING)
        self.assertEqual(down.commands, ())
        self.assertEqual(up.commands, ("mute_on",))
        self.assertEqual(up.expect, STATE_STOPPED)

    def test_re_tap_while_pending_mute_cancels_with_mute_off(self) -> None:
        p = self._gesture("up", SL_STATE_PLAYING, pending=STATE_STOPPED)
        self.assertEqual(p.commands, ("mute_off",))
        self.assertTrue(p.cancel_pending)
        self.assertIsNone(p.expect)

    def test_pending_mute_cancel_does_not_fire_on_pad_down(self) -> None:
        p = self._gesture("down", SL_STATE_PLAYING, pending=STATE_STOPPED)
        self.assertEqual(p.commands, ())

    def test_playing_mutes_rather_than_pauses(self) -> None:
        p = self._plan(SL_STATE_PLAYING)
        self.assertEqual(p.commands, ("mute_on",))
        self.assertEqual(p.expect, STATE_STOPPED)

    def test_stopped_launches_with_a_quantized_trigger(self) -> None:
        for st in (SL_STATE_MUTE, SL_STATE_PAUSED):
            p = self._plan(st)
            self.assertEqual(p.commands, ("pause_off", "trigger"))
            self.assertEqual(p.expect, STATE_PLAYING)
            self.assertNotIn("mute_off", p.commands)


class LedTableTests(unittest.TestCase):
    def test_solid_colours_come_only_from_the_engine(self) -> None:
        self.assertEqual(led_for(SL_STATE_PLAYING), (LED_GREEN,))
        self.assertEqual(led_for(SL_STATE_RECORDING), (LED_RED,))
        self.assertEqual(led_for(SL_STATE_MUTE), (LED_YELLOW,))
        self.assertEqual(led_for(SL_STATE_OFF), (LED_OFF,))

    def test_an_unconfirmed_intent_always_blinks(self) -> None:
        """The whole contract: solid means it happened, blink means it is coming."""
        self.assertEqual(led_for(SL_STATE_OFF, pending=STATE_RECORDING), (LED_RED_BLINK,))
        self.assertEqual(led_for(SL_STATE_PLAYING, pending=STATE_STOPPED), (LED_YELLOW_BLINK,))
        self.assertEqual(led_for(SL_STATE_MUTE, pending=STATE_PLAYING), (LED_GREEN_BLINK,))

    def test_a_confirmed_intent_goes_solid(self) -> None:
        self.assertEqual(led_for(SL_STATE_PLAYING, pending=STATE_PLAYING), (LED_GREEN,))

    def test_queued_to_record_is_ableton_standard(self) -> None:
        self.assertEqual(led_for(SL_STATE_WAIT_START), (LED_RED_BLINK,))

    def test_tail_capture_led_is_amber_while_weld_pending(self) -> None:
        from scripts.sooperlooper.led_table import RECORD_TO_PLAY, led_for

        self.assertEqual(
            led_for(SL_STATE_PLAYING, tail_capture=True),
            RECORD_TO_PLAY,
        )

    def test_recording_queued_to_play_shows_both_colours(self) -> None:
        """The state Ableton drops: recording is STILL RUNNING into this bar."""
        self.assertEqual(led_for(SL_STATE_WAIT_STOP), RECORD_TO_PLAY)
        self.assertIn(LED_RED, RECORD_TO_PLAY)
        self.assertIn(LED_GREEN, RECORD_TO_PLAY)

    def test_never_paints_solid_green_for_anything_but_playing(self) -> None:
        for st in (SL_STATE_OFF, SL_STATE_RECORDING, SL_STATE_WAIT_START,
                   SL_STATE_MUTE, SL_STATE_PAUSED):
            for pending in (None, STATE_PLAYING, STATE_RECORDING, STATE_STOPPED):
                self.assertNotIn(LED_GREEN, led_for(st, pending=pending),
                                 f"solid green over sl_state={st} pending={pending}")


if __name__ == "__main__":
    unittest.main()
