"""SlotRuntime — the ordering rules a pure planner cannot express.

The failures worth testing here all destroy a take or play the wrong audio,
and none of them raise.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "sooperlooper"))

from slot_matrix import (  # noqa: E402
    ACT_LAUNCH,
    ACT_NOOP,
    ACT_SWITCH,
    Slot,
    SlotPlan,
    Track,
)
from slot_flush import MIN_CLIP_BYTES  # noqa: E402
from slot_runtime import (  # noqa: E402
    DEFERRED_LAUNCH_GRACE_S,
    SlotRuntime,
)
from sl_loop_states import (  # noqa: E402
    SL_STATE_MUTE,
    SL_STATE_OFF,
    SL_STATE_PAUSED,
    SL_STATE_PLAYING,
    SL_STATE_RECORDING,
    SL_STATE_WAIT_START,
)


class RuntimeCase(unittest.TestCase):
    def setUp(self) -> None:
        # No sleep patch. The save is polled from the idle loop now, so
        # nothing inside the runtime advances the clock on its own — and a
        # harness that quietly advanced time would hide a press that blocks.
        self.clock = [0.0]
        self.dir = Path(tempfile.mkdtemp())
        self.sent: list[tuple[str, list]] = []
        self.logs: list[str] = []
        #: Is any loop sounding. False by default — most cases here press into
        #: a stopped session, where a launch is immediate. A test that presses
        #: a PLAYING track sets this, because that is what makes the launch
        #: deferred in production.
        self.sounding = [False]
        self.rt = SlotRuntime(
            send=lambda p, a: self.sent.append((p, a)),
            clips_dir=self.dir,
            num_tracks=15,
            log=self.logs.append,
            now=lambda: self.clock[0],
            session_sounding=lambda: self.sounding[0],
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)

    def _clip(self, track: int, slot: int, size: int = 4096) -> Path:
        p = self.rt.clip_path(track, slot)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\0" * size)
        return p

    def paths(self) -> list[str]:
        return [p for p, _ in self.sent]


class LaunchOrderTests(RuntimeCase):
    def test_load_precedes_unmute(self) -> None:
        """Unmuting first plays the OUTGOING clip for the length of the load —
        audibly the wrong take, with nothing in any log to say so."""
        self._clip(0, 2)
        self.rt._tracks[0] = Track(slots=(None, None, Slot("x"), *([None] * 5)))
        self.rt.press(0, 2, sl_state=SL_STATE_OFF)
        paths = self.paths()
        self.assertIn("/sl/0/load_loop", paths)
        self.assertIn("/sl/0/hit", paths)
        self.assertLess(paths.index("/sl/0/load_loop"), paths.index("/sl/0/hit"))
        self.assertEqual(self.sent[-1][1], ["trigger"])

    def test_a_missing_clip_file_does_not_unmute(self) -> None:
        """Without the file check the engine unmutes whatever the buffer still
        holds — the previous clip, playing under a pad that shows the new one."""
        self.rt._tracks[0] = Track(slots=(None, None, Slot("gone.wav"), *([None] * 5)))
        plan = self.rt.press(0, 2, sl_state=SL_STATE_OFF)
        self.assertEqual(plan.action, ACT_NOOP)
        self.assertNotIn("/sl/0/hit", self.paths())


class SwitchSafetyTests(RuntimeCase):
    def _dirty_track_with_target(self) -> None:
        self._clip(0, 3)
        self.rt._tracks[0] = Track(
            slots=(Slot("a.wav", dirty=True), None, None, Slot("b.wav"), *([None] * 4)),
            active_slot=0,
        )

    def test_a_dirty_buffer_is_saved_before_the_switch(self) -> None:
        self._dirty_track_with_target()
        self.sounding[0] = True     # the outgoing clip is audible
        # save_loop lands the file the moment it is asked for.
        real_send = self.rt._send

        def send(path, args):
            real_send(path, args)
            if path.endswith("/save_loop"):
                Path(args[0]).write_bytes(b"\0" * 4096)

        self.rt._send = send
        plan = self.rt.press(0, 3, sl_state=SL_STATE_PLAYING)
        self.assertEqual(plan.action, ACT_SWITCH)
        # The flush is disk-only and happens at press: it must not wait for the
        # boundary, or a slow save would eat into the switch. The load waits,
        # because it is the one that touches audio.
        self.assertIn("/sl/0/save_loop", self.paths())
        self.assertNotIn("/sl/0/load_loop", self.paths())
        self.rt.land_pending(0)
        paths = self.paths()
        self.assertLess(paths.index("/sl/0/save_loop"), paths.index("/sl/0/load_loop"))
        self.assertFalse(self.rt.track(0).slot(0).dirty, "flushed slot is clean")

    def test_a_failed_save_refuses_the_switch(self) -> None:
        """The take is still only in the buffer. Loading over it destroys it,
        and the player would have no way to know until they came back to it."""
        self._dirty_track_with_target()
        self.clock[0] = 0.0
        original = self.rt._send

        def send(path, args):
            original(path, args)
            if path.endswith("/save_loop"):
                self.clock[0] += 10.0  # save never produces a file; time runs out

        self.rt._send = send
        # The press parks rather than blocking: the save is in flight and the
        # buffer must not be reused, but the surface stays responsive.
        plan = self.rt.press(0, 3, sl_state=SL_STATE_PLAYING)
        self.assertEqual(plan.action, ACT_NOOP)
        self.assertIn("waiting for save", plan.note)
        self.assertEqual(self.rt.awaiting_tracks(), (0,))
        self.assertNotIn("/sl/0/load_loop", self.paths())

        # Time runs out with no file. The replay refuses, exactly as the
        # blocking version did.
        self.clock[0] += 10.0
        resumed = self.rt.resume_awaiting(0, sl_state=SL_STATE_PLAYING)
        self.assertIsNotNone(resumed)
        self.assertEqual(resumed.action, ACT_NOOP)
        self.assertNotIn("/sl/0/load_loop", self.paths())
        self.assertIn("REFUSING", " ".join(self.logs))

    def test_a_stub_save_counts_as_failure(self) -> None:
        """A header-only WAV exists, so an existence check would pass it."""
        self._dirty_track_with_target()
        original = self.rt._send

        def send(path, args):
            original(path, args)
            if path.endswith("/save_loop"):
                Path(args[0]).write_bytes(b"\0" * (MIN_CLIP_BYTES - 1))
                self.clock[0] += 10.0

        self.rt._send = send
        self.assertEqual(self.rt.press(0, 3, sl_state=SL_STATE_PLAYING).action, ACT_NOOP)
        self.assertNotIn("/sl/0/load_loop", self.paths())

    def test_a_clean_buffer_is_not_resaved(self) -> None:
        self._clip(0, 3)
        self.rt._tracks[0] = Track(
            slots=(Slot("a.wav", dirty=False), None, None, Slot("b.wav"), *([None] * 4)),
            active_slot=0,
        )
        self.rt.press(0, 3, sl_state=SL_STATE_PLAYING)
        self.assertNotIn("/sl/0/save_loop", self.paths())


class ClearTests(RuntimeCase):
    def test_forgetting_the_active_slot_drops_the_file_without_touching_the_engine(
        self,
    ) -> None:
        """A long press on the active slot is forwarded to the gesture,
        which sends `undo_all` itself. The runtime's half is the disk only —
        sending the engine command here too would double it."""
        p = self._clip(0, 1)
        self.rt._tracks[0] = Track(slots=(None, Slot("b.wav"), *([None] * 6)), active_slot=1)
        self.sent.clear()
        self.assertTrue(self.rt.forget_active_slot(0))
        self.assertFalse(p.exists())
        self.assertIsNone(self.rt.track(0).slot(1))
        self.assertIsNone(self.rt.track(0).active_slot)
        self.assertEqual(self.sent, [], "the gesture owns the engine")

    def test_forgetting_an_unbound_track_is_a_noop(self) -> None:
        self.assertFalse(self.rt.forget_active_slot(0))

    def test_a_hold_on_the_active_slot_is_forwarded_not_cleared(self) -> None:
        p = self._clip(0, 1)
        self.rt._tracks[0] = Track(slots=(None, Slot("b.wav"), *([None] * 6)), active_slot=1)
        self.sent.clear()
        self.rt.press(0, 1, sl_state=SL_STATE_PLAYING, hold=True)
        self.assertTrue(p.exists(), "the surface drives this, not press()")
        self.assertEqual(self.sent, [])

    def test_clearing_an_inactive_slot_leaves_the_playing_buffer_alone(self) -> None:
        """undo_all here would wipe the clip the player is listening to."""
        self._clip(0, 4)
        self.rt._tracks[0] = Track(
            slots=(Slot("a.wav"), None, None, None, Slot("e.wav"), *([None] * 3)),
            active_slot=0,
        )
        self.rt.press(0, 4, sl_state=SL_STATE_PLAYING, hold=True)
        self.assertNotIn("/sl/0/hit", self.paths())
        self.assertEqual(self.rt.track(0).active_slot, 0)


class BookkeepingTests(RuntimeCase):
    def test_record_arm_sets_active_slot(self) -> None:
        self.rt.press(0, 3, sl_state=SL_STATE_OFF)
        self.assertEqual(self.rt.track(0).active_slot, 3)
        self.assertFalse(self.rt.track(0).occupied(3))

    def test_record_into_another_slot_flushes_without_silencing(self) -> None:
        self.rt._tracks[0] = Track(
            slots=(Slot("a.wav", dirty=True), None, *([None] * 6)), active_slot=0
        )
        original = self.rt._send

        def send(path, args):
            original(path, args)
            if path.endswith("/save_loop"):
                Path(args[0]).write_bytes(b"\0" * MIN_CLIP_BYTES)

        self.rt._send = send
        self.rt.press(0, 1, sl_state=SL_STATE_PLAYING)
        # The take must still reach disk — that half is a data-loss guard.
        self.assertTrue(any(p.endswith("/save_loop") for p, _ in self.sent))
        # ...but the audio must not be cut. SL holds playback to the boundary
        # and swaps to recording there; silencing here just makes the track
        # quiet for up to a bar first.
        self.assertNotIn(("/sl/0/hit", ["mute_on"]), self.sent)
        self.assertNotIn(("/sl/0/hit", ["undo_all"]), self.sent)

    def test_record_into_another_slot_clears_the_buffer_first(self) -> None:
        self.rt._tracks[0] = Track(
            slots=(Slot("a.wav"), None, *([None] * 6)), active_slot=0
        )
        self.rt.press(0, 1, sl_state=SL_STATE_MUTE)
        self.assertIn(("/sl/0/hit", ["undo_all"]), self.sent)
        self.assertEqual(self.rt.track(0).active_slot, 1)

    def test_a_finished_take_is_dirty_and_active(self) -> None:
        self.rt.mark_recorded(2, 5, len_s=4.0, sl_state=SL_STATE_PLAYING)
        track = self.rt.track(2)
        self.assertEqual(track.active_slot, 5)
        self.assertTrue(track.slot(5).dirty, "unsaved audio must block a switch")

    def test_the_boundary_promotes_a_pending_switch(self) -> None:
        self._clip(1, 2)
        self.rt._tracks[1] = Track(
            slots=(Slot("a.wav"), None, Slot("c.wav"), *([None] * 5)), active_slot=0
        )
        self.rt.press(1, 2, sl_state=SL_STATE_PLAYING)
        self.assertIsNotNone(self.rt.track(1).pending)
        self.assertEqual(self.rt.track(1).active_slot, 0, "not yet — the bar has not come")
        self.rt.land_pending(1)
        self.assertIsNone(self.rt.track(1).pending)
        self.assertEqual(self.rt.track(1).active_slot, 2)

    def test_clip_paths_are_unique_per_cell(self) -> None:
        seen = {self.rt.clip_path(t, s) for t in range(15) for s in range(8)}
        self.assertEqual(len(seen), 15 * 8)


class LoadLoopArity(RuntimeCase):
    """A one-argument load_loop is silently discarded by SooperLooper.

    This is why a queued switch moved the binding but never the audio: the
    model advanced and both pads repainted while the engine was never told.
    """

    def test_switch_sends_the_reply_paths(self) -> None:
        from dataclasses import replace as _replace
        from slot_matrix import ACT_LAUNCH, SlotPlan

        self._clip(0, 1)
        self.rt._tracks[0] = _replace(
            self.rt.track(0), active_slot=0
        ).with_slot(1, Slot(file=self.rt.clip_path(0, 1).name, len_s=1.0))
        self.rt._launch(SlotPlan(action=ACT_LAUNCH, track=0, slot=1))

        loads = [a for path, a in self.sent if path.endswith("/load_loop")]
        self.assertTrue(loads, "no load_loop was sent")
        self.assertEqual(
            len(loads[0]), 3,
            "load_loop needs filename + return_url + error_path; a shorter "
            "message does not match SL's handler signature and is dropped",
        )


class LaunchAfterStopAll(RuntimeCase):
    """Stop All pauses every loop; a launch has to lift that pause.

    This sequence had ZERO coverage in either direction, which is how it
    shipped. `stop_all_loops` sends `pause_on` to every loop, and the matrix's
    launch used to send only `mute_off` — which does not lift a pause. The clip
    loaded, the pad lit, and nothing came out. The single-clip path had always
    sent `pause_off` + `trigger`; the matrix was a sibling that never inherited
    the rule.
    """

    def test_launch_lifts_a_pause_rather_than_only_unmuting(self) -> None:
        self._clip(0, 1)
        self.rt._tracks[0] = Track(slots=(None, Slot("b.wav"), *([None] * 6)))
        self.rt.press(0, 1, sl_state=SL_STATE_OFF)
        hits = [a[0] for p, a in self.sent if p.endswith("/hit")]
        self.assertIn(
            "pause_off", hits,
            "a launch after Stop All is silent unless the pause is lifted",
        )
        self.assertIn("trigger", hits)

    def test_relaunching_a_dirty_active_slot_also_lifts_the_pause(self) -> None:
        """The 'already bound, just unmute' shortcut needs the rule too."""
        self.rt._tracks[0] = Track(
            slots=(Slot("a.wav", dirty=True), *([None] * 7)), active_slot=0
        )
        self.rt._launch(SlotPlan(action=ACT_LAUNCH, track=0, slot=0))
        hits = [a[0] for p, a in self.sent if p.endswith("/hit")]
        self.assertIn("pause_off", hits)
        self.assertIn("trigger", hits)

    def test_no_launch_path_relies_on_mute_off_alone(self) -> None:
        """One answer to 'how do you start a loop', not two that drift."""
        from slot_runtime import LAUNCH_COMMANDS

        self.assertEqual(LAUNCH_COMMANDS, ("pause_off", "trigger"))
        source = Path("scripts/sooperlooper/slot_runtime.py").read_text()
        self.assertNotIn(
            '["mute_off"]', source,
            "a launch path is sending mute_off directly again",
        )


class StrandedSwitchTests(unittest.TestCase):
    """A held launch must never wait for a wrap that is not coming.

    The wrap is the boundary, and it rides on `loop_pos`. If those updates dry
    up — engine restart, a listener that quietly stopped — the switch waits for
    ever behind a blinking pad and the instrument is dead with no error. A late
    switch is bad; a switch that never happens is worse, so the hold expires.
    """

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.osc: list[tuple[str, list]] = []
        self.clock = [1000.0]
        # The pressed track is PLAYING throughout this class, so the session is
        # sounding and every launch here is a deferred one.
        self.rt = SlotRuntime(
            send=lambda p, a: self.osc.append((p, a)),
            clips_dir=self.dir,
            num_tracks=15,
            now=lambda: self.clock[0],
            session_sounding=lambda: True,
        )
        for slot in (0, 1):
            self.rt.clip_path(0, slot).write_bytes(b"\0" * 4096)
        self.rt._tracks[0] = Track(
            slots=(Slot("a.wav"), Slot("b.wav"), *([None] * 6)), active_slot=0
        )
        self.rt.press(0, 1, sl_state=SL_STATE_PLAYING)

    def paths(self) -> list[str]:
        return [p for p, _ in self.osc]

    def test_the_hold_survives_until_the_grace_runs_out(self) -> None:
        self.clock[0] += DEFERRED_LAUNCH_GRACE_S - 0.1
        self.assertFalse(self.rt.expire_deferred(0, sl_state=SL_STATE_PLAYING))
        self.assertNotIn("/sl/0/load_loop", self.paths())

    def test_a_wrap_that_never_comes_launches_anyway(self) -> None:
        self.clock[0] += DEFERRED_LAUNCH_GRACE_S + 0.1
        self.assertTrue(self.rt.expire_deferred(0, sl_state=SL_STATE_PLAYING))
        self.assertIn("/sl/0/load_loop", self.paths())
        self.assertIsNone(self.rt.track(0).pending, "and the model catches up")
        self.assertEqual(self.rt.track(0).active_slot, 1)

    def test_a_stopped_track_drops_the_launch_instead_of_firing_it(self) -> None:
        """Stop All is the case that made this urgent.

        The grace timer's premise is "playing, but no wrap is reaching us". A
        paused loop can never produce a wrap, so the timer concluded none was
        coming and STARTED THE TRACK about five seconds after the panic button.
        Silence is a reason to drop the launch, never to force it.
        """
        self.clock[0] += DEFERRED_LAUNCH_GRACE_S + 0.1
        self.assertFalse(
            self.rt.expire_deferred(0, sl_state=SL_STATE_PAUSED),
            "a stopped track must not be started by a timer",
        )
        self.assertNotIn("/sl/0/load_loop", self.paths())
        self.assertFalse(self.rt.has_deferred(0), "and the launch is dropped")
        self.assertIsNone(self.rt.track(0).pending)

    def test_stop_all_abandons_every_queued_launch(self) -> None:
        self.assertTrue(self.rt.has_deferred(0))
        self.rt.abandon_all()
        self.assertFalse(self.rt.has_deferred(0))
        self.assertIsNone(self.rt.track(0).pending)
        self.assertEqual(self.rt.awaiting_tracks(), ())
        self.clock[0] += DEFERRED_LAUNCH_GRACE_S + 0.1
        self.assertFalse(self.rt.expire_deferred(0, sl_state=SL_STATE_PLAYING))
        self.assertNotIn("/sl/0/load_loop", self.paths())

    def test_a_wrap_that_does_come_leaves_nothing_to_expire(self) -> None:
        self.rt.land_pending(0)
        self.clock[0] += DEFERRED_LAUNCH_GRACE_S * 10
        self.assertFalse(self.rt.expire_deferred(0, sl_state=SL_STATE_PLAYING), "the hold is gone")
        self.assertEqual(self.paths().count("/sl/0/load_loop"), 1)


class NonBlockingSaveTests(RuntimeCase):
    """A pad press must never sleep. This is an instrument.

    The save used to be waited out inside `press()` with `time.sleep` in a
    loop — up to the full 2s timeout on the failure path, during which the
    bench read no pads and updated no LEDs. The safety rule it enforced is
    unchanged: the buffer is not reused until the take is on disk. Only the
    waiting moved.
    """

    def setUp(self) -> None:
        super().setUp()
        self._clip(0, 3)
        self.rt._tracks[0] = Track(
            slots=(Slot("a.wav", dirty=True), None, None, Slot("b.wav"),
                   *([None] * 4)),
            active_slot=0,
        )

    def _saved_tmp(self) -> Path:
        for path, args in self.sent:
            if path.endswith("/save_loop"):
                return Path(args[0])
        raise AssertionError("no save_loop was sent")

    def test_the_press_does_not_sleep(self) -> None:
        import time as real_time

        before = real_time.monotonic()
        plan = self.rt.press(0, 3, sl_state=SL_STATE_PLAYING)
        elapsed = real_time.monotonic() - before
        self.assertLess(elapsed, 0.05, "a press that sleeps is a deaf surface")
        self.assertEqual(plan.action, ACT_NOOP)
        self.assertIn("waiting for save", plan.note)

    def test_the_parked_press_completes_when_the_save_lands(self) -> None:
        self.rt.press(0, 3, sl_state=SL_STATE_PLAYING)
        self.assertIsNone(
            self.rt.resume_awaiting(0, sl_state=SL_STATE_PLAYING),
            "nothing on disk yet — stay parked",
        )
        self._saved_tmp().write_bytes(b"\0" * 4096)
        resumed = self.rt.resume_awaiting(0, sl_state=SL_STATE_PLAYING)
        self.assertIsNotNone(resumed)
        self.assertEqual(resumed.action, ACT_SWITCH)
        self.assertFalse(self.rt.track(0).slot(0).dirty, "the take is on disk")
        self.assertEqual(self.rt.awaiting_tracks(), ())

    def test_a_failed_save_does_not_retry_for_ever(self) -> None:
        """The replay must refuse, not re-park.

        Replaying would find the slot still dirty, start a fresh save, park
        again, and spin — the take never reaching disk is exactly what makes
        the replay futile.
        """
        self.rt.press(0, 3, sl_state=SL_STATE_PLAYING)
        self.clock[0] += 10.0
        resumed = self.rt.resume_awaiting(0, sl_state=SL_STATE_PLAYING)
        self.assertEqual(resumed.action, ACT_NOOP)
        self.assertEqual(self.rt.awaiting_tracks(), (), "parked no longer")
        self.assertIsNone(self.rt.resume_awaiting(0, sl_state=SL_STATE_PLAYING))
        saves = [p for p, _ in self.sent if p.endswith("/save_loop")]
        self.assertEqual(len(saves), 1, "one attempt, not a retry storm")

    def test_the_buffer_is_never_reused_while_the_save_is_in_flight(self) -> None:
        self.rt.press(0, 3, sl_state=SL_STATE_PLAYING)
        self.assertNotIn("/sl/0/load_loop", self.paths())
        self.assertNotIn("undo_all", [a[0] for p, a in self.sent
                                      if p.endswith("/hit")])


class StartingIntoSilenceTests(RuntimeCase):
    """Nothing is playing: the clip starts NOW and becomes the downbeat.

    Mitch, 2026-08-30, correcting my first attempt at this:

        "When I've stopped all and I start a clip, we've reset the phase to
        zero — technically, quantized start could and probably is most simply
        left as true, but it should also mean that start happens immediately."

    Right, and it is not a special case. A downbeat is where the music starts;
    you cannot be late for something that has not begun. My first version made
    this launch wait for the "next" bar line, which sits in silence for a whole
    bar before the first note.
    """

    def setUp(self) -> None:
        super().setUp()
        self.phase_zeros: list[float] = []
        self.sounding = [False]
        self.rt = SlotRuntime(
            send=lambda p, a: self.sent.append((p, a)),
            clips_dir=self.dir,
            num_tracks=15,
            log=self.logs.append,
            now=lambda: self.clock[0],
            grid_boundary=lambda: 10.0,
            session_sounding=lambda: self.sounding[0],
            mark_phase_zero=lambda: self.phase_zeros.append(self.clock[0]),
        )
        self._clip(1, 3)
        self.rt._tracks[1] = Track(
            slots=(None, None, None, Slot("x"), *([None] * 4)), active_slot=None
        )

    def test_a_clip_started_into_silence_fires_immediately(self) -> None:
        self.rt.press(1, 3, sl_state=SL_STATE_PAUSED)
        self.assertIn("/sl/1/load_loop", self.paths(),
                      "a bar of silence before the first note")
        self.assertFalse(self.rt.has_deferred(1))

    def test_and_that_clip_becomes_the_downbeat(self) -> None:
        """Without this the grid keeps counting from whenever the phase was
        last zeroed, and every later clip lines up with nothing audible."""
        self.clock[0] = 42.0
        self.rt.press(1, 3, sl_state=SL_STATE_PAUSED)
        self.assertEqual(self.phase_zeros, [42.0])

    def test_a_failed_launch_does_not_move_the_downbeat(self) -> None:
        """No clip file: nothing starts, so nothing defines the beat."""
        self.rt._tracks[2] = Track(
            slots=(None, Slot("missing.wav"), *([None] * 6)), active_slot=None
        )
        self.rt.press(2, 1, sl_state=SL_STATE_PAUSED)
        self.assertEqual(self.phase_zeros, [])

    def test_a_track_joining_music_that_is_playing_still_waits(self) -> None:
        """THE OTHER HALF OF THE BUG.

        The old check asked whether the PRESSED track was playing. Launching a
        stopped track while another track played therefore fired instantly —
        landing off the beat of the very music it was joining. The question is
        whether the session is sounding, not whether this track is.
        """
        self.sounding[0] = True
        self.rt.press(1, 3, sl_state=SL_STATE_PAUSED)
        self.assertNotIn("/sl/1/load_loop", self.paths())
        self.assertTrue(self.rt.has_deferred(1))
        self.assertEqual(self.phase_zeros, [], "joining does not redefine the beat")

    def test_a_silent_track_joining_lands_on_the_grid_bar_line(self) -> None:
        """It has no wrap of its own — nothing is playing on THIS track — so
        the grid clock is the only thing that can fire it."""
        self.sounding[0] = True
        self.rt.press(1, 3, sl_state=SL_STATE_PAUSED)
        self.clock[0] = 9.9
        self.assertEqual(self.rt.poll_grid_wait(), [])
        self.clock[0] = 10.0
        self.assertEqual(self.rt.poll_grid_wait(), [1])
        self.assertIn("/sl/1/load_loop", self.paths())

    def test_the_grace_timer_does_not_drop_a_grid_launch(self) -> None:
        """`expire_deferred` drops a queued launch when the pressed track is
        silent, because a silent track can never produce a wrap. A launch
        waiting on the GRID is exactly that case and must survive it."""
        self.sounding[0] = True
        self.rt.press(1, 3, sl_state=SL_STATE_PAUSED)
        self.clock[0] = DEFERRED_LAUNCH_GRACE_S + 1.0
        self.rt.expire_deferred(1, sl_state=SL_STATE_PAUSED)
        self.assertTrue(self.rt.has_deferred(1), "the grid launch was dropped")
