"""Execute `slot_matrix` plans — slot files and buffer prep only.

Record/close/stop **gestures** are delegated to ``LoopFootswitch`` per track.
This module owns mutable slot occupancy, pending switch/stop, and the OSC that
only the matrix needs (`load_loop`, `save_loop`, `undo_all` for slot changes).
"""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path
from typing import Callable

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
    Slot,
    SlotPlan,
    Track,
    apply_pending,
    plan_cell_press,
    resolve_at_boundary,
)
from sl_loop_states import ACTIVE_PLAY, SL_STATE_OFF

# Gestures the footswitch owns — runtime must not send parallel OSC for these.
GESTURE_ACTIONS = frozenset({ACT_RECORD, ACT_CLOSE, ACT_STOP})

SAVE_TIMEOUT_S = 2.0
SAVE_POLL_S = 0.01
MIN_CLIP_BYTES = 512


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
    ) -> None:
        self._send = send
        self._clips_dir = Path(clips_dir)
        self._num_tracks = num_tracks
        self._log = log or (lambda _m: None)
        self._now = now
        self._tracks: dict[int, Track] = {i: Track() for i in range(num_tracks)}

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
        record_phase: str | None = None,
    ) -> SlotPlan:
        """Plan a cell press and run slot/buffer ops. Gestures are not sent here."""
        from slot_matrix import PHASE_IDLE

        track = self.track(track_index)
        plan = plan_cell_press(
            track_index=track_index,
            track=track,
            slot=slot,
            sl_state=sl_state,
            hold=hold,
            record_phase=record_phase or PHASE_IDLE,
        )
        if not self._execute_slot_ops(plan, sl_state=sl_state):
            return replace(plan, action=ACT_NOOP, note=f"{plan.note} — FAILED")
        self._tracks[track_index] = apply_pending(self.track(track_index), plan)
        if plan.action == ACT_RECORD:
            self._tracks[track_index] = replace(
                self.track(track_index), active_slot=plan.slot
            )
        if plan.note:
            self._log(f"track {track_index + 1} slot {slot + 1}: {plan.note}")
        return plan

    def boundary(self, track_index: int) -> None:
        """A quantize boundary arrived for this track: pending becomes true."""
        self._tracks[track_index] = resolve_at_boundary(self.track(track_index))

    def dispatch(self, plan: SlotPlan, *, sl_state: int = SL_STATE_OFF) -> SlotPlan:
        """Execute a plan produced elsewhere (e.g. scene row)."""
        if not self._execute_slot_ops(plan, sl_state=sl_state):
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

    def _execute_slot_ops(self, plan: SlotPlan, *, sl_state: int) -> bool:
        loop = plan.track
        if plan.action in (ACT_NOOP, ACT_CLOSE) or plan.action in GESTURE_ACTIONS:
            if plan.action == ACT_RECORD:
                return self._prepare_record(plan, sl_state=sl_state)
            return True

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

        if plan.action in (ACT_LAUNCH, ACT_SWITCH):
            return self._launch(plan)
        return True

    def _prepare_record(self, plan: SlotPlan, *, sl_state: int) -> bool:
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
        return True

    def _launch(self, plan: SlotPlan) -> bool:
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
        return True

    def _flush_active(self, loop: int) -> bool:
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
