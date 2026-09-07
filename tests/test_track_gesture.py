"""APC gesture — no master-loop special cases."""

from tests import conftest  # noqa: F401 — bare sooperlooper imports (apc_grid, …)

import unittest
from unittest.mock import MagicMock, patch
import time

import scripts.sooperlooper.track_gesture as gesture_mod
from scripts.sooperlooper.track_gesture import (
    TrackGesture,
    build_track_gestures,
    poll_track_gestures,
)
from scripts.sooperlooper.led_compositor import LedCompositor


class RecordingOut:
    def __init__(self) -> None:
        self.sent: list[list[int]] = []

    def send_message(self, msg) -> None:
        self.sent.append(list(msg))


def wire(fs) -> list[int]:
    """Velocities the DEVICE received. Not one writer's outgoing stream."""
    return [m[2] for m in fs._compositor._midi_out.sent]


def compositor() -> LedCompositor:
    """The one writer to the wire, over a recording fake.

    A gesture no longer holds a `midi_out`: it submits desired state and the
    compositor decides what the device is told. `sent` is therefore the
    device's whole history rather than one writer's outgoing stream — which is
    the distinction the four private diff caches made impossible to draw.
    """
    return LedCompositor(RecordingOut(), apc_label="mk1")
from scripts.sooperlooper.loop_model import STATE_PLAYING, STATE_STOPPED
from scripts.sooperlooper.sl_loop_states import (
    SL_STATE_MUTE,
    SL_STATE_OFF,
    SL_STATE_OFF_MUTED,
    SL_STATE_OVERDUBBING,
    SL_STATE_PAUSED,
    SL_STATE_PLAYING,
    SL_STATE_RECORDING,
    SL_STATE_WAIT_START,
    SL_STATE_WAIT_STOP,
)


class ApcTrackGestureTests(unittest.TestCase):
    def test_loop0_tap_record_does_not_send_trigger(self) -> None:
        osc = MagicMock()
        fs = TrackGesture(loop=0, hold_ms=1000.0, debounce_ms=0.0)
        fs.bind(osc, compositor(), 36)
        fs.on_pad_down()
        fs.on_pad_up()
        paths = [c.args[0] for c in osc.send_message.call_args_list]
        self.assertIn("/sl/0/hit", paths)
        self.assertEqual(paths.count("/sl/0/hit"), 1)
        self.assertNotIn("trigger", [c.args[1] for c in osc.send_message.call_args_list])

    def test_record_starts_on_pad_down_not_release(self) -> None:
        """First-beat capture: arm on touch, not on lift."""
        osc = MagicMock()
        fs = TrackGesture(loop=0, hold_ms=1000.0, debounce_ms=0.0)
        fs.bind(osc, compositor(), 36)
        fs.on_pad_down()
        hits = [c.args[1] for c in osc.send_message.call_args_list if c.args[0] == "/sl/0/hit"]
        self.assertEqual(hits, ["record"])
        osc.reset_mock()
        fs.on_pad_up()
        self.assertEqual(osc.send_message.call_args_list, [])

    def test_sync_from_sl_loop0_playing(self) -> None:
        osc = MagicMock()
        fs = TrackGesture(loop=0, hold_ms=1000.0, debounce_ms=0.0)
        fs.bind(osc, compositor(), 36)
        changed = fs.sync_from_sl(SL_STATE_PLAYING)
        self.assertTrue(changed)
        self.assertEqual(fs.state, "playing")

    def test_sync_from_sl_quantize_wait_stays_red(self) -> None:
        osc = MagicMock()
        fs = TrackGesture(loop=2, hold_ms=1000.0, debounce_ms=0.0)
        fs.bind(osc, MagicMock(), 38)
        fs.sync_from_sl(SL_STATE_WAIT_STOP)
        self.assertEqual(fs.state, "recording")
        self.assertTrue(fs.awaiting_quantize)


    def test_quantize_wait_times_out_instead_of_latching(self) -> None:
        """No cycle boundary => release the pad, never latch it forever."""
        osc = MagicMock()
        fs = TrackGesture(loop=0, hold_ms=1000.0, debounce_ms=0.0)
        fs.bind(osc, compositor(), 36)
        with patch.object(gesture_mod, "RING_OUT_ENABLED", False):
            fs.on_pad_down()
            fs.on_pad_up()
            fs.sync_from_sl(SL_STATE_RECORDING)
            fs.on_pad_down()
            fs.on_pad_up()  # stop on pad down -> waits for a boundary
            fs.sync_from_sl(SL_STATE_WAIT_STOP)
            self.assertTrue(fs.awaiting_quantize)
            self.assertTrue(fs._waiting_for_quantize())

            fs._wait_since -= gesture_mod.QUANTIZE_WAIT_TIMEOUT_S + 1.0
            self.assertFalse(fs._waiting_for_quantize())
            self.assertFalse(fs.awaiting_quantize)

            fs.on_pad_down()
            fs.on_pad_up()
        hits = [c.args[1] for c in osc.send_message.call_args_list if c.args[0] == "/sl/0/hit"]
        self.assertEqual(len(hits), 3)

    def test_sync_from_sl_paused_yellow(self) -> None:
        osc = MagicMock()
        fs = TrackGesture(loop=1, hold_ms=1000.0, debounce_ms=0.0)
        fs.bind(osc, compositor(), 37)
        fs.sync_from_sl(SL_STATE_PAUSED)
        self.assertEqual(fs.state, "stopped")

    def test_build_includes_loop0(self) -> None:
        osc = MagicMock()
        _, gestures = build_track_gestures(
            osc=osc,
            compositor=compositor(),
            num_loops=16,
            hold_ms=1000.0,
            debounce_ms=200.0,
        )
        loops = {fs.loop for fs in gestures}
        self.assertIn(0, loops)



