"""The matrix as one object the bench can delegate to.

Slot **occupancy** and switch/stop pending live in ``SlotRuntime``. Record,
close, stop, ring-out, quantize, and active-slot LED timing live in the
existing ``TrackGesture`` for each track — one gesture brain per column.
"""

from __future__ import annotations

import time
from typing import Callable

from apc_grid import GRID_ROWS, GridView
from apc_transport import scene_launch_index_to_row
from led_compositor import LAYER_HOLD, LAYER_SURFACE
from led_table import LED_OFF, LED_RED
from sl_loop_states import (
    ACTIVE_PLAY,
    ACTIVE_RECORD,
    SL_STATE_MUTE,
    SL_STATE_OFF,
    SL_STATE_OVERDUBBING,
    SL_STATE_PAUSED,
    SL_STATE_PLAYING,
    SL_STATE_RECORDING,
)
from slot_leds import matrix_colours
from slot_matrix import (
    ACT_NOOP,
    ACT_RECORD,
    PENDING_LAUNCH,
    PENDING_SWITCH,
    SlotPlan,
    plan_scene_press,
    scene_row_led,
)
from slot_runtime import SlotRuntime

if False:  # TYPE_CHECKING — avoid import cycle at runtime
    from track_gesture import TrackGesture

MIN_TAKE_LEN_S = 0.01
SILENT = (SL_STATE_MUTE, SL_STATE_PAUSED, SL_STATE_OFF)


