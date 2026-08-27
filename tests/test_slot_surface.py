"""SlotSurface — dispatch, pending resolution, and repaint."""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "sooperlooper"))

from apc_footswitch import LoopFootswitch  # noqa: E402
from apc_grid import GridView, pad_note  # noqa: E402
from led_table import LED_GREEN, LED_OFF, LED_YELLOW  # noqa: E402
from sl_loop_states import (  # noqa: E402
    SL_STATE_MUTE,
    SL_STATE_OFF,
    SL_STATE_PLAYING,
    SL_STATE_RECORDING,
)
from slot_matrix import Slot, Track  # noqa: E402
from slot_runtime import SlotRuntime  # noqa: E402
from slot_surface import SlotSurface  # noqa: E402


class FakeOut:
    def __init__(self) -> None:
        self.sent: list[list[int]] = []

    def send_message(self, msg) -> None:
        self.sent.append(list(msg))


class _OscStub:
    def __init__(self, sink: list) -> None:
        self._sink = sink

    def send_message(self, path, args) -> None:
        if isinstance(args, str):
            self._sink.append((path, [args]))
        else:
            self._sink.append((path, list(args)))


def build_footswitches(sink: list, *, num: int = 15) -> dict[int, LoopFootswitch]:
    out: dict[int, LoopFootswitch] = {}
    for loop in range(num):
        fs = LoopFootswitch(
            loop=loop, hold_ms=2000, debounce_ms=0, multigrid=True, quantized=False
        )
        fs.bind(_OscStub(sink), FakeOut(), None)
        out[loop] = fs
    return out