class GridEstablishmentTests(unittest.TestCase):
    """First take defines the tempo, then the grid stands alone."""

    def _fs(self, loop, grid, established_cb=None, reanchor_cb=None):
        from scripts.sooperlooper.track_gesture import TrackGesture

        fs = TrackGesture(
            loop=loop, hold_ms=1000.0, debounce_ms=0.0,
            quantized=True, grid=grid,
            on_grid_established=established_cb,
            on_phase_reanchor=reanchor_cb,
        )
        fs.bind(MagicMock(), compositor(), 36 + loop)
        return fs

    def _start_defining_take(self, fs) -> None:
        fs.on_pad_down()
        fs.on_pad_up()
        fs.sync_from_sl(SL_STATE_RECORDING)

    def _close_defining_take(self, fs) -> None:
        fs.on_pad_down()
        fs.on_pad_up()

    def test_first_take_records_instantly_and_sets_tempo(self) -> None:
        from scripts.sooperlooper.sl_grid_state import GridState

        seen = []
        grid = GridState()
        fs = self._fs(0, grid, lambda bpm, bars: seen.append((bpm, bars)))

        self._start_defining_take(fs)
        self.assertTrue(grid.is_pending(0))
        fs.on_pad_down()
        self.assertFalse(fs.awaiting_quantize)

        hits = [
            c.args[1]
            for c in fs._osc.send_message.call_args_list
            if c.args[0] == "/sl/0/hit"
        ]
        self.assertEqual(hits, ["record", "overdub"],
                         "start, then close the take into the ring-out overdub")

        fs.sync_from_sl(SL_STATE_PLAYING)
        fs.sync_loop_len(2.0)
        fs.sync_loop_pos(0.0)

        self.assertTrue(grid.established)
        self.assertEqual(seen, [(120.0, 1)])

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

    def test_hold_clear_drops_the_grid_once_the_engine_reports_empty(self) -> None:
        """Clearing the last clip empties the pads AND the session's tempo.

        Inverted twice. 2026-08-30 it asserted the tempo survived, on Mitch's
        call: "even if we stop all clips ... they should never be cleared
        away." 2026-09-06 he corrected the scope — that sentence is about STOP
        ALL, and clear is a different gesture: "If we clear all clips, then the
        grid should be cleared."

        The timing half of the test is unchanged and still matters: the drop
        waits for the ENGINE to report OFF. Dropping on the gesture alone would
        act on a clear that had not happened yet.
        """
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
        self.assertFalse(grid.established, "an emptied session has no tempo")
        self.assertIsNone(grid.bpm)

    def test_a_re_record_passing_through_off_does_not_drop_the_grid(self) -> None:
        """An unanswered record intent means this loop's emptiness is not a fact.

        Live under multigrid: `slot_runtime._execute_slot_ops` sends `undo_all`
        immediately before a re-record, so the loop passes through OFF on its
        way INTO a take. Reading that as a clear would drop the grid one
        instant before the new clip records against it — and the clip would
        then define a new grid. That is the tempo walk, rebuilt from parts.

        Added with the 2026-09-06 restoration of the clear-drops-the-grid rule,
        because it is the way that rule would have gone wrong.
        """
        from scripts.sooperlooper.sl_grid_state import GridState

        grid = GridState()
        fs = self._fs(0, grid)
        self._start_defining_take(fs)
        self._close_defining_take(fs)
        fs.sync_loop_len(2.0)
        fs.sync_loop_pos(0.0)
        fs.sync_from_sl(SL_STATE_PLAYING)
        self.assertTrue(grid.established)

        fs._expect("recording")          # the record is out, unanswered
        fs.sync_from_sl(SL_STATE_OFF)    # the undo_all echo arrives first
        self.assertTrue(
            grid.established,
            "OFF with a record in flight is a re-record, not a clear",
        )

        # And the same OFF, with nothing outstanding, IS a clear.
        fs._expect(None)
        fs.sync_from_sl(SL_STATE_OFF)
        self.assertFalse(grid.established, "an idle empty loop is a clear")

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


