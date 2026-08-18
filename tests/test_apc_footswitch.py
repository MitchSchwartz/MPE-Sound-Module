"""APC footswitch — no master-loop special cases."""

import conftest  # noqa: F401 — bare sooperlooper imports (apc_grid, …)

import unittest
from unittest.mock import MagicMock, patch

import scripts.sooperlooper.apc_footswitch as footswitch_mod
from scripts.sooperlooper.apc_footswitch import LoopFootswitch, build_footswitches
from scripts.sooperlooper.sl_loop_states import (
    SL_STATE_MUTE,
    SL_STATE_OFF,
    SL_STATE_PAUSED,
    SL_STATE_PLAYING,
    SL_STATE_RECORDING,
    SL_STATE_WAIT_START,
    SL_STATE_WAIT_STOP,
)


class ApcFootswitchTests(unittest.TestCase):
    def test_loop0_tap_record_does_not_send_trigger(self) -> None:
        osc = MagicMock()
        fs = LoopFootswitch(loop=0, hold_ms=1000.0, debounce_ms=0.0)
        fs.bind(osc, MagicMock(), 36)
        fs.on_pad_down()
        fs.on_pad_up()
        paths = [c.args[0] for c in osc.send_message.call_args_list]
        self.assertIn("/sl/0/hit", paths)
        self.assertEqual(paths.count("/sl/0/hit"), 1)
        self.assertNotIn("trigger", [c.args[1] for c in osc.send_message.call_args_list])

    def test_record_starts_on_pad_down_not_release(self) -> None:
        """First-beat capture: arm on touch, not on lift."""
        osc = MagicMock()
        fs = LoopFootswitch(loop=0, hold_ms=1000.0, debounce_ms=0.0)
        fs.bind(osc, MagicMock(), 36)
        fs.on_pad_down()
        hits = [c.args[1] for c in osc.send_message.call_args_list if c.args[0] == "/sl/0/hit"]
        self.assertEqual(hits, ["record"])
        osc.reset_mock()
        fs.on_pad_up()
        self.assertEqual(osc.send_message.call_args_list, [])

    def test_sync_from_sl_loop0_playing(self) -> None:
        osc = MagicMock()
        fs = LoopFootswitch(loop=0, hold_ms=1000.0, debounce_ms=0.0)
        fs.bind(osc, MagicMock(), 36)
        changed = fs.sync_from_sl(SL_STATE_PLAYING)
        self.assertTrue(changed)
        self.assertEqual(fs.state, "playing")

    def test_sync_from_sl_quantize_wait_stays_red(self) -> None:
        osc = MagicMock()
        fs = LoopFootswitch(loop=2, hold_ms=1000.0, debounce_ms=0.0)
        fs.bind(osc, MagicMock(), 38)
        fs.sync_from_sl(SL_STATE_WAIT_STOP)
        self.assertEqual(fs.state, "recording")
        self.assertTrue(fs.awaiting_quantize)


    def test_quantize_wait_times_out_instead_of_latching(self) -> None:
        """No cycle boundary => release the pad, never latch it forever."""
        osc = MagicMock()
        fs = LoopFootswitch(loop=0, hold_ms=1000.0, debounce_ms=0.0)
        fs.bind(osc, MagicMock(), 36)
        fs.on_pad_down()
        fs.on_pad_up()
        fs.sync_from_sl(SL_STATE_RECORDING)
        fs.on_pad_down()
        fs.on_pad_up()  # stop-record -> waits for a boundary
        fs.sync_from_sl(SL_STATE_WAIT_STOP)
        self.assertTrue(fs.awaiting_quantize)
        self.assertTrue(fs._waiting_for_quantize())

        fs._wait_since -= footswitch_mod.QUANTIZE_WAIT_TIMEOUT_S + 1.0
        self.assertFalse(fs._waiting_for_quantize())
        self.assertFalse(fs.awaiting_quantize)

        fs.on_pad_down()
        fs.on_pad_up()
        hits = [c.args[1] for c in osc.send_message.call_args_list if c.args[0] == "/sl/0/hit"]
        self.assertEqual(len(hits), 3)

    def test_sync_from_sl_paused_yellow(self) -> None:
        osc = MagicMock()
        fs = LoopFootswitch(loop=1, hold_ms=1000.0, debounce_ms=0.0)
        fs.bind(osc, MagicMock(), 37)
        fs.sync_from_sl(SL_STATE_PAUSED)
        self.assertEqual(fs.state, "stopped")

    def test_build_includes_loop0(self) -> None:
        osc = MagicMock()
        _, footswitches = build_footswitches(
            osc=osc,
            midi_out=MagicMock(),
            num_loops=16,
            hold_ms=1000.0,
            debounce_ms=200.0,
        )
        loops = {fs.loop for fs in footswitches}
        self.assertIn(0, loops)



