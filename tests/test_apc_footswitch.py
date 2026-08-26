"""APC footswitch — no master-loop special cases."""

import conftest  # noqa: F401 — bare sooperlooper imports (apc_grid, …)

import unittest
from unittest.mock import MagicMock, patch
import time

import scripts.sooperlooper.apc_footswitch as footswitch_mod
from scripts.sooperlooper.apc_footswitch import LoopFootswitch, build_footswitches
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
        with patch.object(footswitch_mod, "TAIL_CAPTURE_ENABLED", False):
            fs.on_pad_down()
            fs.on_pad_up()
            fs.sync_from_sl(SL_STATE_RECORDING)
            fs.on_pad_down()
            fs.on_pad_up()  # stop on pad down -> waits for a boundary
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

    def _fs(self, loop, grid, established_cb=None, reanchor_cb=None):
        from scripts.sooperlooper.apc_footswitch import LoopFootswitch

        fs = LoopFootswitch(
            loop=loop, hold_ms=1000.0, debounce_ms=0.0,
            quantized=True, grid=grid,
            on_grid_established=established_cb,
            on_phase_reanchor=reanchor_cb,
        )
        fs.bind(MagicMock(), MagicMock(), 36 + loop)
        return fs

    def _start_defining_take(self, fs) -> None:
        fs.on_pad_down()
        fs.on_pad_up()
        fs.sync_from_sl(SL_STATE_RECORDING)

    def _close_defining_take(self, fs) -> None:
        fs.on_pad_down()
        fs.on_pad_up()
        if fs._tail_capture:
            fs._finish_tail_capture("test")

    def _wire_seam_hooks(self, fs, *, merge_immediate: bool = True) -> None:
        from scripts.sooperlooper.sl_seam_weld import SCRATCH_LOOP

        def start(_loop: int) -> None:
            fs._osc.send_message(f"/sl/{SCRATCH_LOOP}/hit", ["record"])

        def merge(_loop: int, done, position=None) -> bool:
            if merge_immediate:
                done()
            return True

        fs.set_seam_weld_hooks(
            on_prepare_scratch=lambda _loop: fs._osc.send_message(
                f"/sl/{SCRATCH_LOOP}/hit", ["undo_all"]
            ),
            on_start_scratch=start,
            on_stop_scratch=lambda _loop: fs._osc.send_message(
                f"/sl/{SCRATCH_LOOP}/hit", ["record"]
            ),
            on_request_merge=merge,
        )

    def test_first_take_records_instantly_and_sets_tempo(self) -> None:
        from scripts.sooperlooper.sl_grid_state import GridState
        from scripts.sooperlooper.sl_grid_sync import TAIL_HOLD_S

        seen = []
        grid = GridState()
        fs = self._fs(0, grid, lambda bpm, bars: seen.append((bpm, bars)))
        self._wire_seam_hooks(fs)

        self._start_defining_take(fs)
        self.assertTrue(grid.is_pending(0))
        fs.on_pad_down()
        self.assertTrue(fs._tail_capture)
        self.assertTrue(fs._tail_stop_sent)
        self.assertFalse(fs.awaiting_quantize)

        hits = [
            c.args[1]
            for c in fs._osc.send_message.call_args_list
            if c.args[0] == "/sl/0/hit"
        ]
        self.assertEqual(hits, ["record", "record"], "start then immediate stop")

        fs.sync_from_sl(SL_STATE_PLAYING)
        fs.sync_loop_len(2.0)
        fs.sync_loop_pos(0.0)
        fs._maybe_start_scratch()
        fs.sync_in_peak(0.5)
        fs.sync_in_peak(0.0)
        fs._tail_silence_since = TAIL_HOLD_S * -2 + time.monotonic()
        fs.poll_tail_capture()

        self.assertTrue(grid.established)
        self.assertEqual(seen, [(120.0, 1)])

    def test_scratch_starts_when_playback_lands(self) -> None:
        with patch.object(footswitch_mod, "SEAM_WELD_ENABLED", True):
            fs = LoopFootswitch(loop=0, hold_ms=1000.0, debounce_ms=0.0)
            fs.bind(MagicMock(), MagicMock(), 36)
            started = []
            prepared = []
            fs.set_seam_weld_hooks(
                on_prepare_scratch=lambda loop: prepared.append(loop),
                on_start_scratch=lambda loop: started.append(loop),
                on_stop_scratch=lambda loop: None,
                on_request_merge=lambda loop, done, position=None: (done(), True)[1],
            )
            fs._tail_capture = True
            fs._tail_stop_sent = True
            fs.sync_from_sl(SL_STATE_PLAYING)
            fs.sync_loop_len(2.0)
            fs._maybe_start_scratch()
            self.assertTrue(fs._scratch_active)
            self.assertEqual(prepared, [0])
            self.assertEqual(started, [0])

    def test_prepare_scratch_deferred_until_playback_ready(self) -> None:
        from scripts.sooperlooper.sl_seam_weld import SCRATCH_LOOP

        fs = LoopFootswitch(loop=0, hold_ms=1000.0, debounce_ms=0.0)
        fs.bind(MagicMock(), MagicMock(), 36)
        prepared = []
        fs.set_seam_weld_hooks(
            on_prepare_scratch=lambda _loop: (
                fs._osc.send_message(f"/sl/{SCRATCH_LOOP}/hit", ["undo_all"]),
                prepared.append(_loop),
            ),
            on_start_scratch=lambda _loop: None,
            on_stop_scratch=lambda _loop: None,
            on_request_merge=lambda _loop, done, position=None: True,
        )
        self._start_defining_take(fs)
        fs.on_pad_down()
        scratch_hits = [
            c.args
            for c in fs._osc.send_message.call_args_list
            if c.args[0] == f"/sl/{SCRATCH_LOOP}/hit"
        ]
        self.assertEqual(scratch_hits, [], "no scratch OSC at stop — wait for PLAYING")
        self.assertEqual(prepared, [])
        fs.sync_from_sl(SL_STATE_PLAYING)
        fs.sync_loop_len(2.0)
        fs._maybe_start_scratch()
        self.assertEqual(prepared, [0])
        scratch_hits = [
            c.args
            for c in fs._osc.send_message.call_args_list
            if c.args[0] == f"/sl/{SCRATCH_LOOP}/hit"
        ]
        self.assertEqual(scratch_hits, [(f"/sl/{SCRATCH_LOOP}/hit", ["undo_all"])])

    def test_grid_anchor_defers_until_loop_wrap(self) -> None:
        """Late PLAYING report: grid now, phase re-anchor at wrap."""
        from scripts.sooperlooper.sl_grid_state import GridState

        seen = []
        reanchored = []
        grid = GridState()
        fs = self._fs(
            0, grid,
            lambda bpm, bars: seen.append((bpm, bars)),
            lambda bpm: reanchored.append(bpm),
        )

        self._start_defining_take(fs)
        self._close_defining_take(fs)
        fs.sync_loop_len(2.0)
        fs.sync_loop_pos(0.08)  # late OSC — mid-bar
        fs.sync_from_sl(SL_STATE_PLAYING)
        self.assertTrue(grid.established, "grid must exist as soon as the take saves")
        self.assertEqual(seen, [(120.0, 1)])
        self.assertEqual(reanchored, [])

        fs.sync_loop_pos(1.85)
        fs.sync_loop_pos(0.01)  # wrap
        self.assertEqual(reanchored, [120.0])

    def test_hold_clear_drops_grid_when_engine_reports_last_clip_off(self) -> None:
        """No clips, no grid — driven by SL state, not bench hold-clear alone."""
        from scripts.sooperlooper.sl_grid_state import GridState

        grid = GridState()
        fs = self._fs(0, grid)
        self._start_defining_take(fs)
        self._close_defining_take(fs)
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
        self._start_defining_take(fs)
        self._close_defining_take(fs)
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
        fs.on_pad_down()
        fs.sync_from_sl(SL_STATE_WAIT_STOP)
        self.assertTrue(fs.awaiting_quantize, "quantized clip must wait for the bar")
        self.assertTrue(fs._tail_capture)
        self.assertTrue(fs._tail_deferred)


