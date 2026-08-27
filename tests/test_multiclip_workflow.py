"""Whole multi-clip sessions, driven only by gestures against a real engine model.

This suite exists because of a specific failure of method, not of code.

Every other slot test builds its starting state by assignment —
`rt._tracks[0] = Track(slots=..., active_slot=0)` — and then performs ONE
gesture. That makes each gesture's behaviour easy to state, and it makes an
entire class of bug invisible: anything where a gesture leaves the model in a
state no later gesture can recover from. The setup line quietly repairs what
the previous gesture failed to do, so the next assertion passes.

Three bugs reached Mitch's hands that way in a single afternoon, all of the
same shape, all found by him playing rather than by the suite:

  * recording into a second slot never moved `active_slot`, so the take was
    never registered, the pad stayed dark, and pressing it recorded again;
  * a failed save deleted the take it was preserving;
  * holding a stored clip to delete it played it first.

So the rule here, and it is the whole point of the file: **state is only ever
created by gestures.** No test may assign to `_tracks`. If a test needs a
track with two clips on it, it records two clips. Anything the instrument
cannot reach by playing it does not get to be a premise.

The engine is `FakeSlEngine`, which already models the transitions and the
quantize boundary; it simply had never been wired to `SlotSurface`.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "sooperlooper"))

from tests.fake_sl_engine import FakeSlEngine  # noqa: E402

from apc_grid import GridView, pad_note  # noqa: E402
from led_table import LED_GREEN, LED_OFF, LED_YELLOW  # noqa: E402
from sl_loop_states import SL_STATE_PLAYING, SL_STATE_WAIT_START  # noqa: E402
from slot_runtime import SlotRuntime  # noqa: E402
from slot_surface import SlotSurface  # noqa: E402
from tests.test_slot_surface import FakeOut, build_footswitches  # noqa: E402


class Session(unittest.TestCase):
    """A running appliance: engine, footswitches, runtime, surface."""

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.engine = FakeSlEngine(num_loops=15, quantized=False)
        self.out = FakeOut()
        self.osc: list[tuple[str, list]] = []
        self.fs_by_loop = build_footswitches(self.osc)
        for fs in self.fs_by_loop.values():
            fs.bind(self, FakeOut(), None)
        self.rt = SlotRuntime(
            send=self.send_message,
            clips_dir=self.dir,
            num_tracks=15,
            log=lambda m: None,
        )
        self.rt._save_timeout_s = 0.2
        self.view = GridView(offset=0)
        self.surface = SlotSurface(
            runtime=self.rt,
            footswitches_by_loop=self.fs_by_loop,
            view=self.view,
            midi_out=self.out,
            num_tracks=15,
            scene_launch_notes=tuple(range(0x52, 0x59)),
            hold_s=2.0,
            hold_blink_start_s=0.5,
            log=lambda m: None,
        )

    # -- the OSC bus -------------------------------------------------------
    def send_message(self, path: str, args) -> None:
        """Everything the bench sends reaches the engine, and the engine's
        answer comes back the way SlBenchStateListener delivers it."""
        args = list(args) if isinstance(args, (list, tuple)) else [args]
        self.osc.append((path, args))
        if path.endswith("/save_loop") and args:
            # The engine writes the WAV itself, asynchronously.
            Path(args[0]).write_bytes(b"\0" * 4096)
            return
        self.engine.send_message(path, args[0] if args else "")
        self.deliver()

    def deliver(self) -> None:
        """One round of engine state to everyone who listens."""
        for loop in range(15):
            state = self.engine.state[loop]
            fs = self.fs_by_loop.get(loop)
            if fs is not None:
                fs.sync_from_sl(state)
            length = self.engine.loop_len.get(loop, 0.0)
            if length:
                self.surface.on_loop_len(loop, length)
            self.surface.on_state(loop, state)

    def boundary(self, *, length: float = 2.0) -> None:
        self.engine.boundary(length=length)
        self.deliver()

    # -- gestures (the ONLY way state is allowed to change) ----------------
    def tap(self, slot: int, track: int = 0) -> None:
        note = pad_note(slot, track)
        self.surface.note_down(note)
        self.surface.note_up(note)
        self.deliver()

    def ring_out_pass(self, track: int = 0, *, length: float = 2.0) -> None:
        """Run the loop round once so the ring-out overdub ends.

        Closing a take starts a one-pass overdub that is ended by the WRAP of
        `loop_pos`, not by a timer — so a harness that never delivers loop_pos
        leaves the engine overdubbing forever, and every take looks unfinished.
        """
        fs = self.fs_by_loop.get(track)
        if fs is None:
            return
        fs.sync_loop_len(length)
        for pos in (0.1, length * 0.5, length * 0.95, 0.02):
            fs.sync_loop_pos(pos)
            self.deliver()

    def record_clip(self, slot: int, track: int = 0, *, length: float = 2.0) -> None:
        """Play a take into a slot, exactly as a player would: tap, tap, let it
        come round once."""
        self.tap(slot, track)                 # arm / start
        self.tap(slot, track)                 # close the take into the ring-out
        self.boundary(length=length)
        self.ring_out_pass(track, length=length)

    def colour_of(self, slot: int, track: int = 0) -> int:
        note = pad_note(slot, track)
        last = [m for m in self.out.sent if len(m) == 3 and m[1] == note]
        return last[-1][2] if last else LED_OFF


class OneClipTests(Session):
    def test_recording_a_clip_registers_it(self) -> None:
        self.record_clip(0)
        self.assertTrue(self.rt.track(0).occupied(0), "the take must land somewhere")
        self.assertEqual(self.rt.track(0).active_slot, 0)

    def test_the_clip_is_playing_afterwards(self) -> None:
        self.record_clip(0)
        self.assertEqual(self.engine.state[0], SL_STATE_PLAYING)


class TwoClipsOnOneTrackTests(Session):
    """The gesture Mitch reported: record clip 1, then clip 2, on one column."""

    def test_the_second_take_lands_on_the_second_slot(self) -> None:
        self.record_clip(0)
        self.record_clip(1)
        track = self.rt.track(0)
        self.assertTrue(track.occupied(1), "the second take was never registered")
        self.assertEqual(track.active_slot, 1)

    def test_the_first_clip_survives_the_second(self) -> None:
        self.record_clip(0)
        self.record_clip(1)
        self.assertTrue(self.rt.track(0).occupied(0), "clip 1 was lost")
        self.assertTrue(self.rt.clip_path(0, 0).exists(), "clip 1's file was deleted")

    def test_pressing_the_second_pad_again_does_not_re_record(self) -> None:
        """Reported: the pad went dark and clicking it recorded over the take."""
        self.record_clip(0)
        self.record_clip(1)
        self.osc.clear()
        self.tap(1)
        cmds = [a[0] for p, a in self.osc if p.endswith("/hit")]
        self.assertNotIn("record", cmds, "that pad already holds a take")

    def test_the_second_pad_is_not_dark(self) -> None:
        self.record_clip(0)
        self.record_clip(1)
        self.assertNotEqual(self.colour_of(1), LED_OFF,
                            "a slot holding a take must never read as empty")

    def test_the_first_pad_shows_a_stored_clip(self) -> None:
        self.record_clip(0)
        self.record_clip(1)
        self.assertEqual(self.colour_of(0), LED_YELLOW,
                         "holds audio, but is not the one sounding")

    def test_going_back_to_the_first_clip_plays_it(self) -> None:
        self.record_clip(0)
        self.record_clip(1)
        self.osc.clear()
        self.tap(0)
        self.boundary()
        self.assertIn("/sl/0/load_loop", [p for p, _ in self.osc])
        self.assertEqual(self.rt.track(0).active_slot, 0)


class ThreeClipsTests(Session):
    def test_every_take_is_kept(self) -> None:
        for slot in range(3):
            self.record_clip(slot)
        track = self.rt.track(0)
        for slot in range(3):
            self.assertTrue(track.occupied(slot), f"slot {slot} lost its take")

    def test_every_take_but_the_live_one_is_on_disk(self) -> None:
        """The newest take is still only in the engine buffer — it reaches disk
        when the track switches away from it. That is the design (one buffer
        per track), and it is also the window in which a power cut loses a
        take, so it is stated here rather than left implicit."""
        for slot in range(3):
            self.record_clip(slot)
        for slot in (0, 1):
            self.assertTrue(self.rt.clip_path(0, slot).exists(), f"slot {slot} file gone")
            self.assertFalse(self.rt.track(0).slot(slot).dirty)
        self.assertTrue(self.rt.track(0).slot(2).dirty, "the live take is unflushed")

    def test_only_the_last_one_is_active(self) -> None:
        for slot in range(3):
            self.record_clip(slot)
        self.assertEqual(self.rt.track(0).active_slot, 2)

    def test_at_most_one_pad_in_the_column_is_green(self) -> None:
        """One buffer per track means one audible clip."""
        for slot in range(3):
            self.record_clip(slot)
        greens = [s for s in range(8) if self.colour_of(s) == LED_GREEN]
        self.assertLessEqual(len(greens), 1, f"green on slots {greens}")


class QuantizedSessionTests(Session):
    """The appliance's ACTUAL configuration, which the tests above do not use.

    On the Pi the grid starts off so the first take can record instantly, and
    `on_grid_established` turns quantize ON once that take defines the tempo.
    Every clip after the first is therefore armed and waits for the bar — a
    different code path from the free-running one, and the one Mitch plays.
    """

    def establish_grid(self) -> None:
        """What on_grid_established does: later clips count in to the bar."""
        self.engine.quantized = True
        for fs in self.fs_by_loop.values():
            fs.quantized = True

    def record_first_then_grid(self, slot: int = 0) -> None:
        self.record_clip(slot)
        self.establish_grid()

    def record_quantized_clip(self, slot: int, track: int = 0,
                              *, length: float = 2.0) -> None:
        """Arm, wait for the bar, record, close at the bar, ring out."""
        self.tap(slot, track)                  # arm -> WAIT_START
        self.boundary(length=length)           # the bar arrives -> RECORDING
        self.tap(slot, track)                  # close -> WAIT_STOP
        self.boundary(length=length)           # the bar arrives -> PLAYING
        self.ring_out_pass(track, length=length)

    def test_the_second_clip_is_armed_not_recorded_instantly(self) -> None:
        self.record_first_then_grid(0)
        self.osc.clear()
        self.tap(1)
        self.assertEqual(self.engine.state[0], SL_STATE_WAIT_START,
                         "a quantized take counts in to the bar")

    def test_the_second_take_lands_on_its_own_slot(self) -> None:
        self.record_first_then_grid(0)
        self.record_quantized_clip(1)
        track = self.rt.track(0)
        self.assertTrue(track.occupied(1), "the quantized take was never registered")
        self.assertEqual(track.active_slot, 1)

    def test_the_first_clip_survives(self) -> None:
        self.record_first_then_grid(0)
        self.record_quantized_clip(1)
        self.assertTrue(self.rt.track(0).occupied(0))
        self.assertTrue(self.rt.clip_path(0, 0).exists())

    def test_the_second_pad_is_not_dark_afterwards(self) -> None:
        self.record_first_then_grid(0)
        self.record_quantized_clip(1)
        self.assertNotEqual(self.colour_of(1), LED_OFF)

    def test_pressing_it_again_does_not_re_record(self) -> None:
        self.record_first_then_grid(0)
        self.record_quantized_clip(1)
        self.osc.clear()
        self.tap(1)
        cmds = [a[0] for p, a in self.osc if p.endswith("/hit")]
        self.assertNotIn("record", cmds, "that pad already holds a take")


class TwoTracksTests(Session):
    def test_recording_on_one_track_does_not_disturb_another(self) -> None:
        self.record_clip(0, track=0)
        self.record_clip(0, track=1)
        self.assertTrue(self.rt.track(0).occupied(0))
        self.assertTrue(self.rt.track(1).occupied(0))
        self.assertEqual(self.rt.track(0).active_slot, 0)
        self.assertEqual(self.rt.track(1).active_slot, 0)


if __name__ == "__main__":
    unittest.main()
