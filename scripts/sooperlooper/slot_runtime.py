"""Execute `slot_matrix` plans against the engine and the song files.

`slot_matrix` decides *what* a cell press means, as pure functions over frozen
values. This module is the only place that turns those decisions into OSC and
file I/O, and the only place that holds mutable per-track state.

The split matters because the interesting failures here are ordering failures,
and ordering is what a pure planner cannot express:

  * A **switch** reuses the track's single SooperLooper buffer. The outgoing
    clip's audio is gone the moment the incoming one loads, so a dirty buffer
    must reach its file *first* — `SlotPlan.save_first` says when, this module
    does it, and refuses the switch if the save fails rather than losing a take
    silently.
  * A **load** must not be heard before the boundary. SP1 measured load p95 at
    7.1 ms against a 2000 ms bar, so the buffer is prepared early and the
    *unmute* is what the engine defers — the same mechanism the single-clip
    launch already uses, because `trigger` does not lift a pause.

State that is not derivable from the engine lives here: which slot is active
per track, which slots hold audio, and what is queued. The engine knows only
about the one buffer per track it is playing.
"""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path
from typing import Callable

from gesture_engine import plan_arm_record, plan_close_take
from slot_matrix import (
    ACT_CANCEL,
    ACT_CLEAR,
    ACT_CLOSE,
    ACT_LAUNCH,
    ACT_NOOP,
    ACT_RECORD,
    ACT_STOP,
    ACT_SWITCH,
    NUM_SLOTS,
    PHASE_ARMING,
    PHASE_CLOSING,
    PHASE_IDLE,
    PHASE_RECORDING,
    Slot,
    SlotPlan,
    Track,
    apply_pending,
    plan_cell_press,
    resolve_at_boundary,
)
from sl_loop_states import (
    ACTIVE_PLAY,
    ACTIVE_RECORD,
    SL_STATE_OFF,
    SL_STATE_OVERDUBBING,
    SL_STATE_PLAYING,
    SL_STATE_RECORDING,
)
from sl_grid_sync import detect_loop_wrap

MIN_TAKE_LEN_S = 0.01

# How long to wait for save_loop to produce a usable file before abandoning a
# switch. Generous against the 2.3 ms p95 measured in SP1 — this budget is not
# about speed, it is about never continuing on a save that did not happen.
SAVE_TIMEOUT_S = 2.0
SAVE_POLL_S = 0.01

# Below this a "saved" WAV is a header and nothing else. Matches
# looper_songs.MIN_LOOP_WAV_BYTES; duplicated as a default rather than imported
# so this module has no dependency on the song layer.
MIN_CLIP_BYTES = 512