class DoubleTapRecordsOneCycleTests(unittest.TestCase):
    """Double-tap while armed must record exactly one cycle, not cancel."""

    def _fs(self, grid):
        from scripts.sooperlooper.track_gesture import TrackGesture

        fs = TrackGesture(loop=1, hold_ms=1000.0, debounce_ms=0.0,
                            quantized=True, grid=grid)
        fs.bind(MagicMock(), compositor(), 37)
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
        from scripts.sooperlooper.track_gesture import TrackGesture

        fs = TrackGesture(loop=0, hold_ms=1000.0, debounce_ms=0.0)
        fs.bind(MagicMock(), compositor(), 36)
        return fs

    def _sent(self, fs):
        return wire(fs)

    def test_recording_queued_to_play_alternates_red_and_green(self) -> None:
        fs = self._fs()
        fs.sync_from_sl(SL_STATE_WAIT_STOP)
        seq = []
        with patch("scripts.sooperlooper.track_gesture.time.monotonic") as clock:
            for i in range(4):
                clock.return_value = i * gesture_mod.TRANSITION_BLINK_S
                fs.poll_led()
                seq.append(self._sent(fs)[-1])
        # gaps demarcate the colours; without them it reads as one flicker
        self.assertEqual(seq, [gesture_mod.LED_OFF, gesture_mod.LED_RED, gesture_mod.LED_OFF, gesture_mod.LED_GREEN])

    def test_queued_to_record_stays_ableton_standard_red_blink(self) -> None:
        fs = self._fs()
        fs.sync_from_sl(SL_STATE_WAIT_START)
        self.assertEqual(self._sent(fs)[-1], gesture_mod.LED_RED_BLINK)
        self.assertIsNone(fs._led_transition, "no animation for an unambiguous state")

    def test_landing_on_playing_ends_the_animation(self) -> None:
        fs = self._fs()
        fs.sync_from_sl(SL_STATE_WAIT_STOP)
        fs.sync_from_sl(SL_STATE_PLAYING)
        self.assertIsNone(fs._led_transition)
        self.assertEqual(self._sent(fs)[-1], gesture_mod.LED_GREEN)


