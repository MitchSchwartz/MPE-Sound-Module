"""Execute `slot_matrix` plans — slot files and buffer prep only.

Record/close/stop **gestures** are delegated to ``TrackGesture`` per track.
This module owns mutable slot occupancy, pending switch/stop, and the OSC that
only the matrix needs (`load_loop`, `save_loop`, `undo_all` for slot changes).
"""

from __future__ import annotations

import os
import time
from dataclasses import replace
from pathlib import Path
from typing import Callable

from slot_matrix import (
    PENDING_LAUNCH,
    ACT_CANCEL,
    ACT_CLEAR,
    ACT_FORWARD,
    ACT_LAUNCH,
    ACT_NOOP,
    ACT_RECORD,
    ACT_SWITCH,
    NUM_SLOTS,
    Slot,
    SlotPlan,
    Track,
    apply_pending,
    plan_cell_press,
    resolve_at_boundary,
)
from looper_songs import _fsync_dir, _fsync_file
from sl_loop_states import ACTIVE_PLAY, SL_STATE_OFF

# Gestures the gesture owns — runtime must not send parallel OSC for these.
# Actions the track's own gesture carries out. ACT_FORWARD is the whole
# active lane: the matrix contributes nothing to it but the binding.
GESTURE_ACTIONS = frozenset({ACT_FORWARD, ACT_RECORD})

SAVE_TIMEOUT_S = 2.0
#: `_ensure_flushed` / `poll_flush` outcomes.
FLUSH_CLEAN = "clean"
FLUSH_PENDING = "pending"
FLUSH_FAILED = "failed"

#: `_execute_slot_ops` outcomes. "waiting" is not failure: the press is parked
#: until the outgoing take reaches disk, and is replayed by `resume_awaiting`.
OP_OK = "ok"
OP_FAILED = "failed"
OP_WAITING = "waiting"
MIN_CLIP_BYTES = 512


#: What it takes to make a loop sound, whatever it was doing before.
#:
#: `mute_off` alone does NOT lift a pause. `stop_all_loops` pauses every loop
#: (`pause_on`), so after any Stop All a matrix launch that sent only
#: `mute_off` was SILENT — the clip loaded, the pads lit, and nothing played.
#:
#: The single-clip path has always known this and sends `pause_off` + `trigger`
#: (see `loop_model.plan_gesture`, STATE_STOPPED). The matrix was written as a
#: sibling of that path and never inherited the rule. This constant exists so
#: there is ONE answer to "how do you start a loop" instead of two that drift.
LAUNCH_COMMANDS: tuple[str, ...] = ("pause_off", "trigger")

#: A deferred launch waits for the track's loop wrap. If `loop_pos` stops
#: arriving the wrap never comes and the switch is stranded — a dead pad with
#: no error, which is worse than a late switch. After this many seconds without
#: a wrap the launch fires anyway and says so.
DEFERRED_LAUNCH_GRACE_S: float = 5.0