class GridEstablishmentTests(unittest.TestCase):
    """First take defines the tempo, then the grid stands alone."""

    def _fs(self, loop, grid, established_cb=None):
        from scripts.sooperlooper.apc_footswitch import LoopFootswitch

        fs = LoopFootswitch(
            loop=loop, hold_ms=1000.0, debounce_ms=0.0,
            quantized=True, grid=grid, on_grid_established=established_cb,
        )
        fs.bind(MagicMock(), MagicMock(), 36 + loop)
        return fs

    def test_first_take_records_instantly_and_sets_tempo(self) -> None:
        from scripts.sooperlooper.sl_grid_state import GridState

        seen = []
        grid = GridState()
        fs = self._fs(0, grid, lambda bpm, bars: seen.append((bpm, bars)))

        fs.on_pad_down(); fs.on_pad_up()          # start
        self.assertTrue(grid.is_pending(0))
        fs.on_pad_down(); fs.on_pad_up()          # stop — must NOT wait
        self.assertFalse(fs.awaiting_quantize)

        fs.sync_loop_len(2.0)
        fs.sync_loop_pos(0.0)
        fs.sync_from_sl(SL_STATE_PLAYING)
        self.assertTrue(grid.established)
        self.assertEqual(seen, [(120.0, 1)])

    def test_grid_anchor_defers_until_loop_wrap(self) -> None:
        """Late PLAYING report must not reset phase mid-bar."""
        from scripts.sooperlooper.sl_grid_state import GridState

        seen = []
        grid = GridState()
        fs = self._fs(0, grid, lambda bpm, bars: seen.append((bpm, bars)))

        fs.on_pad_down(); fs.on_pad_up()
        fs.on_pad_up()
        fs.sync_loop_len(2.0)
        fs.sync_loop_pos(0.08)  # late OSC — mid-bar
        fs.sync_from_sl(SL_STATE_PLAYING)
        self.assertFalse(grid.established)
        self.assertEqual(seen, [])

        fs.sync_loop_pos(1.85)
        fs.sync_loop_pos(0.01)  # wrap
        self.assertTrue(grid.established)
        self.assertEqual(seen, [(120.0, 1)])

    def test_hold_clear_drops_grid_when_engine_reports_last_clip_off(self) -> None:
        """No clips, no grid — driven by SL state, not bench hold-clear alone."""
        from scripts.sooperlooper.sl_grid_state import GridState

        grid = GridState()
        fs = self._fs(0, grid)
        fs.on_pad_down(); fs.on_pad_up()
        fs.on_pad_up()
        fs.sync_loop_len(2.0)
        fs.sync_loop_pos(0.0)
        fs.sync_from_sl(SL_STATE_PLAYING)
        self.assertTrue(grid.established)

        fs._clear_loop()
        self.assertTrue(
            grid.established,
            "hold-clear alone must not drop grid before SL confirms OFF",
        )

        fs.sync_from_sl(SL_STATE_OFF)
        self.assertFalse(grid.established)
        self.assertIsNone(grid.bpm)

    def test_deleting_defining_clip_keeps_grid_while_other_clips_remain(self) -> None:
        from scripts.sooperlooper.sl_grid_state import GridState

        grid = GridState()
        fs = self._fs(0, grid)
        fs.on_pad_down(); fs.on_pad_up()
        fs.on_pad_up()
        fs.sync_loop_len(2.0)
        fs.sync_loop_pos(0.0)
        fs.sync_from_sl(SL_STATE_PLAYING)
        grid.note_loop_content(1, True)

        fs.sync_from_sl(SL_STATE_OFF)
        self.assertTrue(grid.established, "grid stays while any clip remains")
        self.assertAlmostEqual(grid.bpm, 120.0)

    def test_second_clip_does_wait_for_the_boundary(self) -> None:
        from scripts.sooperlooper.sl_grid_state import GridState

        grid = GridState()
        grid.arm(0)
        grid.establish(0, 2.0)

        fs = self._fs(1, grid)
        fs.on_pad_down(); fs.on_pad_up()
        # The engine has to confirm recording before the stop can be sent as a
        # stop — tapping again before that arrives means the engine may still
        # be armed, where `record` lands as CANCEL.
        fs.sync_from_sl(SL_STATE_RECORDING)
        fs.on_pad_down(); fs.on_pad_up()
        self.assertTrue(fs.awaiting_quantize, "quantized clip must wait for the bar")