class TailCaptureTests(unittest.TestCase):
    def test_pad_down_during_tail_aborts_without_merge(self) -> None:
        fs = LoopFootswitch(loop=0, hold_ms=1000.0, debounce_ms=200.0)
        fs.bind(MagicMock(), MagicMock(), 36)
        merged = []
        fs.set_seam_weld_hooks(
            on_prepare_scratch=lambda loop: None,
            on_start_scratch=lambda loop: None,
            on_stop_scratch=lambda loop: None,
            on_request_merge=lambda loop, done, position=None: (merged.append(loop) or False),
        )
        fs._tail_capture = True
        fs._tail_stop_sent = True
        fs._scratch_active = True
        fs._tail_capture_since = time.monotonic()
        fs._last_action_at = time.monotonic()
        fs.on_pad_down()
        self.assertFalse(fs._tail_capture)
        self.assertEqual(merged, [])

    def test_tail_led_record_to_play_during_weld(self) -> None:
        from scripts.sooperlooper.led_table import RECORD_TO_PLAY, led_for

        fs = LoopFootswitch(loop=0, hold_ms=1000.0, debounce_ms=0.0)
        fs.bind(MagicMock(), MagicMock(), 36)
        fs.sync_from_sl(SL_STATE_PLAYING)
        fs._tail_capture = True
        self.assertEqual(fs._led_target(), RECORD_TO_PLAY)
        self.assertEqual(led_for(SL_STATE_PLAYING, tail_capture=True), RECORD_TO_PLAY)

    def test_tail_max_timeout_closes_capture(self) -> None:
        from scripts.sooperlooper.sl_grid_sync import TAIL_MAX_S

        fs = LoopFootswitch(loop=0, hold_ms=1000.0, debounce_ms=0.0)
        fs.bind(MagicMock(), MagicMock(), 36)
        fs.set_seam_weld_hooks(
            on_prepare_scratch=lambda loop: None,
            on_start_scratch=lambda loop: None,
            on_stop_scratch=lambda loop: None,
            on_request_merge=lambda loop, done, position=None: (done(), True)[1],
        )
        fs._tail_capture = True
        fs._tail_stop_sent = True
        fs.sync_from_sl(SL_STATE_PLAYING)
        fs.sync_loop_len(2.0)
        fs._scratch_active = True
        fs._tail_capture_since = time.monotonic() - TAIL_MAX_S - 0.01
        fs.poll_tail_capture()
        self.assertFalse(fs._tail_capture)

    def test_finish_tail_resets_hold_timer(self) -> None:
        fs = LoopFootswitch(loop=0, hold_ms=1000.0, debounce_ms=0.0)
        fs.bind(MagicMock(), MagicMock(), 36)
        fs._tail_capture = True
        fs._pad_down = True
        fs._pad_down_at = time.monotonic() - 5.0
        fs._finish_tail_capture("test")
        self.assertFalse(fs._pad_down)
        self.assertEqual(fs._pad_down_at, 0.0)

    def test_finish_tail_marks_action_for_debounce(self) -> None:
        fs = LoopFootswitch(loop=0, hold_ms=1000.0, debounce_ms=200.0)
        fs.bind(MagicMock(), MagicMock(), 36)
        fs._tail_capture = True
        fs._tail_stop_sent = True
        fs._last_action_at = 0.0
        fs._finish_tail_capture("test")
        fs.on_pad_down()
        fs.on_pad_up()
        hits = [c.args[1] for c in fs._osc.send_message.call_args_list
                if c.args[0] == "/sl/0/hit"]
        self.assertEqual(hits, [], "gesture after auto-close should be debounced")

    def test_defining_stop_sends_immediate_record_stop(self) -> None:
        from scripts.sooperlooper.sl_grid_state import GridState

        grid = GridState()
        fs = LoopFootswitch(loop=0, hold_ms=1000.0, debounce_ms=0.0, grid=grid)
        fs.bind(MagicMock(), MagicMock(), 36)
        fs.on_pad_down()
        fs.on_pad_up()
        fs.sync_from_sl(SL_STATE_RECORDING)
        fs.on_pad_down()
        hits = [
            c.args[1]
            for c in fs._osc.send_message.call_args_list
            if c.args[0] == "/sl/0/hit"
        ]
        self.assertEqual(hits, ["record", "record"], "stop on pad — length fixed now")
        self.assertTrue(fs._tail_stop_sent)
        self.assertTrue(fs._tail_capture)

    def test_tail_max_timeout_without_peak_reading(self) -> None:
        from scripts.sooperlooper.sl_grid_sync import TAIL_MAX_S

        fs = LoopFootswitch(loop=0, hold_ms=1000.0, debounce_ms=0.0)
        fs.bind(MagicMock(), MagicMock(), 36)
        fs.set_seam_weld_hooks(
            on_prepare_scratch=lambda loop: None,
            on_start_scratch=lambda loop: None,
            on_stop_scratch=lambda loop: None,
            on_request_merge=lambda loop, done, position=None: (done(), True)[1],
        )
        fs._tail_capture = True
        fs._tail_stop_sent = True
        fs.sync_from_sl(SL_STATE_PLAYING)
        fs.sync_loop_len(2.0)
        fs._scratch_active = True
        fs._tail_capture_since = time.monotonic() - TAIL_MAX_S - 0.01
        fs._in_peak_seen = False
        fs._tail_saw_loud = False
        fs.poll_tail_capture()
        self.assertFalse(fs._tail_capture)

    def test_tail_waits_for_loud_before_silence_close(self) -> None:
        from scripts.sooperlooper.sl_grid_sync import TAIL_HOLD_S

        fs = LoopFootswitch(loop=0, hold_ms=1000.0, debounce_ms=0.0)
        fs.bind(MagicMock(), MagicMock(), 36)
        fs.set_seam_weld_hooks(
            on_prepare_scratch=lambda loop: None,
            on_start_scratch=lambda loop: None,
            on_stop_scratch=lambda loop: None,
            on_request_merge=lambda loop, done, position=None: (done(), True)[1],
        )
        fs._tail_capture = True
        fs._tail_stop_sent = True
        fs.sync_from_sl(SL_STATE_PLAYING)
        fs.sync_loop_len(2.0)
        fs._scratch_active = True
        fs._tail_capture_since = time.monotonic()
        fs.sync_in_peak(0.0)
        fs._tail_silence_since = TAIL_HOLD_S * -2 + time.monotonic()
        fs.poll_tail_capture()
        self.assertTrue(fs._tail_capture)
        fs.sync_in_peak(0.5)
        fs.sync_in_peak(0.0)
        fs._tail_silence_since = TAIL_HOLD_S * -2 + time.monotonic()
        fs.poll_tail_capture()
        self.assertFalse(fs._tail_capture)

    def test_tail_waits_for_first_peak_reading(self) -> None:
        from scripts.sooperlooper.sl_grid_sync import TAIL_HOLD_S

        fs = LoopFootswitch(loop=0, hold_ms=1000.0, debounce_ms=0.0)
        fs.bind(MagicMock(), MagicMock(), 36)
        fs.set_seam_weld_hooks(
            on_prepare_scratch=lambda loop: None,
            on_start_scratch=lambda loop: None,
            on_stop_scratch=lambda loop: None,
            on_request_merge=lambda loop, done, position=None: (done(), True)[1],
        )
        fs._tail_capture = True
        fs._tail_stop_sent = True
        fs.sync_from_sl(SL_STATE_PLAYING)
        fs.sync_loop_len(2.0)
        fs._scratch_active = True
        fs._tail_capture_since = time.monotonic()
        fs.sync_in_peak(0.5)
        fs.sync_in_peak(0.0)
        fs._tail_silence_since = TAIL_HOLD_S * -2 + time.monotonic()
        fs.poll_tail_capture()
        self.assertFalse(fs._tail_capture)

    def test_merge_after_scratch_even_without_release_peak(self) -> None:
        from scripts.sooperlooper.sl_grid_sync import TAIL_MAX_S

        fs = LoopFootswitch(loop=0, hold_ms=1000.0, debounce_ms=0.0)
        fs.bind(MagicMock(), MagicMock(), 36)
        merged = []
        fs.set_seam_weld_hooks(
            on_prepare_scratch=lambda loop: None,
            on_start_scratch=lambda loop: None,
            on_stop_scratch=lambda loop: None,
            on_request_merge=lambda loop, done, position=None: (
                merged.append((loop, position)),
                done(),
                True,
            )[2],
        )
        fs._tail_capture = True
        fs._tail_stop_sent = True
        fs.sync_from_sl(SL_STATE_PLAYING)
        fs.sync_loop_len(2.0)
        fs._scratch_active = True
        fs._scratch_started = True
        fs._tail_saw_loud = False
        fs._tail_capture_since = time.monotonic() - TAIL_MAX_S - 0.01
        fs.poll_tail_capture()
        self.assertFalse(fs._tail_capture)
        self.assertEqual(len(merged), 1)

    def test_seam_position_is_live_not_a_stale_snapshot(self) -> None:
        """The merge takes hundreds of ms; a position sampled at queue time is
        most of a bar stale by the time the swap fires. The hook hands over a
        *callable* so the worker reads the playhead when it actually needs it."""
        fs = LoopFootswitch(loop=0, hold_ms=1000.0, debounce_ms=0.0)
        fs.bind(MagicMock(), MagicMock(), 36)
        handed = []
        fs.set_seam_weld_hooks(
            on_prepare_scratch=lambda loop: None,
            on_start_scratch=lambda loop: None,
            on_stop_scratch=lambda loop: None,
            on_request_merge=lambda loop, done, position=None: (
                handed.append(position),
                done(),
                True,
            )[2],
        )
        fs.sync_from_sl(SL_STATE_PLAYING)
        fs.sync_loop_len(2.0)
        fs._tail_capture = True
        fs._tail_stop_sent = True
        fs._scratch_started = True
        fs.sync_loop_pos(0.5)
        fs._end_tail_capture("test")
        self.assertEqual(len(handed), 1)
        self.assertTrue(callable(handed[0]), "must be a live feed, not a float")
        pos, length = handed[0]()
        self.assertEqual(length, 2.0)
        self.assertGreaterEqual(pos, 0.5)

    def test_seam_position_is_none_when_not_playing(self) -> None:
        """A stopped playhead would make the worker wait for a wrap forever."""
        fs = LoopFootswitch(loop=0, hold_ms=1000.0, debounce_ms=0.0)
        fs.bind(MagicMock(), MagicMock(), 36)
        fs.sync_loop_len(2.0)
        fs.sync_loop_pos(0.5)
        fs.sync_from_sl(SL_STATE_OFF)
        self.assertIsNone(fs.seam_position())

    def test_seam_position_wraps_within_the_loop(self) -> None:
        fs = LoopFootswitch(loop=0, hold_ms=1000.0, debounce_ms=0.0)
        fs.bind(MagicMock(), MagicMock(), 36)
        fs.sync_from_sl(SL_STATE_PLAYING)
        fs.sync_loop_len(2.0)
        fs.sync_loop_pos(1.999)
        fs._loop_pos_at = time.monotonic() - 0.5  # prediction runs past the end
        pos, length = fs.seam_position()
        self.assertLess(pos, length)
        self.assertGreaterEqual(pos, 0.0)

    def test_stop_scratch_does_not_clear_merge_flag(self) -> None:
        fs = LoopFootswitch(loop=0, hold_ms=1000.0, debounce_ms=0.0)
        fs.bind(MagicMock(), MagicMock(), 36)
        fs.set_seam_weld_hooks(
            on_prepare_scratch=lambda loop: None,
            on_start_scratch=lambda loop: None,
            on_stop_scratch=lambda loop: None,
            on_request_merge=lambda loop, done, position=None: True,
        )
        fs._tail_capture = True
        fs._tail_stop_sent = True
        fs._scratch_active = True
        fs._scratch_started = True
        fs._stop_scratch_capture()
        self.assertTrue(fs._scratch_started)
        self.assertTrue(fs._should_seam_merge())

    def test_grid_clock_deferred_until_tail_weld_finishes(self) -> None:
        from scripts.sooperlooper.sl_grid_state import GridState

        seen = []
        grid = GridState()
        fs = LoopFootswitch(
            loop=0,
            hold_ms=1000.0,
            debounce_ms=0.0,
            grid=grid,
            on_grid_established=lambda bpm, bars: seen.append((bpm, bars)),
        )
        fs.bind(MagicMock(), MagicMock(), 36)
        grid.arm(0)
        fs._tail_capture = True
        fs._tail_stop_sent = True
        fs.sync_loop_len(2.0)
        fs.sync_from_sl(SL_STATE_PLAYING)
        self.assertTrue(grid.established)
        self.assertEqual(seen, [])
        self.assertEqual(fs._deferred_grid_clock, (120.0, 1))
        fs._finish_tail_capture("test")
        self.assertEqual(seen, [(120.0, 1)])

    def test_stop_all_cancels_tail_and_calls_end_callback(self) -> None:
        from scripts.sooperlooper.apc_footswitch import stop_all_loops

        ended = []
        fs = LoopFootswitch(
            loop=0, hold_ms=1000.0, debounce_ms=0.0,
            on_tail_capture_end=lambda loop: ended.append(loop),
        )
        fs.bind(MagicMock(), MagicMock(), 36)
        fs._tail_capture = True
        stop_all_loops(MagicMock(), num_loops=1, footswitches=[fs])
        self.assertFalse(fs._tail_capture)
        self.assertEqual(ended, [0])


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

    def test_stop_all_skips_pending_on_off_muted_empty_loops(self) -> None:
        """Global mute leaves empties at sl=20; must not get pending=stopped."""
        from scripts.sooperlooper.apc_footswitch import stop_all_loops
        from scripts.sooperlooper.led_table import led_for

        osc = MagicMock()
        empty = LoopFootswitch(loop=1, hold_ms=1000.0, debounce_ms=0.0)
        empty.bind(MagicMock(), MagicMock(), 37)
        empty.sync_from_sl(SL_STATE_OFF_MUTED)
        stop_all_loops(osc, num_loops=2, footswitches=[empty])
        self.assertIsNone(empty._pending)
        self.assertEqual(led_for(SL_STATE_OFF_MUTED), (0,))

    def test_per_clip_stop_is_still_quantized(self) -> None:
        """Only Stop All is immediate — a single pad stop still waits."""
        from scripts.sooperlooper.apc_footswitch import LoopFootswitch

        fs = LoopFootswitch(loop=1, hold_ms=1000.0, debounce_ms=0.0, quantized=True)
        fs.bind(MagicMock(), MagicMock(), 37)
        fs.sync_from_sl(SL_STATE_PLAYING)
        fs.on_pad_down(); fs.on_pad_up()
        paths = [c.args[0] for c in fs._osc.send_message.call_args_list]
        self.assertNotIn("/sl/1/set", paths, "must not touch mute_quantized")


class PollFootswitchesTests(unittest.TestCase):
    def test_poll_footswitches_runs_tail_capture(self) -> None:
        from scripts.sooperlooper.apc_footswitch import poll_footswitches
        from scripts.sooperlooper.sl_grid_sync import TAIL_MAX_S

        fs = LoopFootswitch(loop=0, hold_ms=1000.0, debounce_ms=0.0)
        fs.bind(MagicMock(), MagicMock(), 36)
        fs._tail_capture = True
        fs._tail_capture_since = time.monotonic() - TAIL_MAX_S - 0.01
        fs.sync_from_sl(SL_STATE_RECORDING)
        poll_footswitches([fs])
        self.assertFalse(fs._tail_capture)


if __name__ == "__main__":
    unittest.main()