class SlotSurface:
    def __init__(
        self,
        *,
        runtime: SlotRuntime,
        gestures_by_loop: dict[int, "TrackGesture"],
        view: GridView,
        compositor,
        num_tracks: int,
        scene_launch_notes: tuple[int, ...] = (),
        hold_s: float = 2.0,
        hold_blink_start_s: float = 0.5,
        log: Callable[[str], None] | None = None,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._rt = runtime
        self._fs = gestures_by_loop
        self._view = view
        # The surface submits desired state and never sends a byte. Its two
        # private records of what it had painted — `_painted` and
        # `_scene_painted` — are gone with the writes: two of the four caches
        # that let `clear_unwired_surfaces` erase this matrix permanently.
        self._compositor = compositor
        self._num_tracks = num_tracks
        self._scene_launch_notes = scene_launch_notes
        self._hold_s = max(hold_s, 0.001)
        self._hold_blink_start_s = max(hold_blink_start_s, 0.0)
        self._log = log or (lambda _m: None)
        self._now = now
        # The wrap is the only boundary the surface has. Wired here rather
        # than where the gestures are built so that production and every test
        # harness get it by construction — the earlier resolution hung off
        # `sl_state in ACTIVE_PLAY`, which is true the instant a switch is
        # pressed and is why switches fired mid-bar.
        for loop, fs in self._fs.items():
            fs.set_wrap_callback(lambda i=loop: self.on_wrap(i))
        # Whether the SESSION is sounding, which is what decides if a launch
        # waits for a boundary. The runtime used to ask only about the pressed
        # track, so a stopped track launched while other tracks played fired
        # instantly and landed off their beat.
        self._rt.set_session_sounding(
            lambda: any(g.sl_state in ACTIVE_PLAY for g in self._fs.values())
        )
        self._sl_states: dict[int, int] = {}
        self._loop_lens: dict[int, float] = {}
        self._pad_down_note: int | None = None
        self._pad_down_at: float | None = None
        self._hold_fired = False
        #: Last (track, active, loop_len) we declined to register, so the
        #: explanation is logged once per transition rather than every poll.
        self._declined: tuple | None = None

    # -- input ------------------------------------------------------------

    def handles(self, note: int) -> bool:
        return self._view.cell_for_note(note) is not None

    def _acts_on_release(self, note: int) -> bool:
        """Does this pad wait for the finger to lift?

        An occupied slot that is NOT the track's active one does. Its press
        means launch-or-switch, and its long press means delete — so acting on
        pad-down loaded and played the clip before the hold could fire, and the
        player watched the take they were deleting start up first. The
        gesture already lands mute and launch on release for the same
        reason; only record has to be immediate.
        """
        cell = self._view.cell_for_note(note)
        if cell is None or self._is_active_lane(note):
            return False
        track, slot = cell
        return self._rt.track(track).occupied(slot)

    def note_down(self, note: int) -> bool:
        if not self.handles(note):
            return False
        self._pad_down_note = note
        self._pad_down_at = self._now()
        self._hold_fired = False
        if not self._acts_on_release(note):
            self.press(note)
        return True

    def note_up(self, note: int) -> bool:
        if note != self._pad_down_note:
            return False
        if not self._hold_fired and self._acts_on_release(note):
            self.press(note)
        cell = self._view.cell_for_note(note)
        if cell is not None:
            track, _slot = cell
            fs = self._fs.get(track)
            if fs is not None:
                fs.on_pad_up()
        self._pad_down_note = None
        self._pad_down_at = None
        self.poll_hold_led()
        self.repaint()
        return True

    def press(self, note: int, *, hold: bool = False) -> bool:
        cell = self._view.cell_for_note(note)
        if cell is None:
            return False
        track, slot = cell
        fs = self._fs.get(track)
        if fs is None:
            return False
        plan = self._rt.press(
            track,
            slot,
            sl_state=self.track_state(track),
            hold=hold,
        )
        if plan.action == ACT_NOOP and plan.note:
            self._log(f"track {track + 1} slot {slot + 1}: {plan.note}")
        # A real pad press: the down edge only. The up arrives as its own MIDI
        # event, which is what makes hold-to-clear possible.
        self._hand_to_gesture(plan, note=note, tap=False)
        self._sync_gesture_notes()
        self.repaint()
        self.repaint_scenes()
        return True

    def scene_press(self, row: int) -> None:
        if not 0 <= row < GRID_ROWS:
            return
        # Planned against the same state the execution will use. This read
        # `self._sl_states` while `dispatch` read the gesture — so a scene row
        # could be planned from one view of the engine and carried out against
        # another, and `row_is_fully_playing` decides launch-vs-stop for the
        # WHOLE row from it.
        plans = plan_scene_press(
            self._rt.tracks(), row, sl_states=self.track_states()
        )
        for plan in plans:
            executed = self._rt.dispatch(
                plan, sl_state=self.track_state(plan.track)
            )
            # A scene press is a complete tap: there is no pad to release.
            self._hand_to_gesture(executed, tap=True)
        if plans:
            self._log(f"scene row {row + 1}: {len(plans)} track(s)")
        self._sync_gesture_notes()
        self.repaint()
        self.repaint_scenes()

    def on_stop_all(self) -> None:
        """Stop All was pressed. Queued intent dies with the audio.

        `stop_all_loops` talks to the gestures and the engine; it has never
        heard of `SlotRuntime`, so a deferred launch used to survive the panic
        button — and since Stop All pauses the loop, no wrap could ever arrive
        to consume it. `expire_deferred` then concluded no wrap was coming and
        started the track playing again about five seconds later.
        """
        self._rt.abandon_all()
        self._sync_gesture_notes()
        self.repaint()
        self.repaint_scenes()

    def track_states(self) -> dict[int, int]:
        """Every track's state, from the one source. See `track_state`."""
        return {i: self.track_state(i) for i in range(self._num_tracks)}

    def track_state(self, track: int) -> int:
        """The ONE answer to "what is this track's engine state".

        `press` read `fs.sl_state` while `scene_press` and `dispatch` read the
        surface's own `_sl_states` cache. Both are fed from the same OSC
        message (sl_bench_listener updates the gesture, then the surface), so
        they agreed in practice — but two sources for one fact is how the
        paths drifted apart in every other respect, and a plan is only as good
        as the state it was planned against.
        """
        fs = self._fs.get(track)
        if fs is not None:
            return fs.sl_state
        return self._sl_states.get(track, SL_STATE_OFF)

    def _hand_to_gesture(
        self, plan: SlotPlan, *, note: int | None = None, tap: bool
    ) -> None:
        """The single handoff from a plan to the track's gesture.

        This was written out three times — `press`, `dispatch` via
        `scene_press`, and the scene path's own inline copy — and the copies
        had drifted: only `press` called `expect_cleared()`, so a scene press
        that started a take left the gesture deriving `playing` from the last
        engine report and sending a mute instead of a record. Collapsing them
        is the point; `tap` is the ONLY difference that is real.
        """
        if not self._rt.needs_gesture(plan):
            return
        fs = self._fs.get(plan.track)
        if fs is None:
            return
        if note is None:
            note = self._view.note_for_cell(plan.track, plan.slot)
        if note is None:
            # The track is banked away. That is a display fact, not a reason to
            # drop the action: a gesture exists for EVERY track, and the
            # runtime has already moved the binding by the time we get here, so
            # returning silently left the model ahead of the engine. The pad
            # binding comes back on the next bank change.
            self._log(
                f"track {plan.track + 1} slot {plan.slot + 1}: acting off-view"
            )
        fs.set_note(note)
        if plan.action == ACT_RECORD:
            # The runtime just emptied this track's buffer for a take on a
            # different slot. Tell the gesture, or it derives `playing` from
            # the last engine report and its gesture is a mute, not a record.
            fs.expect_cleared()
        if tap:
            # There is no pad to release, so the up has to be synthesised:
            # leaving the gesture held would let its hold timer expire and fire
            # long-press-to-clear on every track in the row, and the
            # mute/unmute half of the gesture lands on the up.
            #
            # It must NOT go through the debounce gate. Calling on_pad_down()
            # then on_pad_up() put both edges in the same microsecond, inside
            # the appliance's real 200 ms window (MPE_APC_DEBOUNCE_MS), so the
            # up was discarded and a scene launch of a stored, muted clip did
            # nothing at all. Every harness used debounce_ms=0 — the one value
            # at which that bug is invisible.
            fs.synthesised_tap()
        else:
            fs.on_pad_down()

    def _is_active_lane(self, note: int) -> bool:
        """Is this pad the track's bound buffer (or a track with none)?

        The lane where the gesture decides everything. Kept as one predicate
        so hold, press and LED routing cannot disagree about which lane a pad
        is in — three separate answers to that question is how the surface
        ended up with two hold implementations.
        """
        cell = self._view.cell_for_note(note)
        if cell is None:
            return False
        track, slot = cell
        tr = self._rt.track(track)
        # Must agree with plan_cell_press exactly. An OCCUPIED slot on an
        # unbound track is NOT the lane — the matrix launches it from disk —
        # so routing its hold or its LED to the gesture would have the
        # surface and the planner disagreeing about who owns the same pad.
        return tr.active_slot == slot or (
            tr.active_slot is None and not tr.occupied(slot)
        )

    def poll_hold(self) -> None:
        if self._pad_down_note is None or self._hold_fired or self._pad_down_at is None:
            return
        # Active lane: the gesture owns hold-to-clear, blink and all. Its
        # own poll_hold is skipped under multigrid (see poll_track_gestures), so
        # drive it here rather than reimplementing the timing — a second hold
        # implementation fired at a different moment and painted a different
        # blink, which is what the equivalence test caught.
        if self._is_active_lane(self._pad_down_note):
            cell = self._view.cell_for_note(self._pad_down_note)
            fs = self._fs.get(cell[0]) if cell else None
            if fs is not None:
                fs.poll_hold()
                if fs.hold_fired:
                    # The gesture cleared the engine. The clip on disk is
                    # ours, and nothing else will remove it.
                    self._rt.forget_active_slot(cell[0])
                    self._hold_fired = True
                    self._pad_down_note = None
                    self._pad_down_at = None
                    self.poll_hold_led()
                    self.repaint()
            return
        if (self._now() - self._pad_down_at) < self._hold_s:
            return
        note = self._pad_down_note
        self._hold_fired = True
        self._pad_down_note = None
        self._pad_down_at = None
        self.poll_hold_led()
        self.press(note, hold=True)

    def poll_hold_led(self) -> None:
        """Submit the delete-hold warning for the pad under the finger.

        It used to write straight to the wire with no record of what it had
        sent: measured at **87,174 messages to one note in 0.30 s**, against a
        pacing budget of ~666/s, so a 1.5 s hold spent most of the LED
        bandwidth re-asserting two velocities and every other repaint queued
        behind it. The blink itself changes at 2 Hz. Going through the
        compositor makes the write rate equal the blink rate by construction —
        there is nothing here to remember to do.

        `replace`, not `submit`: the warning follows the finger, so the pad it
        has left must lose the opinion in the same call. That is what stops the
        old pad blinking on after the player moves.
        """
        self._compositor.replace(LAYER_HOLD, self._hold_warning())

    def _hold_warning(self) -> dict[int, int]:
        """The hold layer's whole opinion — empty when nothing is held."""
        note = self._pad_down_note
        if note is None or self._hold_fired or self._pad_down_at is None:
            return {}
        # Active lane: the gesture's own hold blink reaches the surface
        # through current_led(), so painting here as well would fight it.
        if self._is_active_lane(note):
            return {}
        elapsed = self._now() - self._pad_down_at
        if elapsed < self._hold_blink_start_s:
            return {}
        return {note: LED_RED if int(elapsed * 4) % 2 == 0 else LED_OFF}

    def poll_pending(self) -> None:
        """Resolve queued actions from state we already hold.

        `_maybe_resolve` used to run only from `on_state`, i.e. only when the
        engine reported a CHANGE. A switch between two clips on a playing track
        has no change to report — the loop is Playing before and Playing after
        — so the resolution waited for a callback that was never coming, and
        both pads blinked for ever while the audio had in fact already moved.
        Reported 2026-08-27: "I see the appropriate flashing on each clip, but
        it never actually switches."

        Same lesson the gesture LEDs learned earlier: act on what is true,
        not on the arrival of a message saying it changed.
        """
        # Launches waiting on the grid rather than on a wrap. Nothing is
        # playing in that case, so no wrap callback is ever coming — this is
        # the only thing that will fire them.
        touched = bool(self._rt.poll_grid_wait())

        for track_index in self._rt.awaiting_tracks():
            # A press parked behind an in-flight save. The save used to be
            # waited out inside the press itself, with `time.sleep` in a loop —
            # up to the full timeout with no pad reads and no LED updates, on
            # an instrument. It is polled here instead; the buffer is still
            # never reused before the take is on disk.
            plan = self._rt.resume_awaiting(
                track_index, sl_state=self.track_state(track_index)
            )
            if plan is not None:
                touched = True
                # A complete tap. The real pad's up edge is long gone — it was
                # consumed when the press was first made and parked — so
                # replaying only the down would latch the gesture held, blink
                # the hold warning and eventually fire long-press-to-clear.
                self._hand_to_gesture(plan, tap=True)
                if plan.note:
                    self._log(f"track {track_index + 1}: {plan.note}")
        for track_index, track in self._rt.tracks().items():
            if track.pending is None:
                continue
            touched = True
            self._maybe_resolve(track_index, self.track_state(track_index))
        if touched:
            # Whatever just happened moved a clip, so the scene column can have
            # changed too. This is the ONE path that mutates the runtime
            # without an engine message behind it, and it is why the bench used
            # to re-derive all eight scene buttons on every idle iteration —
            # 485 times a second, over fifteen tracks each, to discover
            # nothing had moved. Saying so here costs one boolean.
            self.repaint()
            self.repaint_scenes()

    def poll_led_repaint(self) -> None:
        """Advance gesture blink phase and repaint if needed."""
        self.poll_pending()
        self.repaint()

    # -- engine feedback --------------------------------------------------

    def on_state(self, track: int, sl_state: int) -> None:
        self._sl_states[track] = int(sl_state)
        self._maybe_mark_recorded(track, int(sl_state))
        self._maybe_resolve(track, int(sl_state))
        self._sync_gesture_notes()
        self.repaint()
        self.repaint_scenes()

    def on_loop_len(self, track: int, loop_len: float) -> None:
        if loop_len > 0:
            self._loop_lens[track] = float(loop_len)
        sl_state = self._sl_states.get(track, SL_STATE_OFF)
        if sl_state in ACTIVE_PLAY:
            self._maybe_mark_recorded(track, sl_state)
        self.repaint()
        self.repaint_scenes()

    def _maybe_mark_recorded(self, track: int, sl_state: int) -> None:
        # ACTIVE_PLAY, not PLAYING. Closing a take drops the loop straight into
        # the ring-out overdub, which runs for a whole pass — seconds at any
        # usable tempo. Waiting for plain PLAYING to admit the take exists left
        # that entire window with a recording in the buffer that nothing knew
        # about: `_flush_active` found no slot to save and skipped silently,
        # and the `undo_all` that arms the next take destroyed it with no way
        # back. Reported 2026-08-27 as "the clip gets deleted", cause guessed
        # correctly as "the tail or overdub phase is still happening".
        #
        # The audio is real the moment the take closes. That is when it gets
        # written down.
        if sl_state not in ACTIVE_PLAY:
            return
        row = self._rt.track(track)
        active = row.active_slot
        loop_len = self._loop_lens.get(track, 0.0)
        if (
            active is not None
            and not row.occupied(active)
            and loop_len >= MIN_TAKE_LEN_S
        ):
            self._rt.mark_recorded(
                track, active, len_s=loop_len, sl_state=sl_state
            )
            self._log(
                f"track {track + 1} slot {active + 1}: take landed "
                f"({loop_len:.2f}s)"
            )
            return

        # Declining used to be silent, and its symptom on the surface — a pad
        # that stays dark and records again when pressed — looks identical to
        # "you never recorded anything". Say which of the three reasons it was,
        # once per transition into PLAYING, so one reproduction is enough to
        # tell them apart.
        if self._declined == (track, active, round(loop_len, 3)):
            return
        self._declined = (track, active, round(loop_len, 3))
        if active is None:
            why = "no slot is bound to this track's buffer"
        elif row.occupied(active):
            why = (
                f"slot {active + 1} already holds a take, so the buffer's "
                f"binding never moved to the new slot"
            )
        else:
            why = f"loop_len {loop_len:.3f}s is below the {MIN_TAKE_LEN_S}s floor"
        self._log(
            f"track {track + 1}: engine reached PLAYING but the take was NOT "
            f"registered — {why}. The pad will read empty and the next press "
            f"will record again."
        )

    def _sync_gesture_notes(self) -> None:
        for track_index in self._view.visible_loops():
            fs = self._fs.get(track_index)
            if fs is None:
                continue
            active = self._rt.track(track_index).active_slot
            if active is None:
                fs.set_note(None)
            else:
                fs.set_note(self._view.note_for_cell(track_index, active))

    def on_wrap(self, track_index: int) -> None:
        """This track's loop just wrapped — the one boundary in the bench.

        Fed by `TrackGesture`'s wrap detector, the same one that ends the
        ring-out overdub, so a queued switch and the ring-out cannot disagree
        about where the bar line is.
        """
        if self._rt.track(track_index).pending is None:
            return
        self._rt.land_pending(track_index)
        self._sync_gesture_notes()
        self.repaint()
        self.repaint_scenes()

    def _maybe_resolve(self, track_index: int, sl_state: int) -> None:
        track = self._rt.track(track_index)
        pending = track.pending
        if pending is None:
            return
        if pending.kind in (PENDING_LAUNCH, PENDING_SWITCH):
            if self._rt.has_deferred(track_index):
                # A launch waiting for the wrap must NOT be resolved by
                # `sl_state in ACTIVE_PLAY`. On a switch the outgoing take is
                # already playing, so that test was true the instant the pad
                # went down — which is how a queued switch fired mid-bar.
                # `on_wrap` is the boundary. This is only the safety net for a
                # track whose `loop_pos` has stopped arriving, where no wrap is
                # coming at all.
                self._rt.expire_deferred(track_index, sl_state=sl_state)
                return
            # Nothing was held: the launch went out at press because the track
            # was silent. Reaching ACTIVE_PLAY is then a real confirmation,
            # not a guess, and it is what moves the binding.
            if sl_state in ACTIVE_PLAY:
                self._rt.land_pending(track_index)

    def reset(self) -> None:
        self._rt.reset()
        self._sl_states.clear()
        self._loop_lens.clear()
        self._pad_down_note = None
        self._pad_down_at = None
        self._hold_fired = False
        self.poll_hold_led()
        # No `blank()` any more. The runtime is empty, so every cell in the
        # desired map is already OFF and the compositor sends exactly the pads
        # that were lit. `blank()` existed to overwrite pads the diff cache
        # thought were still painted, which is a question that no longer has a
        # second answer.
        self.repaint()
        self.repaint_scenes()

    # -- output -----------------------------------------------------------

    def set_view(self, view: GridView) -> None:
        self._view = view
        self._sync_gesture_notes()
        # No force. The notes now address different tracks, so the desired map
        # has genuinely changed and the one diff at the wire sees it. `force=`
        # was here because the diff was against this object's own record of
        # what it had painted, which a bank change invalidated.
        self.repaint()
        self.repaint_scenes()

    def _gesture_leds(self) -> dict[int, int]:
        out: dict[int, int] = {}
        for track_index in self._view.visible_loops():
            fs = self._fs.get(track_index)
            if fs is not None:
                out[track_index] = fs.current_led()
        return out

    def repaint(self) -> None:
        """Submit the matrix. Nothing is sent unless a pad actually changed.

        There is no `force=`. What it meant was "the device is not what I think
        it is", which was never this object's question to answer: it is the
        compositor's `invalidate()`, called once, by whoever learned the device
        came back dark.
        """
        self._compositor.submit(LAYER_SURFACE, matrix_colours(
            self._view,
            self._rt.tracks(),
            gesture_leds=self._gesture_leds(),
        ))

    def repaint_scenes(self) -> None:
        """Submit the scene column — the eight buttons beside the eight rows."""
        if not self._scene_launch_notes:
            return
        # Hoisted. `SlotRuntime.tracks()` copies the whole track dict, and this
        # called it once per button — eight copies of fifteen tracks, at the
        # bench's ~485 Hz poll rate, to answer a question about the same
        # tracks eight times.
        tracks = self._rt.tracks()
        self._compositor.submit(LAYER_SURFACE, {
            note: scene_row_led(
                tracks, scene_launch_index_to_row(index), sl_states=self._sl_states
            )
            for index, note in enumerate(self._scene_launch_notes)
        })
