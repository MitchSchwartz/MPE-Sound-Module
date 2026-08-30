"""SlotSurface — dispatch, pending resolution, and repaint."""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "sooperlooper"))

from track_gesture import TrackGesture  # noqa: E402
from apc_grid import GridView, pad_note  # noqa: E402
from led_table import (  # noqa: E402
    LED_GREEN,
    LED_OFF,
    LED_YELLOW,
    RECORD_TO_PLAY,
)
from sl_loop_states import (  # noqa: E402
    SL_STATE_MUTE,
    SL_STATE_OFF,
    SL_STATE_PLAYING,
    SL_STATE_RECORDING,
    SL_STATE_WAIT_STOP,
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


def build_track_gestures(sink: list, *, num: int = 15) -> dict[int, TrackGesture]:
    out: dict[int, TrackGesture] = {}
    for loop in range(num):
        fs = TrackGesture(
            loop=loop, hold_ms=2000, debounce_ms=0, multigrid=True, quantized=True
        )
        fs.bind(_OscStub(sink), FakeOut(), None)
        out[loop] = fs
    return out


def feed_wrap(gesture, *, length: float = 2.0) -> None:
    """Drive one loop wrap through a gesture's playhead.

    The wrap is the bench's only quantize boundary — a queued switch is
    released here — so a test about switch timing has to cross one for real
    rather than assert on the state that happens to be true at press.
    """
    gesture.sync_loop_len(length)
    gesture.sync_loop_pos(length * 0.99)
    gesture.sync_loop_pos(0.0)


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
        self.fs_by_loop = build_track_gestures(self.osc)
        self.surface = SlotSurface(
            runtime=self.rt,
            gestures_by_loop=self.fs_by_loop,
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


    def state(self, loop: int, value: int) -> None:
        """Deliver an engine state exactly as SlBenchStateListener does.

        Both halves, in this order. A test that updated only the surface left
        the gesture's mirror at idle, so a forwarded press planned from
        stale state — the harness reporting a bug the appliance does not have.
        """
        fs = self.fs_by_loop.get(loop)
        if fs is not None:
            fs.sync_from_sl(int(value))
        self.surface.on_state(loop, int(value))

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
        self.state(0, SL_STATE_PLAYING)
        # A stored slot acts on release now, so the gesture needs both edges.
        self.surface.note_down(pad_note(4, 0))
        self.surface.note_up(pad_note(4, 0))

    def test_a_switch_is_pending_until_the_engine_moves(self) -> None:
        self._armed_switch()
        self.assertIsNotNone(self.rt.track(0).pending)
        self.assertEqual(self.rt.track(0).active_slot, 0)

    def test_the_engine_reaching_the_target_resolves_it(self) -> None:
        """The WRAP resolves it, not the state.

        This used to resolve on `sl_state in ACTIVE_PLAY`, which on a switch is
        already true when the pad goes down — the outgoing take is playing.
        So the "pending" resolved instantly and the switch landed in the middle
        of a bar. Reported as "switching works but isn't quantized".
        """
        self._armed_switch()
        self.state(0, SL_STATE_PLAYING)
        self.assertIsNotNone(
            self.rt.track(0).pending, "playing is not a boundary"
        )
        self.assertEqual(self.rt.track(0).active_slot, 0)
        feed_wrap(self.fs_by_loop[0])
        self.assertIsNone(self.rt.track(0).pending)
        self.assertEqual(self.rt.track(0).active_slot, 4)

    def test_stopping_the_active_clip_is_the_gesture_not_a_pending(self) -> None:
        """The matrix has no pending for its own active slot any more. The
        gesture mutes it, exactly as on the single-clip surface — the
        engine does its own quantizing, so a second queue here only added a
        way for the two to disagree about when the stop happened."""
        self.rt._tracks[0] = Track(slots=(Slot("a.wav"), *([None] * 7)), active_slot=0)
        self.fs_by_loop[0].sl_state = SL_STATE_PLAYING
        self.state(0, SL_STATE_PLAYING)
        self.osc.clear()
        self.surface.note_down(pad_note(0, 0))
        self.surface.note_up(pad_note(0, 0))
        self.assertIsNone(self.rt.track(0).pending)
        self.assertIn(("/sl/0/hit", ["mute_on"]), self.osc)

    def test_another_tracks_state_does_not_resolve_this_one(self) -> None:
        self._armed_switch()
        self.state(5, SL_STATE_PLAYING)
        self.assertIsNotNone(self.rt.track(0).pending)


class PaintTests(SurfaceCase):
    def test_the_active_playing_cell_is_green_and_a_sibling_yellow(self) -> None:
        self.rt._tracks[1] = Track(
            slots=(Slot("a.wav"), None, Slot("c.wav"), *([None] * 5)), active_slot=0
        )
        self.fs_by_loop[1].sl_state = SL_STATE_PLAYING
        self.state(1, SL_STATE_PLAYING)
        self.assertEqual(self.colour_of(pad_note(0, 1)), LED_GREEN)
        self.assertEqual(self.colour_of(pad_note(2, 1)), LED_YELLOW)

    def test_repaint_is_quiet_when_nothing_changed(self) -> None:
        self.state(0, SL_STATE_OFF)
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
    def test_hold_on_the_active_slot_clears_engine_and_disk_once(self) -> None:
        """Split gesture: the gesture sends `undo_all`, the runtime deletes
        the WAV and unbinds. Neither does the other's half."""
        path = self.rt.clip_path(0, 0)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\0" * 4096)
        self.rt._tracks[0] = Track(slots=(Slot("a.wav"), *([None] * 7)), active_slot=0)
        self.surface._hold_s = 0.05
        self.fs_by_loop[0].hold_ms = 50.0
        self.surface.note_down(pad_note(0, 0))
        self.osc.clear()
        self.surface._pad_down_at = 0.0
        self.fs_by_loop[0]._pad_down_at = 0.0
        self.surface.poll_hold()
        self.assertEqual([o for o in self.osc if o[1] == ["undo_all"]],
                         [("/sl/0/hit", ["undo_all"])], "exactly once")
        self.assertFalse(path.exists())
        self.assertIsNone(self.rt.track(0).active_slot)


class RecordOverPlayingTrackTests(SurfaceCase):
    """Recording into an empty slot while the track is playing something else.

    Reported from the appliance 2026-08-27: the pad went green, then yellow on
    a second press, and no take was ever recorded.
    """

    def setUp(self) -> None:
        super().setUp()
        self.rt._tracks[0] = Track(
            slots=(Slot("a.wav", dirty=False), *([None] * 7)), active_slot=0
        )
        self.state(0, SL_STATE_PLAYING)
        self.osc.clear()

    def test_pressing_an_empty_slot_records(self) -> None:
        self.surface.note_down(pad_note(3, 0))
        self.surface.note_up(pad_note(3, 0))
        cmds = [a[0] for p, a in self.osc if p == "/sl/0/hit"]
        self.assertIn("record", cmds,
                      "an empty slot means record, whatever the track was doing")
        self.assertNotIn("mute_on", cmds[cmds.index("record"):],
                         "the mute belongs BEFORE the record, not instead of it")

    def test_the_outgoing_clip_keeps_sounding_until_the_engine_stops_it(self) -> None:
        """One buffer per track, but the swap belongs on the bar, not the press.

        SL arms `record` over a playing loop and keeps it sounding to the wrap.
        Muting and emptying here pre-empted that and left the track silent for
        up to a full bar."""
        self.surface.note_down(pad_note(3, 0))
        cmds = [a[0] for p, a in self.osc if p == "/sl/0/hit"]
        self.assertIn("record", cmds)
        self.assertNotIn("mute_on", cmds)
        self.assertNotIn("undo_all", cmds)

    def test_the_gesture_is_not_left_thinking_it_is_playing(self) -> None:
        """The root cause: the runtime cleared the engine, but the gesture's
        own state machine still read `playing`, so its gesture was mute."""
        self.surface.note_down(pad_note(3, 0))
        self.assertEqual(self.fs_by_loop[0].state, "recording")


class SceneRowTests(SurfaceCase):
    def setUp(self) -> None:
        super().setUp()
        # The real seven, in hardware order: the row a note means depends on
        # its INDEX in this tuple, so a truncated stand-in silently addresses
        # different rows.
        self.surface._scene_launch_notes = tuple(range(0x52, 0x59))
        # Row 0 has NO scene button — Stop All Clips (0x59) occupies that
        # position on the panel. The lowest scene launcher, 0x58, is beside
        # row 1, so that is the row these tests drive.
        self.scene_row = 1
        self.scene_note = 0x58

    def test_scene_led_lit_when_row_not_fully_playing(self) -> None:
        self.rt._tracks[0] = Track(
            slots=(None, Slot("a.wav"), *([None] * 6)), active_slot=1
        )
        self.state(0, SL_STATE_MUTE)
        self.surface.repaint_scenes(force=True)
        scene_msgs = [m for m in self.out.sent if m[1] == self.scene_note]
        self.assertTrue(scene_msgs)
        self.assertEqual(scene_msgs[-1][2], 1)

    def test_scene_leds_stay_dark_when_a_row_is_empty(self) -> None:
        self.surface.repaint_scenes(force=True)
        scene_msgs = [m for m in self.out.sent if m[1] == self.scene_note]
        self.assertTrue(scene_msgs)
        self.assertEqual(scene_msgs[-1][2], 0)

    def test_engine_sync_marks_a_take_and_repaints(self) -> None:
        self.surface.note_down(pad_note(2, 0))
        self.surface.note_up(pad_note(2, 0))
        self.fs_by_loop[0].sl_state = SL_STATE_RECORDING
        self.surface.on_loop_len(0, 4.0)
        # Closing a take goes through WAIT_STOP — the quantized tail, where the
        # pad blinks red/green to say "recording is STILL RUNNING until the
        # bar". This is the blinker that went missing on the matrix: it is a
        # SEQUENCE over time, so a colour derived from the current sl_state can
        # only ever be one frame of it, and `_led_transition` — which holds the
        # sequence — was never armed because multigrid skipped `_sync_led`.
        fs = self.fs_by_loop[0]
        self.state(0, SL_STATE_WAIT_STOP)
        self.assertEqual(fs._led_transition, RECORD_TO_PLAY)
        self.assertIn(self.colour_of(pad_note(2, 0)), RECORD_TO_PLAY)

        self.state(0, SL_STATE_PLAYING)
        self.assertTrue(self.rt.track(0).occupied(2))
        self.assertIsNone(fs._led_transition, "the tail is over")
        self.assertEqual(self.colour_of(pad_note(2, 0)), LED_GREEN)

    def test_scene_press_launches_stopped_cells(self) -> None:
        self.rt.clip_path(0, 0).write_bytes(b"\0" * 4096)
        self.rt.clip_path(1, 0).write_bytes(b"\0" * 4096)
        self.rt._tracks[0] = Track(slots=(Slot("a.wav"), *([None] * 7)), active_slot=0)
        self.rt._tracks[1] = Track(slots=(Slot("b.wav"), *([None] * 7)), active_slot=0)
        self.state(0, SL_STATE_MUTE)
        self.state(1, SL_STATE_MUTE)
        self.osc.clear()
        self.surface.scene_press(0)
        # Both cells are their track's active slot, so the scene fans the
        # cell's own gesture out rather than reloading a clip that is already
        # in the buffer: unmute, not load.
        self.assertEqual([p for p, _ in self.osc if p.endswith("/load_loop")], [],
                         "the clip is already in the buffer")
        self.assertEqual(
            [o for o in self.osc if o[1] == ["trigger"]],
            [("/sl/0/hit", ["trigger"]), ("/sl/1/hit", ["trigger"])],
            "a scene launch of an active cell is that cell's own tap, fanned "
            "out — the same relaunch gesture, not a second way to start a clip",
        )


class SceneLaunchSurvivesTheRealDebounce(SurfaceCase):
    """Scene launch must work at the debounce the appliance actually runs.

    `MPE_APC_DEBOUNCE_MS` defaults to 200 on the Pi. The synthesised down/up
    pair lands in one microsecond, so the up — which carries the mute/unmute
    half of the gesture — was inside the window and silently dropped. Every
    other harness in this suite uses debounce_ms=0, the single value at which
    the defect cannot appear, so it shipped green.
    """

    def test_the_up_edge_is_not_swallowed_at_200ms(self) -> None:
        from tests.test_multigrid_delegates import build_test_gesture

        sink: list[tuple[str, list]] = []
        fs = build_test_gesture(0, sink, debounce_ms=200.0)
        fs.sl_state = SL_STATE_OFF
        fs.synthesised_tap()
        self.assertTrue(
            sink, "the synthesised tap produced no engine command at all"
        )

    def test_a_real_double_press_is_still_debounced(self) -> None:
        """The guard must keep rejecting hardware contact bounce."""
        from tests.test_multigrid_delegates import build_test_gesture

        sink: list[tuple[str, list]] = []
        fs = build_test_gesture(0, sink, debounce_ms=200.0)
        fs.sl_state = SL_STATE_OFF
        fs.on_pad_down()
        first = len(sink)
        fs.on_pad_down()
        self.assertEqual(
            len(sink), first, "a bouncing pad must not fire twice"
        )


class OneStateSourceTests(SurfaceCase):
    """A plan is only as good as the state it was planned against.

    `press` read the gesture's `sl_state`; `scene_press` planned from the
    surface's `_sl_states` cache and then dispatched against a third read.
    Both are fed from the same OSC message in production, so they agreed —
    which is exactly why nothing caught the three paths drifting apart in
    every other respect. These tests pin the single source.
    """

    def _playing_track_with_a_stale_cache(self) -> None:
        (self.rt.clip_path(0, 2)).write_bytes(b"\0" * 4096)
        self.rt._tracks[0] = Track(
            slots=(None, None, Slot("c.wav"), *([None] * 5)), active_slot=2
        )
        # The engine says PLAYING. The surface's own cache never heard.
        self.fs_by_loop[0].sl_state = SL_STATE_PLAYING
        self._sl_states_is_stale = True

    def test_a_scene_row_is_planned_against_the_engine_not_the_cache(self) -> None:
        self._playing_track_with_a_stale_cache()
        self.assertEqual(
            self.surface.track_state(0),
            SL_STATE_PLAYING,
            "the gesture is the source; a stale cache must not win",
        )

    def test_every_track_reports_through_the_same_source(self) -> None:
        self._playing_track_with_a_stale_cache()
        states = self.surface.track_states()
        self.assertEqual(states[0], SL_STATE_PLAYING)
        self.assertEqual(len(states), 15, "all tracks, banked or not")
        for track, state in states.items():
            self.assertEqual(state, self.surface.track_state(track))


class GridWaitLaunchTests(SurfaceCase):
    """The silent-session launch path, which had never once run.

    `poll_pending` called `self.repaint(self._sl_states)`. `repaint` is
    keyword-only and takes no positional argument, so the call raised
    TypeError the instant a queued launch came due — and the bench main loop
    has no `try`, so the process died with the load/launch OSC already sent:
    the audio moves, the pads freeze mid-blink, nothing repaints them ever.

    The unused loop variable was the tell. Nothing had executed this branch.
    `repaint()` already reads `self._sl_states` itself, so the argument was
    redundant as well as fatal.

    Canon: `Documents/specs/multi-clip-integration-plan.md` — a launch with
    nothing playing lands on the grid bar line. That cannot happen if reaching
    the bar line kills the process.
    """

    def test_a_launch_due_on_the_grid_bar_line_does_not_raise(self) -> None:
        self.rt._grid_wait[3] = 0.0  # due for any monotonic now
        self.surface.poll_pending()  # raised TypeError before this fix
        self.assertEqual(
            self.rt._grid_wait, {}, "the wait is consumed, not left to refire"
        )

    def test_nothing_due_paints_nothing(self) -> None:
        before = len(self.out.sent)
        self.surface.poll_pending()
        self.assertEqual(
            len(self.out.sent),
            before,
            "no launch came due, so the surface must not be repainted",
        )
