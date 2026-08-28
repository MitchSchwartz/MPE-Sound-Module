"""The matrix's OWN vocabulary, end to end through the surface.

Everything the single-clip surface has no concept of: launch, switch,
record-into-another-slot, clear, cancel. `test_multigrid_equivalence.py`
cannot reach any of it — there is no reference behaviour to compare against —
so this is the only net under these paths.

That gap was not theoretical. The 2026-08-27 "pad goes green then yellow and
never records" bug lived in exactly this space: `_prepare_record` cleared the
engine correctly, but nothing told the gesture, so its next gesture was a
mute. Equivalence was green throughout.

Each test asserts the OSC the engine actually receives, in order, because
every bug in this area so far has been a command missing, doubled, or sent by
the wrong owner.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "sooperlooper"))

from apc_grid import GridView, pad_note  # noqa: E402
from sl_loop_states import (  # noqa: E402
    SL_STATE_MUTE,
    SL_STATE_OFF,
    SL_STATE_PLAYING,
)
from slot_matrix import PENDING_SWITCH, Slot, Track  # noqa: E402
from slot_runtime import SlotRuntime  # noqa: E402
from slot_surface import SlotSurface  # noqa: E402
from tests.test_slot_surface import (  # noqa: E402
    FakeOut,
    build_track_gestures,
    feed_wrap,
)


class VocabularyCase(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.osc: list[tuple[str, list]] = []
        self.out = FakeOut()
        self.fs_by_loop = build_track_gestures(self.osc)
        self.engine_saves = True     # does save_loop actually produce a file?
        self.rt = SlotRuntime(
            send=self._send,
            clips_dir=self.dir,
            num_tracks=15,
            log=lambda m: None,
        )
        self.view = GridView(offset=0)
        self.surface = SlotSurface(
            runtime=self.rt,
            gestures_by_loop=self.fs_by_loop,
            view=self.view,
            midi_out=self.out,
            num_tracks=15,
            scene_launch_notes=tuple(range(0x52, 0x59)),
            hold_s=2.0,
            hold_blink_start_s=0.5,
            log=lambda m: None,
        )

    def _send(self, path: str, args) -> None:
        """Record the OSC, and emulate the one side effect that matters.

        SooperLooper writes the WAV itself, asynchronously. A harness that
        skips that models an engine which never saves, so every flush looks
        like a failure and the success path goes untested.
        """
        args = list(args)
        self.osc.append((path, args))
        if path.endswith("/save_loop") and self.engine_saves and args:
            Path(args[0]).write_bytes(b"\0" * 4096)

    def clip(self, track: int, slot: int, name: str = "c.wav") -> Slot:
        p = self.rt.clip_path(track, slot)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\0" * 4096)
        return Slot(name, dirty=False)

    def state(self, loop: int, value: int) -> None:
        fs = self.fs_by_loop.get(loop)
        if fs is not None:
            fs.sync_from_sl(int(value))
        self.surface.on_state(loop, int(value))

    def hits(self, loop: int = 0) -> list[str]:
        return [a[0] for p, a in self.osc if p == f"/sl/{loop}/hit"]

    def paths(self) -> list[str]:
        return [p for p, _ in self.osc]

    def tap(self, slot: int, track: int = 0) -> None:
        note = pad_note(slot, track)
        self.surface.note_down(note)
        self.surface.note_up(note)


class LaunchTests(VocabularyCase):
    """An occupied slot on a track with no buffer bound: load it and play."""

    def setUp(self) -> None:
        super().setUp()
        self.rt._tracks[0] = Track(
            slots=(None, self.clip(0, 1), *([None] * 6)), active_slot=None
        )
        self.state(0, SL_STATE_OFF)
        self.osc.clear()

    def test_the_clip_is_loaded_before_it_is_unmuted(self) -> None:
        """Unmuting first plays whatever the buffer still held — the previous
        clip — for as long as the load takes."""
        self.tap(1)
        paths = self.paths()
        self.assertIn("/sl/0/load_loop", paths)
        self.assertLess(paths.index("/sl/0/load_loop"), paths.index("/sl/0/hit"))
        # pause_off + trigger, not mute_off: mute_off does not lift a pause,
        # and stop_all_loops pauses every loop. See LAUNCH_COMMANDS.
        self.assertEqual(self.hits(), ["pause_off", "trigger"])

    def test_the_slot_becomes_active_only_once_the_engine_confirms(self) -> None:
        """Binding at press time would make the pad claim a clip is sounding
        before the engine has started it."""
        self.tap(1)
        self.assertIsNone(self.rt.track(0).active_slot)
        self.state(0, SL_STATE_PLAYING)
        self.assertEqual(self.rt.track(0).active_slot, 1)

    def test_no_record_is_ever_sent(self) -> None:
        """The slot holds a take. Recording would destroy it."""
        self.tap(1)
        self.assertNotIn("record", self.hits())

    def test_a_missing_clip_file_does_not_bind_the_slot(self) -> None:
        """The manifest can outlive the WAV. Binding anyway would leave the
        track pointing at silence with no way back."""
        self.rt._tracks[0] = Track(
            slots=(None, Slot("gone.wav"), *([None] * 6)), active_slot=None
        )
        self.rt.clip_path(0, 1).unlink(missing_ok=True)
        self.osc.clear()
        self.tap(1)
        self.assertEqual(self.osc, [])
        self.assertIsNone(self.rt.track(0).active_slot)


class SwitchTests(VocabularyCase):
    """Another occupied slot while one is already playing."""

    def setUp(self) -> None:
        super().setUp()
        self.rt._tracks[0] = Track(
            slots=(self.clip(0, 0, "a.wav"), self.clip(0, 1, "b.wav"), *([None] * 6)),
            active_slot=0,
        )
        self.state(0, SL_STATE_PLAYING)
        self.osc.clear()

    def test_the_incoming_clip_is_loaded_and_unmuted(self) -> None:
        """And not one byte of it before the wrap.

        `load_loop` swaps the buffer the instant it lands — measured
        2026-08-26, it does NOT halt playback (PI5-LOOPER-SEAM-WRAP.md). Sent
        at press it therefore replaces the audio under the player's fingers
        part-way through a bar, which is exactly what "switching isn't
        quantized" sounded like.
        """
        self.tap(1)
        self.assertNotIn(
            "/sl/0/load_loop",
            self.paths(),
            "loading at press overwrites the take that is still sounding",
        )
        feed_wrap(self.fs_by_loop[0])
        self.assertIn("/sl/0/load_loop", self.paths())
        self.assertEqual(self.hits(), ["pause_off", "trigger"])

    def test_a_switch_is_recorded_as_pending_until_the_engine_confirms(self) -> None:
        self.tap(1)
        pending = self.rt.track(0).pending
        self.assertIsNotNone(pending)
        self.assertEqual(pending.kind, PENDING_SWITCH)
        self.assertEqual(pending.from_slot, 0)
        self.assertEqual(pending.to_slot, 1)

    def test_the_active_slot_moves_only_once_the_engine_confirms(self) -> None:
        """Moving it at press time would make the pad lie about what is
        sounding for a whole cycle."""
        self.tap(1)
        self.assertEqual(self.rt.track(0).active_slot, 0)
        # The loop was already PLAYING before the press, so another PLAYING is
        # not news and certainly not a bar line.
        self.state(0, SL_STATE_PLAYING)
        self.assertEqual(self.rt.track(0).active_slot, 0)
        feed_wrap(self.fs_by_loop[0])
        self.assertEqual(self.rt.track(0).active_slot, 1)
        self.assertIsNone(self.rt.track(0).pending)

    def test_a_clean_outgoing_clip_is_not_resaved(self) -> None:
        self.tap(1)
        self.assertNotIn("/sl/0/save_loop", self.paths())


class RecordIntoAnotherSlotTests(VocabularyCase):
    """The path that broke on 2026-08-27."""

    def setUp(self) -> None:
        super().setUp()
        self.rt._tracks[0] = Track(
            slots=(self.clip(0, 0, "a.wav"), *([None] * 7)), active_slot=0
        )
        self.state(0, SL_STATE_PLAYING)
        self.osc.clear()

    def test_the_outgoing_clip_is_left_sounding_for_the_engine_to_stop(self) -> None:
        """Measured 2026-08-28: `record` over a PLAYING loop arms (WAIT_START)
        and the loop keeps sounding to the wrap, entering RECORDING there. So
        the stop already lands on the take's own boundary, and muting or
        emptying the buffer at press time only creates a silent gap of up to a
        bar. Previously asserted as ["mute_on", "undo_all", "record"]."""
        self.tap(3)
        self.assertEqual(self.hits(), ["record"])

    def test_the_gesture_owns_the_record_command(self) -> None:
        """One record state machine. The runtime prepares the buffer; the
        gesture is the gesture's, or the two disagree about the take."""
        self.tap(3)
        self.assertEqual(self.hits().count("record"), 1)

    def test_a_dirty_outgoing_clip_is_flushed_before_the_buffer_is_reused(self) -> None:
        """One buffer per track: arming reuses it, so unflushed audio dies."""
        self.rt._tracks[0] = Track(
            slots=(Slot("a.wav", dirty=True), *([None] * 7)), active_slot=0
        )
        self.osc.clear()
        self.tap(3)
        paths = self.paths()
        self.assertIn("/sl/0/save_loop", paths)
        self.assertLess(paths.index("/sl/0/save_loop"),
                        [p for p in paths].index("/sl/0/hit"))