class QuantizedLaunchTests(unittest.TestCase):
    """Launching a stopped clip lands on the bar, not immediately."""

    def _fs(self):
        from scripts.sooperlooper.track_gesture import TrackGesture

        fs = TrackGesture(loop=2, hold_ms=1000.0, debounce_ms=0.0, quantized=True)
        fs.bind(MagicMock(), compositor(), 38)
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
            wire(fs)[-1],
            gesture_mod.LED_GREEN_BLINK,
        )
        fs.sync_from_sl(SL_STATE_PLAYING)
        self.assertEqual(
            wire(fs)[-1],
            gesture_mod.LED_GREEN,
            "landed — solid green, and only now",
        )


class StopAllIsImmediateTests(unittest.TestCase):
    """Stop All is a transport action; per-clip stop stays musical."""

    def test_stop_all_lifts_quantize_and_settle_restores_it(self) -> None:
        """The lift and the restore are now a second apart, on purpose.

        Restoring in the same breath is the best explanation for the 2026-09-06
        "stop all, and it just resumes again": `set` lands on the OSC thread
        while `hit` waits for the audio thread, so `trigger` could run with
        quantize already back at CYCLE, be deferred to the next boundary, and
        fire after `pause_on`. Quantize now stays at 0 across that window.
        """
        from scripts.sooperlooper.track_gesture import (
            build_track_gestures, settle_stop_all, stop_all_loops,
        )

        osc = MagicMock()
        _, gestures = build_track_gestures(
            osc=osc, compositor=compositor(), num_loops=2,
            hold_ms=1000.0, debounce_ms=0.0
        )
        stop_all_loops(osc, num_loops=2, gestures=gestures)

        sent = [(c.args[0], c.args[1]) for c in osc.send_message.call_args_list]
        self.assertEqual(
            [v for path, v in sent if path == "/sl/-1/set"],
            [["mute_quantized", 0.0], ["quantize", 0.0]],
            "both quantizers lifted, neither restored yet",
        )
        self.assertEqual([v for path, v in sent if path == "/sl/-1/hit"],
                         ["mute_on", "pause_on"])

        settle_stop_all(osc, gestures, log=lambda _m: None)
        sent = [(c.args[0], c.args[1]) for c in osc.send_message.call_args_list]
        self.assertEqual(
            [v for path, v in sent if path == "/sl/-1/set"],
            [["mute_quantized", 0.0], ["quantize", 0.0],
             ["quantize", 0.0], ["mute_quantized", 1.0]],
            "restored only once the engine has been asked what happened",
        )

    def test_stop_all_sends_no_trigger(self) -> None:
        """Mitch, 2026-09-06: 'After I stop all clips, it just resumes again.'

        The burst used to be mute_on, trigger, pause_on -- the trigger added
        2026-08-30 as a rewind so a relaunch came from the top. MEASURED
        2026-09-07 on the real engine (tests/engine/test_engine_claims.py):
        with that trigger a grid clip was still PLAYING 0.4 s and a full cycle
        after Stop All at every one of five bar phases; without it, PAUSED in
        5 of 5. The rewind is the launch's job -- `trigger` from PAUSED waits
        for the bar and plays from the top -- and the mid-loop resumes of
        08-30 were the launch's `pause_off`, not a missing rewind here.
        """
        from scripts.sooperlooper.track_gesture import build_track_gestures, stop_all_loops

        osc = MagicMock()
        _, gestures = build_track_gestures(
            osc=osc, compositor=compositor(), num_loops=2,
            hold_ms=1000.0, debounce_ms=0.0
        )
        stop_all_loops(osc, num_loops=2, gestures=gestures)

        hits = [c.args[1] for c in osc.send_message.call_args_list
                if c.args[0] == "/sl/-1/hit"]
        self.assertNotIn("trigger", hits,
                         "a trigger in the Stop All burst keeps the loop playing")
        self.assertLess(hits.index("mute_on"), hits.index("pause_on"),
                        "mute first, so the pause lands on a silent loop")

    def _grid_rig(self, established: bool):
        from scripts.sooperlooper.track_gesture import build_track_gestures
        from scripts.sooperlooper.sl_grid_state import GridState

        osc = MagicMock()
        _, gestures = build_track_gestures(
            osc=osc, compositor=compositor(), num_loops=2,
            hold_ms=1000.0, debounce_ms=0.0
        )
        grid = GridState()
        if established:
            grid.established = True
            grid.bpm = 120.0
            grid.bars = 1
            grid.cycle_s = 2.0
        for fs in gestures:
            fs.grid = grid
        return osc, gestures

    @staticmethod
    def _quantize_sets(osc):
        return [v for path, v in
                ((c.args[0], c.args[1]) for c in osc.send_message.call_args_list)
                if path == "/sl/-1/set" and v[0] == "quantize"]

    def test_stop_all_leaves_quantize_at_zero(self) -> None:
        """The restore must NOT ride along with the pause.

        `set` is applied on the OSC thread and `hit` is queued for the audio
        thread, so restoring quantize in the same breath can put it back to
        CYCLE before `trigger` is processed. The trigger is then DEFERRED to
        the next boundary, fires after `pause_on`, and plays the loop from
        zero — "I stop all clips and it just resumes again", reported
        2026-09-06 and caught by the verify as "loop 0 state=4".
        """
        from scripts.sooperlooper.track_gesture import stop_all_loops

        osc, gestures = self._grid_rig(established=True)
        stop_all_loops(osc, num_loops=2, gestures=gestures)
        self.assertEqual(self._quantize_sets(osc), [["quantize", 0.0]],
                         "quantize must stay at 0 until the pause is confirmed")

    def test_settle_restores_quantize_to_what_the_grid_says(self) -> None:
        """Positive control: the restore is not an unconditional 1.0.

        With no grid, every loop is deliberately free-form -- the take that
        will DEFINE the grid must not be synced to a cycle inherited from the
        previous session. An unconditional restore would reintroduce exactly
        the imaginary-bar bug set_grid_active was written to kill.
        """
        from scripts.sooperlooper.track_gesture import (
            settle_stop_all, stop_all_loops,
        )

        osc, gestures = self._grid_rig(established=True)
        stop_all_loops(osc, num_loops=2, gestures=gestures)
        settle_stop_all(osc, gestures, log=lambda _m: None)
        self.assertEqual(self._quantize_sets(osc),
                         [["quantize", 0.0], ["quantize", 1.0]],
                         "with a grid established the restore is 1.0")

        osc, gestures = self._grid_rig(established=False)
        stop_all_loops(osc, num_loops=2, gestures=gestures)
        settle_stop_all(osc, gestures, log=lambda _m: None)
        self.assertEqual(self._quantize_sets(osc),
                         [["quantize", 0.0], ["quantize", 0.0]],
                         "with no grid the restore is 0.0, not 1.0")

    def test_stop_all_skips_pending_on_off_muted_empty_loops(self) -> None:
        """Global mute leaves empties at sl=20; must not get pending=stopped."""
        from scripts.sooperlooper.track_gesture import stop_all_loops
        from scripts.sooperlooper.led_table import led_for

        osc = MagicMock()
        empty = TrackGesture(loop=1, hold_ms=1000.0, debounce_ms=0.0)
        empty.bind(MagicMock(), compositor(), 37)
        empty.sync_from_sl(SL_STATE_OFF_MUTED)
        stop_all_loops(osc, num_loops=2, gestures=[empty])
        self.assertIsNone(empty._pending)
        self.assertEqual(led_for(SL_STATE_OFF_MUTED), (0,))

    def test_per_clip_stop_is_still_quantized(self) -> None:
        """Only Stop All is immediate — a single pad stop still waits."""
        from scripts.sooperlooper.track_gesture import TrackGesture

        fs = TrackGesture(loop=1, hold_ms=1000.0, debounce_ms=0.0, quantized=True)
        fs.bind(MagicMock(), compositor(), 37)
        fs.sync_from_sl(SL_STATE_PLAYING)
        fs.on_pad_down(); fs.on_pad_up()
        paths = [c.args[0] for c in fs._osc.send_message.call_args_list]
        self.assertNotIn("/sl/1/set", paths, "must not touch mute_quantized")