class SlotRuntime:
    """Per-track slot bookkeeping plus non-gesture engine actions."""

    def __init__(
        self,
        *,
        send: Callable[[str, list], None],
        clips_dir: Path,
        num_tracks: int,
        log: Callable[[str], None] | None = None,
        now: Callable[[], float] = time.monotonic,
        grid_boundary: Callable[[], float | None] = lambda: None,
        session_sounding: Callable[[], bool] = lambda: False,
        mark_phase_zero: Callable[[], None] = lambda: None,
    ) -> None:
        self._send = send
        #: When the next grid bar line falls, or None if there is no grid.
        #: Injected rather than reached for: the runtime needs a BOUNDARY, and
        #: has no business knowing what a GridState is.
        self._grid_boundary = grid_boundary
        #: Is ANY loop sounding right now — not just this one. The old check
        #: asked only about the pressed track, so launching a stopped track
        #: while another track played fired instantly and landed off the beat
        #: of the thing it was joining.
        self._session_sounding = session_sounding
        #: Declare the downbeat. A clip started into silence IS the downbeat.
        self._mark_phase_zero = mark_phase_zero
        self._clips_dir = Path(clips_dir)
        self._num_tracks = num_tracks
        self._save_timeout_s = SAVE_TIMEOUT_S
        self._log = log or (lambda _m: None)
        self._now = now
        self._tracks: dict[int, Track] = {i: Track() for i in range(num_tracks)}
        #: Launches held until the track's next wrap, by track index.
        #: `load_loop` swaps the buffer the instant it lands (measured
        #: 2026-08-26, PI5-LOOPER-SEAM-WRAP.md: it does NOT halt playback), so
        #: sending it at press time replaces the audio under the player's
        #: fingers mid-bar. Held here instead and fired at the boundary.
        self._deferred: dict[int, tuple[SlotPlan, float]] = {}
        #: In-flight saves: loop -> (slot, tmp path, final path, deadline).
        self._flush: dict[int, tuple[int, Path, Path, float]] = {}
        #: Presses parked until their track's save lands: loop -> (slot, hold).
        self._awaiting: dict[int, tuple[int, bool]] = {}
        #: Deferred launches that are waiting on the GRID clock rather than on
        #: a loop wrap: track -> the boundary time they are waiting for. A
        #: track that is playing gets its wrap, which is the more accurate
        #: signal; this is for when nothing is playing at all.
        self._grid_wait: dict[int, float] = {}

    def set_session_sounding(self, probe: Callable[[], bool]) -> None:
        """Tell the runtime how to ask whether ANY loop is sounding.

        Wired by `SlotSurface`, which holds every gesture, for the same reason
        the wrap callback is: production and every test harness then get it by
        construction instead of each caller remembering.
        """
        self._session_sounding = probe

    def track(self, index: int) -> Track:
        return self._tracks.get(index, Track())

    def tracks(self) -> dict[int, Track]:
        return dict(self._tracks)

    def clip_path(self, track: int, slot: int) -> Path:
        return self._clips_dir / f"live_t{track:02d}_s{slot}.wav"

    def press(
        self,
        track_index: int,
        slot: int,
        *,
        sl_state: int,
        hold: bool = False,
    ) -> SlotPlan:
        """Plan a cell press and run slot/buffer ops. Gestures are not sent here."""
        track = self.track(track_index)
        plan = plan_cell_press(
            track_index=track_index,
            track=track,
            slot=slot,
            sl_state=sl_state,
            hold=hold,
        )
        outcome = self._execute_slot_ops(plan, sl_state=sl_state)
        if outcome == OP_WAITING:
            # The outgoing take is still being written. Park the press and
            # replay it from the idle loop rather than sleeping here — the
            # buffer must not be reused before the save lands, but the surface
            # must not go deaf while it does.
            self._awaiting[track_index] = (slot, hold)
            return replace(
                plan, action=ACT_NOOP, note=f"{plan.note} — waiting for save"
            )
        self._awaiting.pop(track_index, None)
        if outcome == OP_FAILED:
            return replace(plan, action=ACT_NOOP, note=f"{plan.note} — FAILED")
        self._tracks[track_index] = apply_pending(self.track(track_index), plan)
        if plan.note:
            self._log(f"track {track_index + 1} slot {slot + 1}: {plan.note}")
        return plan

    def land_pending(self, track_index: int) -> None:
        """Carry out this track's queued action now.

        Named for what it does rather than for why it is usually called. It
        WAS `boundary()`, which stopped being true the moment `expire_deferred`
        started calling it precisely because no boundary had arrived.

        This is where a held launch actually reaches the engine. `load_loop`
        swaps the buffer immediately, so at the wrap the outgoing take has just
        finished and the swap lands in the seam instead of over the middle of
        a bar.
        """
        self._grid_wait.pop(track_index, None)
        held = self._deferred.pop(track_index, None)
        if held is not None and not self._launch(held[0]):
            # The launch failed at the boundary. Drop the pending rather than
            # advancing the model onto a slot the engine never loaded — the
            # outgoing take is still sounding and still the truth.
            self._tracks[track_index] = replace(
                self.track(track_index), pending=None
            )
            return
        self._tracks[track_index] = resolve_at_boundary(self.track(track_index))

    def dispatch(self, plan: SlotPlan, *, sl_state: int = SL_STATE_OFF) -> SlotPlan:
        """Execute a plan produced elsewhere (e.g. scene row)."""
        outcome = self._execute_slot_ops(plan, sl_state=sl_state)
        if outcome == OP_WAITING:
            self._awaiting[plan.track] = (plan.slot, False)
            return replace(
                plan, action=ACT_NOOP, note=f"{plan.note} — waiting for save"
            )
        self._awaiting.pop(plan.track, None)
        if outcome == OP_FAILED:
            failed = replace(plan, action=ACT_NOOP, note=f"{plan.note} — FAILED")
            if plan.note:
                self._log(failed.note)
            return failed
        self._tracks[plan.track] = apply_pending(self.track(plan.track), plan)
        if plan.note:
            self._log(f"track {plan.track + 1} slot {plan.slot + 1}: {plan.note}")
        return plan

    def abandon(self, track_index: int) -> None:
        """Drop every unfinished intent for one track.

        A track can be taken out from under a queued action in several ways —
        Stop All, clearing the slot, forgetting the binding — and each one used
        to leave `_deferred`, `_awaiting` and `Track.pending` behind. The
        deferred launch was the dangerous one: Stop All pauses the loop, so no
        wrap can ever arrive, and `expire_deferred` then decided none was
        coming and STARTED THE TRACK PLAYING about five seconds after the panic
        button. One place to abandon, called from all of them.
        """
        self._deferred.pop(track_index, None)
        self._grid_wait.pop(track_index, None)
        self._awaiting.pop(track_index, None)
        track = self.track(track_index)
        if track.pending is not None:
            self._tracks[track_index] = replace(track, pending=None)

    def abandon_all(self) -> None:
        """Stop All means everything, queued intent included."""
        for track_index in range(self._num_tracks):
            self.abandon(track_index)

    def awaiting_tracks(self) -> tuple[int, ...]:
        """Tracks holding a press parked behind an in-flight save."""
        return tuple(self._awaiting)

    def resume_awaiting(self, track_index: int, *, sl_state: int) -> SlotPlan | None:
        """Replay a parked press once its save resolves. None while pending.

        The press is re-planned rather than resumed mid-way: the track may have
        moved on, and re-running `plan_cell_press` against current state is the
        only way the answer stays true. A save that FAILED is not replayed into
        a proceed — `_ensure_flushed` reports failed again and the press is
        refused, which is the same protection the blocking version gave.
        """
        held = self._awaiting.get(track_index)
        if held is None:
            return None
        status = self.poll_flush(track_index)
        if status == FLUSH_PENDING:
            return None
        slot, hold = held
        self._awaiting.pop(track_index, None)
        if status == FLUSH_FAILED:
            # Refuse HERE rather than replaying the press. A replay would find
            # the slot still dirty, start a fresh save, park again, and retry
            # for ever — the take never reaching disk is exactly the state that
            # makes the replay futile. A later press by the player is a new
            # decision and does get a new attempt.
            self._log(
                f"track {track_index + 1}: REFUSING — the take on the current "
                f"slot did not reach disk, and reusing the buffer would "
                f"overwrite it"
            )
            return SlotPlan(
                action=ACT_NOOP,
                track=track_index,
                slot=slot,
                note="save did not land — FAILED",
            )
        return self.press(track_index, slot, sl_state=sl_state, hold=hold)

    def poll_grid_wait(self) -> list[int]:
        """Fire launches whose grid bar line has arrived. Returns those fired.

        The counterpart to `on_wrap` for a silent session. A playing track
        still lands on its own wrap — that is sample-accurate and this is not —
        so this only ever runs for launches that had no wrap to wait for.
        """
        now = self._now()
        due = [t for t, at in self._grid_wait.items() if now >= at]
        for track_index in due:
            self._log(
                f"track {track_index + 1}: queued launch lands on the grid bar "
                f"line (nothing was playing to sync to)"
            )
            self.land_pending(track_index)
        return due

    def has_deferred(self, track_index: int) -> bool:
        """True while a launch is held waiting for this track's wrap."""
        return track_index in self._deferred

    def reset(self) -> None:
        """Drop slot bookkeeping after a full track reset."""
        self._tracks = {i: Track() for i in range(self._num_tracks)}
        self._deferred.clear()
        self._grid_wait.clear()
        self._awaiting.clear()

    def mark_recorded(
        self, track_index: int, slot: int, *, len_s: float, sl_state: int
    ) -> None:
        """A take finished on this cell — the buffer now holds unsaved audio."""
        track = self.track(track_index)
        self._tracks[track_index] = replace(
            track.with_slot(
                slot,
                Slot(
                    file=self.clip_path(track_index, slot).name,
                    len_s=len_s,
                    sl_state=sl_state,
                    dirty=True,
                ),
            ),
            active_slot=slot,
        )

    def needs_gesture(self, plan: SlotPlan) -> bool:
        return plan.action in GESTURE_ACTIONS

    def _execute_slot_ops(self, plan: SlotPlan, *, sl_state: int) -> str:
        loop = plan.track
        if plan.action == ACT_NOOP or plan.action in GESTURE_ACTIONS:
            if plan.action == ACT_RECORD:
                return self._prepare_record(plan, sl_state=sl_state)
            if plan.action == ACT_FORWARD:
                # Bind the buffer to this slot if the track had none. No OSC:
                # the gesture owns every command in this lane, and a stray
                # send from here would double whatever it does.
                if self.track(plan.track).active_slot is None:
                    self._tracks[plan.track] = replace(
                        self.track(plan.track), active_slot=plan.slot
                    )
            return OP_OK

        if plan.action == ACT_CANCEL:
            # Cancelling a queued LAUNCH means "never mind, stay silent", and
            # pause_on is right. Cancelling a queued SWITCH means "never mind,
            # keep playing what you were playing" — the outgoing slot is still
            # sounding, and pausing it stops audio the player never asked to
            # stop. Only the launch case has anything to undo.
            self._deferred.pop(loop, None)
            self._grid_wait.pop(loop, None)
            pending = self.track(loop).pending
            if pending is None or pending.kind == PENDING_LAUNCH:
                self._send(f"/sl/{loop}/hit", ["pause_on"])
            self._awaiting.pop(loop, None)
            return OP_OK

        if plan.action == ACT_CLEAR:
            return OP_OK if self._clear(plan) else OP_FAILED

        if plan.save_first:
            status = self._ensure_flushed(loop)
            if status == FLUSH_PENDING:
                return OP_WAITING
            if status == FLUSH_FAILED:
                self._log(
                    f"track {loop + 1}: REFUSING to switch — the take on the "
                    f"current slot did not reach disk, and switching would "
                    f"overwrite the buffer holding it"
                )
                return OP_FAILED

        if plan.action in (ACT_LAUNCH, ACT_SWITCH):
            # THE QUESTION IS WHETHER THE SESSION IS SOUNDING, NOT THIS TRACK.
            #
            # It used to be `sl_state in ACTIVE_PLAY` — the pressed track only.
            # Two failures came out of that. Launching a stopped track while
            # another track played fired instantly, landing off the beat of the
            # music it was joining. And after Stop All nothing is sounding by
            # definition, so every launch fired instantly too.
            if self._session_sounding():
                return OP_OK if self._defer_launch(plan) else OP_FAILED
            # Nothing is playing anywhere. Mitch, 2026-08-30: "when I've
            # stopped all and I start a clip, we've reset the phase to zero
            # ... it should also mean that start happens immediately."
            #
            # Right, and it is not a special case — it is what a downbeat IS.
            # You cannot be late for something that has not started. Waiting
            # for the "next" bar line here would sit in silence for a full bar
            # before the first note, which is what this code did for about
            # twenty minutes after I first read the report.
            #
            # So the clip starts now AND becomes the phase reference, which is
            # what makes every later clip line up with it.
            if not self._launch(plan):
                return OP_FAILED
            self._mark_phase_zero()
            return OP_OK
        return OP_OK

    def _defer_launch(self, plan: SlotPlan) -> bool:
        """Hold a launch until the wrap. Validate NOW so failures are visible.

        A missing clip file discovered at the boundary would be a pad that
        lights, waits a bar and then quietly does nothing. Checked at press
        instead, where the press is what failed.
        """
        loop = plan.track
        # No `retrigger_only` guard. A press on the track's OWN active slot
        # plans ACT_FORWARD, never LAUNCH or SWITCH — verified across every
        # state x dirty x hold combination — so this only ever runs for a slot
        # that is not the active one, and the clip file has to be there.
        if not self.clip_path(loop, plan.slot).exists():
            self._log(f"track {loop + 1} slot {plan.slot + 1}: no clip file")
            return False
        self._deferred[loop] = (plan, self._now())
        boundary = self._grid_boundary()
        if boundary is not None:
            self._grid_wait[loop] = boundary
        return True

    def expire_deferred(self, track_index: int, *, sl_state: int) -> bool:
        """Fire a deferred launch that has waited too long for a wrap.

        Called from the surface's routine state poll, so no new ticker exists
        for this. Returns True if it fired.

        The grace timer's premise is "this track is playing but no wrap is
        reaching us". If the track is not sounding, the premise is false: there
        is no audio to protect and no wrap to wait for, and firing here starts
        a track the player has stopped. That is exactly what happened after
        Stop All. Silence is a reason to drop the launch, never to force it.
        """
        held = self._deferred.get(track_index)
        if held is None:
            return False
        if sl_state not in ACTIVE_PLAY and track_index not in self._grid_wait:
            self._log(
                f"track {track_index + 1}: dropping the queued launch — the "
                f"track is no longer playing and there is no grid, so there is "
                f"nothing to sync to"
            )
            self.abandon(track_index)
            return False
        if sl_state not in ACTIVE_PLAY:
            # Silent, but the grid knows when the bar falls. `poll_grid_wait`
            # owns this one; the playing-track grace timer must not touch it.
            return False
        plan, at = held
        if self._now() - at < DEFERRED_LAUNCH_GRACE_S:
            return False
        self._log(
            f"track {track_index + 1}: no loop wrap in "
            f"{DEFERRED_LAUNCH_GRACE_S:.0f}s — launching unquantized rather "
            f"than stranding the switch"
        )
        self.land_pending(track_index)
        return True

    def _prepare_record(self, plan: SlotPlan, *, sl_state: int) -> str:
        loop = plan.track
        track = self.track(loop)
        if (
            plan.from_slot is not None
            and plan.from_slot != plan.slot
            and (
                track.occupied(plan.from_slot)
                or track.active_slot == plan.from_slot
            )
        ):
            if sl_state in ACTIVE_PLAY:
                status = self._ensure_flushed(loop)
                if status == FLUSH_PENDING:
                    return OP_WAITING
                if status == FLUSH_FAILED:
                    return OP_FAILED
                # Deliberately NO mute_on and NO undo_all. Measured on the Pi
                # 2026-08-28: `record` over a PLAYING loop goes WAIT_START and
                # the loop KEEPS SOUNDING to the wrap, then enters RECORDING
                # there (0.303 s of continued playback against 0.307 s left in
                # the cycle). The engine already puts the stop on the same
                # boundary as the take.
                #
                # Silencing here pre-empted that: the track went quiet the
                # instant the pad was pressed and stayed quiet for up to a full
                # bar before anything replaced it. Reported as "recording a new
                # clip immediately cuts the currently playing clip".
                #
                # The flush stays — it writes the outgoing take to disk and
                # touches no audio.
                pass
            else:
                if plan.save_first:
                    status = self._ensure_flushed(loop)
                    if status == FLUSH_PENDING:
                        return OP_WAITING
                    if status == FLUSH_FAILED:
                        return OP_FAILED
                # Nothing is sounding, so clearing the stale buffer costs no
                # audio and guarantees the take starts from empty.
                self._send(f"/sl/{loop}/hit", ["undo_all"])

        # The buffer is now this slot's, so the binding moves NOW — not when
        # the take lands. `_maybe_mark_recorded` refuses to register a take on
        # a slot that already holds one, so leaving the binding on the outgoing
        # slot means the new take is never recorded anywhere: the pad stays
        # dark and the next press records over it again.
        #
        # This lives here rather than in `press()` because `dispatch()` — the
        # scene path — never ran that branch, so the same plan bound the slot
        # or not depending on which caller produced it.
        self._tracks[loop] = replace(self.track(loop), active_slot=plan.slot)
        return OP_OK

    def _launch(self, plan: SlotPlan) -> bool:
        loop = plan.track
        track = self.track(loop)
        if track.active_slot == plan.slot:
            active = track.slot(plan.slot)
            if active is not None and active.dirty:
                for cmd in LAUNCH_COMMANDS:
                    self._send(f"/sl/{loop}/hit", [cmd])
                return True
        path = self.clip_path(loop, plan.slot)
        if not path.exists():
            self._log(f"track {loop + 1} slot {plan.slot + 1}: no clip file")
            return False
        # Three arguments, not one. SooperLooper's handler is registered as
        # `s:filename s:return_url s:error_path`; a one-argument message does
        # not match the signature and is DISCARDED without a reply or an error.
        # That is why a queued switch moved the binding but never the audio:
        # the model advanced, `boundary()` resolved, both pads repainted, and
        # the engine had simply never been told. Every other load_loop in this
        # repo (looper_songs.py, both spikes) already sends the empty reply
        # paths — this call site was the only one that did not.
        self._send(f"/sl/{loop}/load_loop", [str(path), "", ""])
        for cmd in LAUNCH_COMMANDS:
            self._send(f"/sl/{loop}/hit", [cmd])
        return True

    def _clear(self, plan: SlotPlan) -> bool:
        loop, slot = plan.track, plan.slot
        self.abandon(loop)
        track = self.track(loop)
        if track.active_slot == slot:
            self._send(f"/sl/{loop}/hit", ["undo_all"])
        self.clip_path(loop, slot).unlink(missing_ok=True)
        cleared = track.with_slot(slot, None)
        if cleared.active_slot == slot:
            cleared = replace(cleared, active_slot=None)
        self._tracks[loop] = cleared
        return True

    def forget_active_slot(self, loop: int) -> bool:
        """Drop the active slot's file and binding, touching nothing else.

        The engine half of a long-press-to-clear belongs to `TrackGesture`,
        which already sent `undo_all` — sending it again from here would be the
        second opinion this design removed. What the gesture cannot do is
        delete the WAV: it has never heard of slot files. So the split is
        exact — the gesture owns the engine and the LED, the matrix owns the
        disk and the binding.
        """
        track = self.track(loop)
        slot = track.active_slot
        if slot is None:
            return False
        self.clip_path(loop, slot).unlink(missing_ok=True)
        self._tracks[loop] = replace(track.with_slot(slot, None), active_slot=None)
        # The clip this track was going to switch to may be the one just
        # deleted, and a parked press is waiting on a buffer that no longer
        # belongs to anything.
        self.abandon(loop)
        return True

    # -- saving a take to disk ------------------------------------------
    #
    # This used to be one synchronous method with `time.sleep` in a loop,
    # called straight from a pad press. On the failure path it froze the whole
    # input/LED loop for the full timeout — no pad reads, no LED updates, on an
    # instrument. Split into begin/poll so the wait happens in the idle loop
    # while the surface stays responsive.
    #
    # The SAFETY RULE is unchanged and is the reason for the split rather than
    # a shortcut around it: a buffer is never reused until its take is on disk.
    # Callers get "pending" and come back; they never get to proceed early.

    def _ensure_flushed(self, loop: int) -> str:
        """clean | pending | failed — start the save if needed, never block."""
        track = self.track(loop)
        if track.active_slot is None:
            return FLUSH_CLEAN
        active = track.slot(track.active_slot)
        if active is None or not active.dirty:
            return FLUSH_CLEAN
        if loop not in self._flush:
            self._begin_flush(loop, track.active_slot)
        return self.poll_flush(loop)

    def _begin_flush(self, loop: int, slot: int) -> None:
        path = self.clip_path(loop, slot)
        path.parent.mkdir(parents=True, exist_ok=True)

        # Save to a sibling temp file and rename over the original only once a
        # complete file exists. This used to unlink `path` first and ask the
        # engine to write it — so a save that never landed destroyed the take
        # it was trying to preserve, and the caller then "refused to switch" to
        # protect a clip it had already deleted. Reported from the appliance
        # 2026-08-27 as "when I record clip 2, clip 1 is deleted".
        #
        # The unlink was not gratuitous: SooperLooper will not overwrite an
        # existing file. A fresh temp path satisfies that without ever putting
        # the recorded take at risk.
        tmp = path.with_name(path.name + ".part")
        tmp.unlink(missing_ok=True)
        self._send(f"/sl/{loop}/save_loop", [str(tmp), "", "", "", ""])
        self._flush[loop] = (slot, tmp, path, self._now() + self._save_timeout_s)

    def poll_flush(self, loop: int) -> str:
        """clean | pending | failed. Cheap enough for the idle loop."""
        job = self._flush.get(loop)
        if job is None:
            return FLUSH_CLEAN
        slot, tmp, path, deadline = job
        try:
            if tmp.stat().st_size >= MIN_CLIP_BYTES:
                # Durable before it is authoritative: fsync the data, then
                # rename, then fsync the directory. A rename that reaches the
                # SD card before the bytes do leaves a manifest naming a
                # truncated file after a power cut.
                _fsync_file(tmp)
                os.replace(tmp, path)
                _fsync_dir(path.parent)
                track = self.track(loop)
                active = track.slot(slot)
                if active is not None:
                    self._tracks[loop] = track.with_slot(
                        slot, replace(active, dirty=False)
                    )
                self._flush.pop(loop, None)
                return FLUSH_CLEAN
        except OSError:
            pass
        if self._now() < deadline:
            return FLUSH_PENDING

        # Nothing usable arrived. Leave the original exactly as it was, drop
        # the partial, and stay dirty so the surface keeps telling the truth.
        self._flush.pop(loop, None)
        tmp.unlink(missing_ok=True)
        self._log(
            f"track {loop + 1} slot {slot + 1}: save did not land "
            f"in {self._save_timeout_s:.1f}s — the take is still only in the "
            f"engine buffer, and the clip already on disk is untouched"
        )
        return FLUSH_FAILED
