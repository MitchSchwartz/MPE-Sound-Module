"""A SooperLooper stand-in that models the transitions we actually depend on.

Every looper bug that cost an evening was a *timing* bug: a command sent before
the engine had confirmed the previous one, a poll landing between an action and
its acknowledgement, a quantized action that had been requested but not yet
happened. None of those are visible to a test that asserts on a MagicMock's
call list, because a mock answers instantly and always agrees.

This does not. It goes where the real engine goes, when the real engine goes
there — quantized actions sit pending until `boundary()` is called, exactly as
they sit until the next cycle on the Pi. That gap is where the bugs live, so
the gap is the point.

Verb semantics are taken from the engine, not guessed:
  * `record` on an armed loop is a CANCEL, not a stop. This is why a second tap
    while armed has to be queued instead of sent.
  * `trigger` plays from the clip start and lifts a mute, deferred to the sync
    boundary (plugin.cc MULTI_TRIGGER).
  * `mute_on` with mute_quantized set lands on the bar, not immediately.
"""

from __future__ import annotations

from scripts.sooperlooper.sl_loop_states import (
    SL_STATE_MUTE,
    SL_STATE_OFF,
    SL_STATE_PAUSED,
    SL_STATE_OVERDUBBING,
    SL_STATE_PLAYING,
    SL_STATE_RECORDING,
    SL_STATE_WAIT_START,
    SL_STATE_WAIT_STOP,
)


class FakeSlEngine:
    """Per-loop state plus a queue of actions waiting for the next bar."""

    def __init__(self, *, num_loops: int = 16, quantized: bool = True) -> None:
        self.quantized = quantized
        self.state: dict[int, int] = {i: SL_STATE_OFF for i in range(num_loops)}
        self.loop_len: dict[int, float] = {i: 0.0 for i in range(num_loops)}
        self.sent: list[tuple[str, object]] = []
        # loop -> state to enter at the next cycle boundary
        self._at_boundary: dict[int, int] = {}

    # --- the OSC surface the bench talks to ------------------------------
    def send_message(self, path: str, arg) -> None:
        self.sent.append((path, arg))
        parts = path.strip("/").split("/")
        if len(parts) != 3 or parts[0] != "sl" or parts[2] != "hit":
            return  # /set and global paths do not move loop state here
        loop = int(parts[1])
        if loop < 0:
            for i in self.state:
                self._hit(i, str(arg))
            return
        self._hit(loop, str(arg))

    def _hit(self, loop: int, cmd: str) -> None:
        st = self.state[loop]
        if cmd == "record":
            if st == SL_STATE_OFF:
                self.state[loop] = SL_STATE_WAIT_START if self.quantized else SL_STATE_RECORDING
            elif st == SL_STATE_WAIT_START:
                self.state[loop] = SL_STATE_OFF  # CANCEL — the whole reason for queueing
            elif st == SL_STATE_RECORDING:
                if self.quantized:
                    self.state[loop] = SL_STATE_WAIT_STOP
                else:
                    self._finish_record(loop)
            elif st == SL_STATE_PLAYING:
                # MEASURED on the Pi, 2026-08-28, engine 9951, loop_len 0.803 s:
                # hit `record` at loop_pos 0.496 and the loop stayed PLAYING,
                # went WAIT_START at +0.041 s (still at 0.496), and reached
                # RECORDING at +0.344 s and loop_pos 0.030 — i.e. it kept
                # sounding for 0.303 s against 0.307 s remaining to the wrap.
                #
                # SooperLooper holds playback to the boundary and swaps to
                # recording there, on its own. This branch was MISSING, so the
                # fake silently ignored `record` over a playing loop, and the
                # only reason the suite passed was that the runtime sent
                # `undo_all` first and dropped the loop to OFF. That made a
                # press-time mute look mandatory when it is the defect.
                self.state[loop] = (
                    SL_STATE_WAIT_START if self.quantized else SL_STATE_RECORDING
                )
        elif cmd == "overdub":
            # Real SL: `overdub` while RECORDING closes the take and starts
            # overdubbing at the same sample — one transition, no gap. That is
            # the whole reason the pad sends it instead of `record`.
            if st == SL_STATE_RECORDING:
                if self.quantized:
                    self._at_boundary[loop] = SL_STATE_OVERDUBBING
                    self.state[loop] = SL_STATE_WAIT_STOP
                else:
                    self._finish_record(loop)
                    self.state[loop] = SL_STATE_OVERDUBBING
            elif st == SL_STATE_OVERDUBBING:
                self.state[loop] = SL_STATE_PLAYING
            elif st == SL_STATE_PLAYING:
                self.state[loop] = SL_STATE_OVERDUBBING
        elif cmd == "mute_on":
            if st == SL_STATE_PLAYING:
                if self.quantized:
                    self._at_boundary[loop] = SL_STATE_MUTE
                else:
                    self.state[loop] = SL_STATE_MUTE
        elif cmd == "mute_off":
            self._at_boundary.pop(loop, None)
            if st == SL_STATE_MUTE:
                self.state[loop] = SL_STATE_PLAYING
        elif cmd == "trigger":
            if st in (SL_STATE_MUTE, SL_STATE_PAUSED, SL_STATE_PLAYING):
                if self.quantized:
                    self._at_boundary[loop] = SL_STATE_PLAYING
                else:
                    self.state[loop] = SL_STATE_PLAYING
        elif cmd == "pause_on":
            # From MUTE with only a queued trigger: cancel the queue, stay muted.
            if st == SL_STATE_MUTE and loop in self._at_boundary:
                self._at_boundary.pop(loop, None)
            else:
                self.state[loop] = SL_STATE_PAUSED
                self._at_boundary.pop(loop, None)
        elif cmd == "pause_off":
            if st == SL_STATE_PAUSED:
                self.state[loop] = SL_STATE_MUTE
        elif cmd == "undo_all":
            self.state[loop] = SL_STATE_OFF
            self.loop_len[loop] = 0.0
            self._at_boundary.pop(loop, None)

    def _finish_record(self, loop: int, length: float = 2.0) -> None:
        self.state[loop] = SL_STATE_PLAYING
        self.loop_len[loop] = length

    # --- time ------------------------------------------------------------
    def boundary(self, *, length: float = 2.0) -> None:
        """The next cycle boundary arrives. Everything queued lands now."""
        for loop, st in list(self.state.items()):
            if st == SL_STATE_WAIT_START:
                self.state[loop] = SL_STATE_RECORDING
            elif st == SL_STATE_WAIT_STOP:
                self._finish_record(loop, length)
        for loop, st in self._at_boundary.items():
            self.state[loop] = st
        self._at_boundary.clear()

    # --- what the bench listener would deliver ----------------------------
    def poll(self, gesture) -> None:
        """Deliver this loop's state, the way the OSC listener would."""
        loop = gesture.loop
        gesture.sync_from_sl(self.state[loop])
        if self.loop_len[loop]:
            gesture.sync_loop_len(self.loop_len[loop])