class SlotRuntime:
    """Per-track slot state plus the engine actions that change it."""

    def __init__(
        self,
        *,
        send: Callable[[str, list], None],
        clips_dir: Path,
        num_tracks: int,
        log: Callable[[str], None] | None = None,
        now: Callable[[], float] = time.monotonic,
        grid=None,
        quantized: bool = True,
    ) -> None:
        self._send = send
        self._clips_dir = Path(clips_dir)
        self._num_tracks = num_tracks
        self._log = log or (lambda _m: None)
        self._now = now
        self._grid = grid
        self._quantized = quantized
        self._tracks: dict[int, Track] = {i: Track() for i in range(num_tracks)}
        self._loop_lens: dict[int, float] = {}
        self._last_sl_state: dict[int, int] = {}
        self._phase: dict[int, str] = {i: PHASE_IDLE for i in range(num_tracks)}
        self._stop_queued: dict[int, bool] = {}
        self._overdub_pass: dict[int, bool] = {}
        self._loop_pos: dict[int, float] = {}
        self._loop_pos_seen: dict[int, bool] = {}
        self._last_loop_pos: dict[int, float] = {}

    # -- state ------------------------------------------------------------

    def track(self, index: int) -> Track:
        return self._tracks.get(index, Track())

    def tracks(self) -> dict[int, Track]:
        return dict(self._tracks)

    def record_phase(self, track_index: int) -> str:
        return self._phase.get(track_index, PHASE_IDLE)

    def clip_path(self, track: int, slot: int) -> Path:
        return self._clips_dir / f"live_t{track:02d}_s{slot}.wav"

    def _grid_established(self) -> bool:
        return self._grid is None or self._grid.established

    def _is_defining(self, track_index: int) -> bool:
        return self._grid is not None and self._grid.is_pending(track_index)

    # -- the one entry point ----------------------------------------------

    def press(self, track_index: int, slot: int, *, sl_state: int,
              hold: bool = False) -> SlotPlan:
        """Plan a cell press, execute it, and record the result.

        Returns the plan actually carried out. A plan that fails to execute is
        returned with its action replaced by ACT_NOOP, so a caller painting
        LEDs from the result cannot show a switch that did not happen.
        """
        track = self.track(track_index)
        plan = plan_cell_press(
            track_index=track_index,
            track=track,
            slot=slot,
            sl_state=sl_state,
            hold=hold,
            record_phase=self.record_phase(track_index),
        )
        if not self._execute(plan, sl_state=sl_state):
            return replace(plan, action=ACT_NOOP, note=f"{plan.note} — FAILED")
        self._tracks[track_index] = apply_pending(self.track(track_index), plan)
        if plan.action == ACT_RECORD:
            self._tracks[track_index] = replace(
                self.track(track_index), active_slot=plan.slot
            )
            self._phase[track_index] = PHASE_ARMING
        elif plan.action == ACT_CLOSE:
            self._phase[track_index] = PHASE_CLOSING
        if plan.note:
            self._log(f"track {track_index + 1} slot {slot + 1}: {plan.note}")
        return plan

    def boundary(self, track_index: int) -> None:
        """A quantize boundary arrived for this track: pending becomes true."""
        self._tracks[track_index] = resolve_at_boundary(self.track(track_index))

    def dispatch(self, plan: SlotPlan) -> SlotPlan:
        """Execute a plan produced elsewhere (e.g. scene row) and record it."""
        sl_state = self._last_sl_state.get(plan.track, SL_STATE_OFF)
        if not self._execute(plan, sl_state=sl_state):
            failed = replace(plan, action=ACT_NOOP, note=f"{plan.note} — FAILED")
            if plan.note:
                self._log(failed.note)
            return failed
        self._tracks[plan.track] = apply_pending(self.track(plan.track), plan)
        if plan.note:
            self._log(f"track {plan.track + 1} slot {plan.slot + 1}: {plan.note}")
        return plan

    def reset(self) -> None:
        """Drop slot bookkeeping after a full track reset."""
        self._tracks = {i: Track() for i in range(self._num_tracks)}
        self._loop_lens.clear()
        self._last_sl_state.clear()
        self._phase = {i: PHASE_IDLE for i in range(self._num_tracks)}
        self._stop_queued.clear()
        self._overdub_pass.clear()
        self._loop_pos.clear()
        self._loop_pos_seen.clear()
        self._last_loop_pos.clear()

    def on_loop_pos(self, track_index: int, loop_pos: float) -> bool:
        """Track loop position; end ring-out overdub at wrap. True if model moved."""
        pos = float(loop_pos)
        loop_len = self._loop_lens.get(track_index, 0.0)
        if (
            self._overdub_pass.get(track_index)
            and self._loop_pos_seen.get(track_index)
            and detect_loop_wrap(self._loop_pos.get(track_index, pos), pos, loop_len)
        ):
            self._overdub_pass[track_index] = False
            self._send(f"/sl/{track_index}/hit", ["overdub"])
            self._log(f"track {track_index + 1}: overdub off at wrap — one pass of ring-out")
            return True
        if self._loop_pos_seen.get(track_index) and detect_loop_wrap(
            self._last_loop_pos.get(track_index, pos), pos, loop_len
        ):
            self._overdub_pass[track_index] = False
        prev = self._loop_pos.get(track_index, pos)
        self._last_loop_pos[track_index] = prev if self._loop_pos_seen.get(track_index) else pos
        self._loop_pos[track_index] = pos
        self._loop_pos_seen[track_index] = True
        return False

    def sync_engine(
        self, track_index: int, *, sl_state: int, loop_len: float
    ) -> bool:
        """Reconcile matrix occupancy and recording phase with engine reports."""
        prev_sl = self._last_sl_state.get(track_index, SL_STATE_OFF)
        self._last_sl_state[track_index] = int(sl_state)
        if loop_len > 0:
            self._loop_lens[track_index] = float(loop_len)

        changed = False

        if sl_state == SL_STATE_RECORDING and self._stop_queued.get(track_index):
            self._stop_queued[track_index] = False
            self._send(f"/sl/{track_index}/hit", ["record"])
            self._phase[track_index] = PHASE_CLOSING
            changed = True

        phase = self._phase.get(track_index, PHASE_IDLE)
        track = self.track(track_index)
        active = track.active_slot

        if sl_state in ACTIVE_RECORD:
            phase = (
                PHASE_RECORDING
                if sl_state == SL_STATE_RECORDING
                else PHASE_ARMING
            )
        elif sl_state == SL_STATE_OVERDUBBING:
            phase = PHASE_CLOSING
            self._overdub_pass[track_index] = True
        elif sl_state == SL_STATE_PLAYING:
            if (
                phase in (PHASE_CLOSING, PHASE_RECORDING, PHASE_ARMING)
                and active is not None
                and not track.occupied(active)
                and loop_len >= MIN_TAKE_LEN_S
            ):
                self.mark_recorded(
                    track_index, active, len_s=loop_len, sl_state=int(sl_state)
                )
                self._log(
                    f"track {track_index + 1} slot {active + 1}: take landed "
                    f"({loop_len:.2f}s)"
                )
                changed = True
            phase = PHASE_IDLE
            self._overdub_pass[track_index] = False
        elif sl_state == SL_STATE_OFF and prev_sl != SL_STATE_OFF:
            phase = PHASE_IDLE
            self._overdub_pass[track_index] = False

        if phase != self._phase.get(track_index):
            changed = True
        self._phase[track_index] = phase
        return changed

    def mark_recorded(self, track_index: int, slot: int, *, len_s: float,
                      sl_state: int) -> None:
        """A take finished on this cell — the buffer now holds unsaved audio."""
        track = self.track(track_index)
        self._tracks[track_index] = replace(
            track.with_slot(slot, Slot(
                file=self.clip_path(track_index, slot).name,
                len_s=len_s, sl_state=sl_state, dirty=True,
            )),
            active_slot=slot,
        )
        self._phase[track_index] = PHASE_IDLE

    # -- execution --------------------------------------------------------

    def _execute(self, plan: SlotPlan, *, sl_state: int) -> bool:
        loop = plan.track
        if plan.action in (ACT_NOOP, ACT_CANCEL):
            if plan.action == ACT_CANCEL:
                self._send(f"/sl/{loop}/hit", ["pause_on"])
            return True

        if plan.action == ACT_CLEAR:
            return self._clear(plan)

        if plan.save_first and not self._flush_active(loop):
            self._log(
                f"track {loop + 1}: REFUSING to switch — the take on the "
                f"current slot did not reach disk, and switching would "
                f"overwrite the buffer holding it"
            )
            return False

        if plan.action == ACT_RECORD:
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
                    if not self._flush_active(loop):
                        return False
                    self._send(f"/sl/{loop}/hit", ["mute_on"])
                elif plan.save_first and not self._flush_active(loop):
                    return False
                self._send(f"/sl/{loop}/hit", ["undo_all"])
            gesture = plan_arm_record(
                grid_established=self._grid_established(),
                is_defining=self._is_defining(loop),
                quantized=self._quantized,
            )
            return self._dispatch_gesture(loop, gesture)

        if plan.action == ACT_CLOSE:
            gesture = plan_close_take(
                sl_state=sl_state,
                grid_established=self._grid_established(),
                is_defining=self._is_defining(loop),
                quantized=self._quantized,
            )
            if gesture.note:
                self._log(f"track {loop + 1} slot {plan.slot + 1}: {gesture.note}")
            return self._dispatch_gesture(loop, gesture)

        if plan.action == ACT_STOP:
            self._send(f"/sl/{loop}/hit", ["mute_on"])
            return True
        if plan.action == ACT_LAUNCH:
            return self._launch(plan)
        if plan.action == ACT_SWITCH:
            return self._launch(plan)
        return True

    def _dispatch_gesture(self, loop: int, gesture) -> bool:
        if gesture.arm_grid and self._grid is not None:
            self._grid.arm(loop)
        for cmd in gesture.commands:
            self._send(f"/sl/{loop}/hit", [cmd])
        if gesture.queue_stop:
            self._stop_queued[loop] = True
        return True

    def _launch(self, plan: SlotPlan) -> bool:
        """Load the incoming clip, then unmute — the engine defers the unmute.

        Load first, unmute second, always. The reverse order unmutes a buffer
        that still holds the *outgoing* clip, so the wrong audio is heard for
        however long the load takes.

        When the target slot is already loaded in the buffer (dirty, not yet on
        disk), skip ``load_loop`` and unmute in place.
        """
        loop = plan.track
        track = self.track(loop)
        if track.active_slot == plan.slot:
            active = track.slot(plan.slot)
            if active is not None and active.dirty:
                self._send(f"/sl/{loop}/hit", ["mute_off"])
                return True
        path = self.clip_path(loop, plan.slot)
        if not path.exists():
            self._log(f"track {loop + 1} slot {plan.slot + 1}: no clip file")
            return False
        self._send(f"/sl/{loop}/load_loop", [str(path)])
        self._send(f"/sl/{loop}/hit", ["mute_off"])
        return True

    def _clear(self, plan: SlotPlan) -> bool:
        loop, slot = plan.track, plan.slot
        track = self.track(loop)
        if track.active_slot == slot:
            self._send(f"/sl/{loop}/hit", ["undo_all"])
        self.clip_path(loop, slot).unlink(missing_ok=True)
        cleared = track.with_slot(slot, None)
        if cleared.active_slot == slot:
            cleared = replace(cleared, active_slot=None)
        self._tracks[loop] = cleared
        if track.active_slot == slot:
            self._phase[loop] = PHASE_IDLE
        return True

    def _flush_active(self, loop: int) -> bool:
        """Write the active slot's buffer to its file and verify it landed.

        Verified by size, not by the absence of an error: SooperLooper's
        save_loop is fire-and-forget over OSC, so "no error" is indistinguish-
        able from "never happened".
        """
        track = self.track(loop)
        if track.active_slot is None:
            return True
        active = track.slot(track.active_slot)
        if active is None or not active.dirty:
            return True

        path = self.clip_path(loop, track.active_slot)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)
        self._send(f"/sl/{loop}/save_loop", [str(path), "", "", "", ""])

        deadline = self._now() + SAVE_TIMEOUT_S
        while self._now() < deadline:
            try:
                if path.stat().st_size >= MIN_CLIP_BYTES:
                    self._tracks[loop] = track.with_slot(
                        track.active_slot, replace(active, dirty=False)
                    )
                    return True
            except OSError:
                pass
            time.sleep(SAVE_POLL_S)
        return False