class DoubleTapRecordsOneCycleTests(unittest.TestCase):
    """Double-tap while armed must record exactly one cycle, not cancel."""

    def _fs(self, grid):
        from scripts.sooperlooper.apc_footswitch import LoopFootswitch

        fs = LoopFootswitch(loop=1, hold_ms=1000.0, debounce_ms=0.0,
                            quantized=True, grid=grid)
        fs.bind(MagicMock(), MagicMock(), 37)
        return fs

    def _grid(self):
        from scripts.sooperlooper.sl_grid_state import GridState

        g = GridState()
        g.arm(0)
        g.establish(0, 2.0)
        g.note_loop_content(0, True)
        return g

    def test_second_tap_while_armed_does_not_reach_sl_as_cancel(self) -> None:
        fs = self._fs(self._grid())
        fs.on_pad_down(); fs.on_pad_up()            # arm
        fs.sync_from_sl(SL_STATE_WAIT_START)
        fs.on_pad_down(); fs.on_pad_up()            # double tap
        hits = [c.args[1] for c in fs._osc.send_message.call_args_list
                if c.args[0] == "/sl/1/hit"]
        self.assertEqual(hits, ["record"], "a 2nd record while armed is CANCEL in SL")
        self.assertTrue(fs._stop_queued)

    def test_queued_stop_fires_when_recording_actually_begins(self) -> None:
        fs = self._fs(self._grid())
        fs.on_pad_down(); fs.on_pad_up()
        fs.sync_from_sl(SL_STATE_WAIT_START)
        fs.on_pad_down(); fs.on_pad_up()
        fs.sync_from_sl(SL_STATE_RECORDING)        # boundary reached
        hits = [c.args[1] for c in fs._osc.send_message.call_args_list
                if c.args[0] == "/sl/1/hit"]
        self.assertEqual(hits, ["record", "record"])
        self.assertFalse(fs._stop_queued)
        self.assertTrue(fs.awaiting_quantize)


class TransitionBlinkTests(unittest.TestCase):
    """Recording -> playing alternates red/green; other states stay standard."""

    def _fs(self):
        from scripts.sooperlooper.apc_footswitch import LoopFootswitch

        fs = LoopFootswitch(loop=0, hold_ms=1000.0, debounce_ms=0.0)
        fs.bind(MagicMock(), MagicMock(), 36)
        return fs

    def _sent(self, fs):
        return [c.args[0][2] for c in fs._midi_out.send_message.call_args_list]

    def test_recording_queued_to_play_alternates_red_and_green(self) -> None:
        fs = self._fs()
        fs.sync_from_sl(SL_STATE_WAIT_STOP)
        seq = []
        with patch("scripts.sooperlooper.apc_footswitch.time.monotonic") as clock:
            for i in range(4):
                clock.return_value = i * footswitch_mod.TRANSITION_BLINK_S
                fs.poll_led()
                seq.append(self._sent(fs)[-1])
        # gaps demarcate the colours; without them it reads as one flicker
        self.assertEqual(seq, [footswitch_mod.LED_OFF, footswitch_mod.LED_RED, footswitch_mod.LED_OFF, footswitch_mod.LED_GREEN])

    def test_queued_to_record_stays_ableton_standard_red_blink(self) -> None:
        fs = self._fs()
        fs.sync_from_sl(SL_STATE_WAIT_START)
        self.assertEqual(self._sent(fs)[-1], footswitch_mod.LED_RED_BLINK)
        self.assertIsNone(fs._led_transition, "no animation for an unambiguous state")

    def test_landing_on_playing_ends_the_animation(self) -> None:
        fs = self._fs()
        fs.sync_from_sl(SL_STATE_WAIT_STOP)
        fs.sync_from_sl(SL_STATE_PLAYING)
        self.assertIsNone(fs._led_transition)
        self.assertEqual(self._sent(fs)[-1], footswitch_mod.LED_GREEN)


