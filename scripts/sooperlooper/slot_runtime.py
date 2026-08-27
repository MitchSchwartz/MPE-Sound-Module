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

from slot_matrix import (
    ACT_CANCEL,
    ACT_CLEAR,
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
    ) -> None:
        self._send = send
        self._clips_dir = Path(clips_dir)
        self._num_tracks = num_tracks
        self._log = log or (lambda _m: None)
        self._now = now
        self._tracks: dict[int, Track] = {i: Track() for i in range(num_tracks)}

    # -- state ------------------------------------------------------------

    def track(self, index: int) -> Track:
        return self._tracks.get(index, Track())

    def tracks(self) -> dict[int, Track]:
        return dict(self._tracks)

    def clip_path(self, track: int, slot: int) -> Path:
        return self._clips_dir / f"live_t{track:02d}_s{slot}.wav"

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
            track_index=track_index, track=track, slot=slot,
            sl_state=sl_state, hold=hold,
        )
        if not self._execute(plan):
            return replace(plan, action=ACT_NOOP, note=f"{plan.note} — FAILED")
        self._tracks[track_index] = apply_pending(self.track(track_index), plan)
        if plan.note:
            self._log(f"track {track_index + 1} slot {slot + 1}: {plan.note}")
        return plan

    def boundary(self, track_index: int) -> None:
        """A quantize boundary arrived for this track: pending becomes true."""
        self._tracks[track_index] = resolve_at_boundary(self.track(track_index))

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

    # -- execution --------------------------------------------------------

    def _execute(self, plan: SlotPlan) -> bool:
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
            self._send(f"/sl/{loop}/hit", ["record"])
            return True
        if plan.action == ACT_STOP:
            self._send(f"/sl/{loop}/hit", ["mute_on"])
            return True
        if plan.action == ACT_LAUNCH:
            return self._launch(plan)
        if plan.action == ACT_SWITCH:
            return self._launch(plan)
        return True

    def _launch(self, plan: SlotPlan) -> bool:
        """Load the incoming clip, then unmute — the engine defers the unmute.

        Load first, unmute second, always. The reverse order unmutes a buffer
        that still holds the *outgoing* clip, so the wrong audio is heard for
        however long the load takes.
        """
        loop = plan.track
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
