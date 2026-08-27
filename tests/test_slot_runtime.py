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
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "sooperlooper"))

from slot_matrix import ACT_NOOP, ACT_SWITCH, Slot, Track  # noqa: E402
from slot_runtime import MIN_CLIP_BYTES, SlotRuntime  # noqa: E402
from sl_loop_states import (  # noqa: E402
    SL_STATE_MUTE,
    SL_STATE_OFF,
    SL_STATE_PLAYING,
    SL_STATE_RECORDING,
    SL_STATE_WAIT_START,
)


class RuntimeCase(unittest.TestCase):
    def setUp(self) -> None:
        # The save poll sleeps between checks. Under a fake clock that only
        # moves elsewhere, that loop would never reach its deadline — so the
        # patched sleep advances time, the way a real one does.
        self.clock = [0.0]
        self._sleep_patch = patch(
            "slot_runtime.time.sleep",
            side_effect=lambda s: self.clock.__setitem__(0, self.clock[0] + s),
        )
        self._sleep_patch.start()
        self.addCleanup(self._sleep_patch.stop)
        self.dir = Path(tempfile.mkdtemp())
        self.sent: list[tuple[str, list]] = []
        self.logs: list[str] = []
        self.rt = SlotRuntime(
            send=lambda p, a: self.sent.append((p, a)),
            clips_dir=self.dir,
            num_tracks=15,
            log=self.logs.append,
            now=lambda: self.clock[0],
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
        self.assertEqual(self.sent[-1][1], ["mute_off"])

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
        # save_loop lands the file the moment it is asked for.
        real_send = self.rt._send

        def send(path, args):
            real_send(path, args)
            if path.endswith("/save_loop"):
                Path(args[0]).write_bytes(b"\0" * 4096)

        self.rt._send = send
        plan = self.rt.press(0, 3, sl_state=SL_STATE_PLAYING)
        self.assertEqual(plan.action, ACT_SWITCH)
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
        plan = self.rt.press(0, 3, sl_state=SL_STATE_PLAYING)
        self.assertEqual(plan.action, ACT_NOOP)
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
    def test_clearing_the_active_slot_drops_the_buffer_and_the_file(self) -> None:
        p = self._clip(0, 1)
        self.rt._tracks[0] = Track(slots=(None, Slot("b.wav"), *([None] * 6)), active_slot=1)
        self.rt.press(0, 1, sl_state=SL_STATE_PLAYING, hold=True)
        self.assertFalse(p.exists())
        self.assertIsNone(self.rt.track(0).slot(1))
        self.assertIsNone(self.rt.track(0).active_slot)
        self.assertIn(("/sl/0/hit", ["undo_all"]), self.sent)

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

    def test_record_into_another_slot_mutes_and_flushes_first(self) -> None:
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
        self.assertIn(("/sl/0/hit", ["mute_on"]), self.sent)
        self.assertTrue(any(p.endswith("/save_loop") for p, _ in self.sent))
        self.assertIn(("/sl/0/hit", ["undo_all"]), self.sent)

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
        self.rt.boundary(1)
        self.assertIsNone(self.rt.track(1).pending)
        self.assertEqual(self.rt.track(1).active_slot, 2)

    def test_clip_paths_are_unique_per_cell(self) -> None:
        seen = {self.rt.clip_path(t, s) for t in range(15) for s in range(8)}
        self.assertEqual(len(seen), 15 * 8)