class QuantizedLaunchTests(unittest.TestCase):
    """Launching a stopped clip lands on the bar, not immediately."""

    def _fs(self):
        from scripts.sooperlooper.apc_footswitch import LoopFootswitch

        fs = LoopFootswitch(loop=2, hold_ms=1000.0, debounce_ms=0.0, quantized=True)
        fs.bind(MagicMock(), MagicMock(), 38)
        return fs

    def _hits(self, fs):
        return [c.args[1] for c in fs._osc.send_message.call_args_list
                if c.args[0] == "/sl/2/hit"]

    def test_stop_mutes_rather_than_pauses(self) -> None:
        """A muted loop keeps running, so relaunch is back in phase."""
        fs = self._fs()
        fs.sync_from_sl(SL_STATE_PLAYING)
        fs.on_pad_down(); fs.on_pad_up()
        self.assertEqual(self._hits(fs), ["mute_on"])

    def test_launch_is_a_quantized_trigger_from_the_clip_start(self) -> None:
        """trigger plays from the start, is deferred to the boundary by SL,
        and lifts a mute (verified on the engine) — so it is the whole launch."""
        fs = self._fs()
        fs.sync_from_sl(SL_STATE_MUTE)
        fs.on_pad_down(); fs.on_pad_up()
        hits = self._hits(fs)
        self.assertIn("trigger", hits)
        self.assertNotIn("mute_off", hits)
        # A queued launch is just an unconfirmed expectation of Playing.
        self.assertEqual(fs.state, "playing")
        self.assertEqual(fs.sl_state, SL_STATE_MUTE)

    def test_queued_launch_blinks_plain_green(self) -> None:
        fs = self._fs()
        fs.sync_from_sl(SL_STATE_MUTE)
        fs.on_pad_down(); fs.on_pad_up()
        # a queued launch is a plain green blink — no second colour needed
        self.assertIsNone(fs._led_transition)
        self.assertEqual(
            [c.args[0][2] for c in fs._midi_out.send_message.call_args_list][-1],
            footswitch_mod.LED_GREEN_BLINK,
        )
        fs.sync_from_sl(SL_STATE_PLAYING)
        self.assertEqual(
            [c.args[0][2] for c in fs._midi_out.send_message.call_args_list][-1],
            footswitch_mod.LED_GREEN,
            "landed — solid green, and only now",
        )


class StopAllIsImmediateTests(unittest.TestCase):
    """Stop All is a transport action; per-clip stop stays musical."""

    def test_stop_all_lifts_quantize_then_restores_it(self) -> None:
        from scripts.sooperlooper.apc_footswitch import build_footswitches, stop_all_loops

        osc, midi = MagicMock(), MagicMock()
        _, footswitches = build_footswitches(
            osc=osc, midi_out=midi, num_loops=2, hold_ms=1000.0, debounce_ms=0.0
        )
        stop_all_loops(osc, num_loops=2, footswitches=footswitches)

        sent = [(c.args[0], c.args[1]) for c in osc.send_message.call_args_list]
        quant = [v for path, v in sent if path == "/sl/-1/set"]
        self.assertEqual(quant, [["mute_quantized", 0.0], ["mute_quantized", 1.0]],
                         "quantize must be lifted for the stop, then restored")
        hits = [v for path, v in sent if path == "/sl/-1/hit"]
        self.assertEqual(hits, ["mute_on", "pause_on"])

    def test_per_clip_stop_is_still_quantized(self) -> None:
        """Only Stop All is immediate — a single pad stop still waits."""
        from scripts.sooperlooper.apc_footswitch import LoopFootswitch

        fs = LoopFootswitch(loop=1, hold_ms=1000.0, debounce_ms=0.0, quantized=True)
        fs.bind(MagicMock(), MagicMock(), 37)
        fs.sync_from_sl(SL_STATE_PLAYING)
        fs.on_pad_down(); fs.on_pad_up()
        paths = [c.args[0] for c in fs._osc.send_message.call_args_list]
        self.assertNotIn("/sl/1/set", paths, "must not touch mute_quantized")

if __name__ == "__main__":
    unittest.main()