class FlushDurabilityTests(VocabularyCase):
    """Saving take 1 must never be able to destroy take 1.

    Reported 2026-08-27: "when I record clip 2, seems like clip 1 is deleted."
    """

    def setUp(self) -> None:
        super().setUp()
        self.clip(0, 0, "a.wav")
        self.rt._tracks[0] = Track(
            slots=(Slot("a.wav", dirty=True), *([None] * 7)), active_slot=0
        )
        self.state(0, SL_STATE_PLAYING)
        self.osc.clear()

    def test_a_save_that_never_lands_leaves_the_original_intact(self) -> None:
        """The engine can refuse or be slow. Deleting first and hoping is how
        a recorded take is lost for good."""
        path = self.rt.clip_path(0, 0)
        original = path.read_bytes()
        self.engine_saves = False             # the save never lands
        self.engine_saves = False
        self.rt._save_timeout_s = 0.05
        self.tap(3)
        self.assertTrue(path.exists(), "take 1 must survive a failed save")
        self.assertEqual(path.read_bytes(), original)

    def test_a_failed_flush_keeps_the_slot_dirty_and_bound(self) -> None:
        """Still unsaved and still the live buffer — the surface must not show
        it as safely on disk."""
        self.engine_saves = False
        self.rt._save_timeout_s = 0.05
        self.tap(3)
        self.assertTrue(self.rt.track(0).slot(0).dirty)
        self.assertEqual(self.rt.track(0).active_slot, 0, "the switch was refused")

    def test_a_failed_flush_does_not_record_over_the_take(self) -> None:
        """Refusing to switch is only protection if the record is refused too."""
        self.engine_saves = False
        self.rt._save_timeout_s = 0.05
        self.tap(3)
        self.assertNotIn("record", self.hits())
        self.assertNotIn("undo_all", self.hits())