class HoldGestureTests(unittest.TestCase):
    def test_hold_delete_shows_red_after_blink_start(self) -> None:
        fs = TrackGesture(
            loop=0,
            hold_ms=2000.0,
            hold_blink_start_ms=500.0,
            debounce_ms=0.0,
        )
        fs.bind(MagicMock(), compositor(), 36)
        fs.sync_from_sl(SL_STATE_PLAYING)
        fs.on_pad_down()
        fs._pad_down_at = time.monotonic() - 0.6
        fs.poll_led()
        self.assertEqual(wire(fs)[-1], 3)

    def test_sync_from_sl_does_not_overwrite_hold_warning(self) -> None:
        fs = TrackGesture(loop=0, hold_ms=1000.0, debounce_ms=0.0)
        fs.bind(MagicMock(), compositor(), 36)
        fs.sync_from_sl(SL_STATE_PLAYING)
        fs.on_pad_down()
        fs._pad_down_at = time.monotonic() - 0.6
        before = len(wire(fs))
        fs.sync_from_sl(SL_STATE_PLAYING)
        self.assertEqual(len(wire(fs)), before,
                         "the hold warning owns the pad until the hold ends")

    def test_hold_blink_starts_after_blink_start_s(self) -> None:
        fs = TrackGesture(
            loop=0,
            hold_ms=2000.0,
            hold_blink_start_ms=500.0,
            debounce_ms=0.0,
        )
        fs.bind(MagicMock(), compositor(), 36)
        fs.on_pad_down()
        fs.sync_from_sl(SL_STATE_PLAYING)

        fs._pad_down_at = time.monotonic() - 0.6
        fs.poll_led()
        self.assertTrue(wire(fs))
        self.assertIn(wire(fs)[-1], (0, 3))

    def test_hold_while_armed_cancels_with_record_not_undo(self) -> None:
        osc = MagicMock()
        fs = TrackGesture(loop=0, hold_ms=1000.0, debounce_ms=0.0)
        fs.bind(osc, compositor(), 36)
        fs.on_pad_down()
        fs.sync_from_sl(SL_STATE_WAIT_START)
        fs._pad_down_at -= 2.0
        fs.poll_hold()
        hits = [c.args[1] for c in osc.send_message.call_args_list if c.args[0].endswith("/hit")]
        self.assertEqual(hits[-1], "record")

    def test_hold_while_recording_cancels_with_undo_all(self) -> None:
        osc = MagicMock()
        fs = TrackGesture(loop=0, hold_ms=1000.0, debounce_ms=0.0)
        fs.bind(osc, compositor(), 36)
        fs.on_pad_down()
        fs.sync_from_sl(SL_STATE_RECORDING)
        fs._pad_down_at -= 2.0
        fs.poll_hold()
        hits = [c.args[1] for c in osc.send_message.call_args_list if c.args[0].endswith("/hit")]
        self.assertEqual(hits[-1], "undo_all")

    def test_hold_on_playing_clip_clears_with_undo_all(self) -> None:
        osc = MagicMock()
        fs = TrackGesture(loop=0, hold_ms=1000.0, debounce_ms=0.0)
        fs.bind(osc, compositor(), 36)
        fs.sync_from_sl(SL_STATE_PLAYING)
        fs.on_pad_down()
        fs._pad_down_at -= 2.0
        fs.poll_hold()
        hits = [c.args[1] for c in osc.send_message.call_args_list if c.args[0].endswith("/hit")]
        self.assertEqual(hits[-1], "undo_all")