class SurfaceCase(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.osc: list[tuple[str, list]] = []
        self.out = FakeOut()
        self.rt = SlotRuntime(
            send=lambda p, a: self.osc.append((p, a)),
            clips_dir=self.dir,
            num_tracks=15,
        )
        self.fs_by_loop = build_footswitches(self.osc)
        self.surface = SlotSurface(
            runtime=self.rt,
            footswitches_by_loop=self.fs_by_loop,
            view=GridView(offset=0),
            midi_out=self.out,
            num_tracks=15,
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)

    def colour_of(self, note: int) -> int | None:
        for msg in reversed(self.out.sent):
            if msg[1] == note:
                return msg[2]
        return None


class DispatchTests(SurfaceCase):
    def test_every_grid_note_is_handled(self) -> None:
        for row in range(8):
            for col in range(8):
                self.assertTrue(self.surface.handles(pad_note(row, col)))

    def test_a_non_grid_note_is_declined(self) -> None:
        self.assertFalse(self.surface.handles(0x62))
        self.assertFalse(self.surface.press(0x62))

    def test_a_press_on_an_empty_cell_starts_a_take(self) -> None:
        self.assertTrue(self.surface.note_down(pad_note(3, 2)))
        self.assertIn(("/sl/2/hit", ["record"]), self.osc)

    def test_the_row_is_the_slot_and_the_column_is_the_track(self) -> None:
        self.surface.note_down(pad_note(5, 6))
        self.assertIn(("/sl/6/hit", ["record"]), self.osc)


class PendingResolutionTests(SurfaceCase):
    def _armed_switch(self) -> None:
        (self.rt.clip_path(0, 4)).write_bytes(b"\0" * 4096)
        self.rt._tracks[0] = Track(
            slots=(Slot("a.wav"), None, None, None, Slot("e.wav"), *([None] * 3)),
            active_slot=0,
        )
        self.surface.on_state(0, SL_STATE_PLAYING)
        self.surface.note_down(pad_note(4, 0))

    def test_a_switch_is_pending_until_the_engine_moves(self) -> None:
        self._armed_switch()
        self.assertIsNotNone(self.rt.track(0).pending)
        self.assertEqual(self.rt.track(0).active_slot, 0)

    def test_the_engine_reaching_the_target_resolves_it(self) -> None:
        self._armed_switch()
        self.surface.on_state(0, SL_STATE_PLAYING)
        self.assertIsNone(self.rt.track(0).pending)
        self.assertEqual(self.rt.track(0).active_slot, 4)

    def test_a_pending_stop_resolves_only_on_silence(self) -> None:
        self.rt._tracks[0] = Track(slots=(Slot("a.wav"), *([None] * 7)), active_slot=0)
        self.fs_by_loop[0].sl_state = SL_STATE_PLAYING
        self.surface.on_state(0, SL_STATE_PLAYING)
        self.surface.note_down(pad_note(0, 0))
        self.assertIsNotNone(self.rt.track(0).pending)
        self.surface.on_state(0, SL_STATE_PLAYING)
        self.assertIsNotNone(self.rt.track(0).pending, "still sounding — not yet")
        self.surface.on_state(0, SL_STATE_MUTE)
        self.assertIsNone(self.rt.track(0).pending)

    def test_another_tracks_state_does_not_resolve_this_one(self) -> None:
        self._armed_switch()
        self.surface.on_state(5, SL_STATE_PLAYING)
        self.assertIsNotNone(self.rt.track(0).pending)


class PaintTests(SurfaceCase):
    def test_the_active_playing_cell_is_green_and_a_sibling_yellow(self) -> None:
        self.rt._tracks[1] = Track(
            slots=(Slot("a.wav"), None, Slot("c.wav"), *([None] * 5)), active_slot=0
        )
        self.fs_by_loop[1].sl_state = SL_STATE_PLAYING
        self.surface.on_state(1, SL_STATE_PLAYING)
        self.assertEqual(self.colour_of(pad_note(0, 1)), LED_GREEN)
        self.assertEqual(self.colour_of(pad_note(2, 1)), LED_YELLOW)

    def test_repaint_is_quiet_when_nothing_changed(self) -> None:
        self.surface.on_state(0, SL_STATE_OFF)
        before = len(self.out.sent)
        self.surface.repaint()
        self.assertEqual(len(self.out.sent), before)

    def test_a_bank_change_repaints_everything(self) -> None:
        self.surface.repaint()
        before = len(self.out.sent)
        self.surface.set_view(GridView(offset=7))
        self.assertGreaterEqual(len(self.out.sent) - before, 64)

    def test_blank_darkens_all_64(self) -> None:
        self.surface.blank()
        dark = [m for m in self.out.sent if m[2] == LED_OFF]
        self.assertGreaterEqual(len(dark), 64)


class HoldClearTests(SurfaceCase):
    def test_hold_clear_fires_after_hold_s(self) -> None:
        self.rt._tracks[0] = Track(slots=(Slot("a.wav"), *([None] * 7)), active_slot=0)
        self.surface._hold_s = 0.05
        self.surface.note_down(pad_note(0, 0))
        self.osc.clear()
        self.surface._pad_down_at = 0.0
        self.surface.poll_hold()
        self.assertIn(("/sl/0/hit", ["undo_all"]), self.osc)


class SceneRowTests(SurfaceCase):
    def setUp(self) -> None:
        super().setUp()
        self.surface._scene_launch_notes = (0x52, 0x53)

    def test_scene_led_lit_when_row_not_fully_playing(self) -> None:
        self.rt._tracks[0] = Track(slots=(Slot("a.wav"), *([None] * 7)), active_slot=0)
        self.surface.on_state(0, SL_STATE_MUTE)
        self.surface.repaint_scenes(force=True)
        scene_msgs = [m for m in self.out.sent if m[1] == 0x52]
        self.assertTrue(scene_msgs)
        self.assertEqual(scene_msgs[-1][2], 1)

    def test_scene_leds_stay_dark_when_a_row_is_empty(self) -> None:
        self.surface.repaint_scenes(force=True)
        scene_msgs = [m for m in self.out.sent if m[1] == 0x52]
        self.assertTrue(scene_msgs)
        self.assertEqual(scene_msgs[-1][2], 0)

    def test_engine_sync_marks_a_take_and_repaints(self) -> None:
        self.surface.note_down(pad_note(2, 0))
        self.fs_by_loop[0].sl_state = SL_STATE_RECORDING
        self.surface.on_loop_len(0, 4.0)
        self.surface.on_state(0, SL_STATE_PLAYING)
        self.assertTrue(self.rt.track(0).occupied(2))
        self.assertEqual(self.colour_of(pad_note(2, 0)), LED_GREEN)

    def test_scene_press_launches_stopped_cells(self) -> None:
        self.rt.clip_path(0, 0).write_bytes(b"\0" * 4096)
        self.rt.clip_path(1, 0).write_bytes(b"\0" * 4096)
        self.rt._tracks[0] = Track(slots=(Slot("a.wav"), *([None] * 7)), active_slot=0)
        self.rt._tracks[1] = Track(slots=(Slot("b.wav"), *([None] * 7)), active_slot=0)
        self.surface.on_state(0, SL_STATE_MUTE)
        self.surface.on_state(1, SL_STATE_MUTE)
        self.osc.clear()
        self.surface.scene_press(0)
        paths = [p for p, _ in self.osc if p.endswith("/load_loop")]
        self.assertEqual(len(paths), 2)