class ClearTests(VocabularyCase):
    """Long press on a slot that is NOT the track's active one."""

    def setUp(self) -> None:
        super().setUp()
        self.rt._tracks[0] = Track(
            slots=(self.clip(0, 0, "a.wav"), self.clip(0, 1, "b.wav"), *([None] * 6)),
            active_slot=0,
        )
        self.state(0, SL_STATE_PLAYING)
        self.osc.clear()

    def _hold(self, slot: int) -> None:
        self.surface._hold_s = 0.05
        self.surface.note_down(pad_note(slot, 0))
        self.surface._pad_down_at = 0.0
        self.surface.poll_hold()

    def test_clearing_a_stored_slot_touches_only_the_disk(self) -> None:
        """That slot is not in the buffer. Any engine command here would hit
        the clip that IS playing.

        This caught a real one: the launch fired on pad DOWN, so holding a
        stored clip to delete it loaded and played it first, then deleted it.
        A stored slot now acts on RELEASE, which is also where the gesture
        puts mute and launch.
        """
        path = self.rt.clip_path(0, 1)
        self._hold(1)
        self.assertFalse(path.exists())
        self.assertIsNone(self.rt.track(0).slot(1))
        self.assertEqual(self.osc, [], "the playing clip must not be disturbed")

    def test_the_playing_slot_is_left_alone(self) -> None:
        self._hold(1)
        self.assertEqual(self.rt.track(0).active_slot, 0)
        self.assertTrue(self.rt.clip_path(0, 0).exists())


if __name__ == "__main__":
    unittest.main()