if __name__ == "__main__":
    unittest.main()


class OverdubOnePassTests(unittest.TestCase):
    """The take closes into an overdub; it has to end itself one pass later.

    Every engine report below goes through `_state`/`_pos`, which deliver it
    and then run the bench's gesture poll. That is not decoration: since
    2026-08-30 the ring-out has ONE owner, `poll_tail`, and the OSC entry points
    only record what they saw (`track_gesture` module docstring). A test that
    called `sync_loop_pos` and asserted the send had already gone out would be
    asserting that an OSC dispatcher thread ends the overdub — which is the
    defect these tests exist to catch, not the behaviour.

    The negative cases run the poll too. Without it they would pass by
    asserting that nothing happened in code that was never given the chance.
    """

    def _fs(self):
        fs = TrackGesture(loop=0, hold_ms=1000.0, debounce_ms=0.0)
        fs.bind(MagicMock(), compositor(), 36)
        fs.sync_loop_len(2.0)
        return fs

    def _hits(self, fs):
        return [c.args[1] for c in fs._osc.send_message.call_args_list
                if c.args[0] == "/sl/0/hit"]

    def _state(self, fs, sl_state: int) -> None:
        fs.sync_from_sl(sl_state)
        poll_track_gestures([fs])

    def _pos(self, fs, pos: float) -> None:
        fs.sync_loop_pos(pos)
        poll_track_gestures([fs])

    def test_overdub_ends_at_the_first_wrap(self) -> None:
        fs = self._fs()
        self._state(fs, SL_STATE_OVERDUBBING)
        self._pos(fs, 0.1)
        self._pos(fs, 1.9)
        self.assertNotIn("overdub", self._hits(fs), "still inside pass one")
        self._pos(fs, 0.02)
        self.assertEqual(self._hits(fs).count("overdub"), 1)

    def test_overdub_ends_only_once(self) -> None:
        fs = self._fs()
        self._state(fs, SL_STATE_OVERDUBBING)
        self._pos(fs, 1.9)
        self._pos(fs, 0.02)
        self._pos(fs, 1.9)
        self._pos(fs, 0.02)
        self.assertEqual(self._hits(fs).count("overdub"), 1)

    def test_two_wraps_queued_before_one_poll_still_send_one_overdub(self) -> None:
        """The queue cannot smuggle a second toggle past the owner.

        The OSC side now records; if two wrap reports land between polls the
        drain sees two `TAIL_WRAP` events. The second must find the phase gone
        and send nothing — `overdub` is a toggle, so a second send starts a
        fresh one recording the room over the take.
        """
        fs = self._fs()
        self._state(fs, SL_STATE_OVERDUBBING)
        fs.sync_loop_pos(1.9)
        fs.sync_loop_pos(0.02)
        fs.sync_loop_pos(1.9)
        fs.sync_loop_pos(0.02)
        poll_track_gestures([fs])
        self.assertEqual(self._hits(fs).count("overdub"), 1)

    def test_a_wrap_while_merely_playing_sends_nothing(self) -> None:
        fs = self._fs()
        self._state(fs, SL_STATE_PLAYING)
        self._pos(fs, 1.9)
        self._pos(fs, 0.02)
        self.assertNotIn("overdub", self._hits(fs))

    def test_pad_ending_the_overdub_disarms_the_wrap_watch(self) -> None:
        """Ending it by hand must not leave a wrap primed to send a second
        `overdub`, which would turn overdub back ON a pass later."""
        fs = self._fs()
        self._state(fs, SL_STATE_OVERDUBBING)
        self._state(fs, SL_STATE_PLAYING)
        self._pos(fs, 1.9)
        self._pos(fs, 0.02)
        self.assertNotIn("overdub", self._hits(fs))


class StopAllVerificationTests(unittest.TestCase):
    """Stop All must report what it ACHIEVED, not what it requested.

    Reported 2026-08-30: clips restarting 5-10s after Stop All. The only
    evidence was `-> stop all: paused 15 loops`, printed unconditionally at the
    end of the function -- it read the same whether every loop paused, some
    did, or none did. So "they never stopped" and "something restarted them"
    could not be told apart, and the log actively pointed away from the first.
    """

    def _gestures(self, states):
        from scripts.sooperlooper.track_gesture import TrackGesture

        out = []
        for loop, state in enumerate(states):
            fs = TrackGesture(loop=loop, hold_ms=1000.0, debounce_ms=0.0)
            fs.bind(MagicMock(), compositor(), 36 + loop)
            fs.sync_from_sl(state)
            out.append(fs)
        return out

    def test_a_loop_that_did_not_stop_is_named(self) -> None:
        from scripts.sooperlooper.track_gesture import verify_stop_all
        from scripts.sooperlooper.sl_loop_states import (
            SL_STATE_PAUSED, SL_STATE_PLAYING,
        )

        lines = []
        still = verify_stop_all(
            self._gestures([SL_STATE_PAUSED, SL_STATE_PLAYING, SL_STATE_PAUSED]),
            log=lines.append,
        )
        self.assertEqual(still, [(1, SL_STATE_PLAYING)])
        self.assertIn("did NOT stop", lines[0])
        self.assertIn("loop 1", lines[0], "the offender must be named")

    def test_all_stopped_reports_clean(self) -> None:
        """Positive control: a verifier that always cried wolf would be as
        useless as the unconditional print it replaces."""
        from scripts.sooperlooper.track_gesture import verify_stop_all
        from scripts.sooperlooper.sl_loop_states import (
            SL_STATE_OFF, SL_STATE_OFF_MUTED, SL_STATE_PAUSED,
        )

        lines = []
        still = verify_stop_all(
            self._gestures([SL_STATE_PAUSED, SL_STATE_OFF, SL_STATE_OFF_MUTED]),
            log=lines.append,
        )
        self.assertEqual(still, [])
        # NOT "all 3 loops stopped". Two of the three are empty, and an empty
        # loop is always in STOPPED_STATES — it can never fail this check. The
        # old wording made a one-loop test read as a three-loop one.
        self.assertIn("all 1 loop(s) with audio stopped", lines[0])
        self.assertIn("2 empty pad(s) not checked", lines[0])

    def test_settle_re_pauses_a_loop_that_did_not_stop(self) -> None:
        """The instrument has known since 2026-08-30 and only ever wrote it
        down. Now it acts, and says that it did."""
        from scripts.sooperlooper.track_gesture import settle_stop_all
        from scripts.sooperlooper.sl_loop_states import (
            SL_STATE_PAUSED, SL_STATE_PLAYING,
        )

        osc = MagicMock()
        gestures = self._gestures(
            [SL_STATE_PAUSED, SL_STATE_PLAYING, SL_STATE_PAUSED]
        )
        lines = []
        still = settle_stop_all(osc, gestures, log=lines.append)

        self.assertEqual(still, [(1, SL_STATE_PLAYING)])
        hits = [(c.args[0], c.args[1]) for c in osc.send_message.call_args_list
                if c.args[0].endswith("/hit")]
        self.assertIn(("/sl/1/hit", "pause_on"), hits,
                      "the offending loop must actually be paused again")
        self.assertNotIn(("/sl/0/hit", "pause_on"), hits,
                         "loops that stopped must be left alone")
        self.assertTrue(any("CORRECT" in ln and "loop 1" in ln for ln in lines))

    def test_settle_corrects_nothing_when_everything_stopped(self) -> None:
        """Positive control: a corrector that always fires is a stutter."""
        from scripts.sooperlooper.track_gesture import settle_stop_all
        from scripts.sooperlooper.sl_loop_states import SL_STATE_PAUSED

        osc = MagicMock()
        still = settle_stop_all(
            osc, self._gestures([SL_STATE_PAUSED, SL_STATE_PAUSED]),
            log=lambda _m: None,
        )
        self.assertEqual(still, [])
        hits = [c.args[1] for c in osc.send_message.call_args_list
                if c.args[0].endswith("/hit")]
        self.assertEqual(hits, [], "nothing was wrong — send nothing")

    def test_stop_all_returns_a_verification_deadline(self) -> None:
        """Asking in the same breath returns SL's PRE-stop state, which would
        confirm whatever was already there."""
        import time
        from scripts.sooperlooper.track_gesture import (
            build_track_gestures, stop_all_loops, STOP_ALL_VERIFY_S,
        )

        osc = MagicMock()
        _, gestures = build_track_gestures(
            osc=osc, compositor=compositor(), num_loops=2,
            hold_ms=1000.0, debounce_ms=0.0
        )
        before = time.monotonic()
        due = stop_all_loops(osc, num_loops=2, gestures=gestures)
        self.assertIsNotNone(due, "no deadline — nothing would ever verify")
        self.assertGreaterEqual(due, before + STOP_ALL_VERIFY_S)
