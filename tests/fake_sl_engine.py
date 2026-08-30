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
        # Loops whose playhead has crossed zero since the last poll.
        self._wrapped: set[int] = set()
        #: loop -> file most recently loaded into its buffer, and loop -> file
        #: most recently written out. These used to be invisible: send_message
        #: returned early for anything that was not a `hit`, so a test could
        #: only see load_loop/save_loop by grepping raw OSC, and nothing in the
        #: engine model changed when a buffer was replaced. A switch that
        #: loaded the wrong clip, or loaded none at all, looked identical.
        self.loaded: dict[int, str] = {}
        self.saved: dict[int, str] = {}

    # --- the OSC surface the bench talks to ------------------------------
    def send_message(self, path: str, arg) -> None:
        self.sent.append((path, arg))
        parts = path.strip("/").split("/")
        if len(parts) != 3 or parts[0] != "sl":
            return  # /set and global paths do not move loop state here
        if parts[2] in ("load_loop", "save_loop"):
            self._buffer_op(int(parts[1]), parts[2], arg)
            return
        if parts[2] != "hit":
            return
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

    def _buffer_op(self, loop: int, op: str, arg) -> None:
        """`load_loop` replaces the buffer; `save_loop` writes it out.

        SooperLooper's handlers take `s:filename s:return_url s:error_path`, so
        the filename is the first element of a list. A bare string is accepted
        too — that is the one-argument form the engine silently DISCARDS, and
        modelling it as a no-op is what lets a test catch it.
        """
        if isinstance(arg, (list, tuple)):
            if len(arg) < 3:
                return  # discarded by the real engine: wrong arity, no reply
            filename = str(arg[0])
        else:
            return  # one-argument form — never matches the handler signature
        if loop < 0:
            return
        if op == "load_loop":
            self.loaded[loop] = filename
            # A loaded clip is resident and stopped until something starts it.
            if self.state.get(loop) in (SL_STATE_OFF, None):
                self.state[loop] = SL_STATE_PAUSED
            if not self.loop_len.get(loop):
                self.loop_len[loop] = 2.0
        else:
            self.saved[loop] = filename

    def _finish_record(self, loop: int, length: float = 2.0) -> None:
        self.state[loop] = SL_STATE_PLAYING
        self.loop_len[loop] = length

    # --- time ------------------------------------------------------------
    def boundary(self, *, length: float = 2.0) -> None:
        """The next cycle boundary arrives. Everything queued lands now."""
        # Snapshot first: only a loop that was ALREADY running has completed a
        # pass at this boundary. A take that closes into its ring-out overdub
        # here has one full pass still to go, and marking it wrapped now would
        # end the ring-out in the same breath that started it.
        running_before = {
            loop
            for loop, st in self.state.items()
            if st in (SL_STATE_PLAYING, SL_STATE_OVERDUBBING)
        }
        for loop, st in list(self.state.items()):
            if st == SL_STATE_WAIT_START:
                self.state[loop] = SL_STATE_RECORDING
            elif st == SL_STATE_WAIT_STOP:
                self._finish_record(loop, length)
        for loop, st in self._at_boundary.items():
            self.state[loop] = st
        self._at_boundary.clear()
        # A cycle boundary IS a loop wrap for anything that is playing. The
        # fake had no playhead at all, so a wrap was invisible to it and every
        # test that thought it was crossing a bar line was crossing nothing.
        # That blindness is why a switch firing at press time passed the suite.
        self._wrapped |= running_before

    # --- what the bench listener would deliver ----------------------------
    def poll(self, gesture) -> None:
        """Deliver this loop's state the way the OSC listener would, then let
        the bench's ring-out owner act on it.

        The appliance has both halves: `SlBenchStateListener` delivers on OSC
        dispatcher threads, and `poll_holds()` runs `poll_track_gestures` on
        every bench iteration — the idle branch at ~485 Hz and once per MIDI
        message besides. The second half was missing here until 2026-08-30, and
        it started mattering the day the ring-out got a single owner: the OSC
        entry points now only record what they saw, so a harness that ran the
        listener alone modelled an appliance whose main loop had stopped, with
        the cap and the 400 ms silent grace switched off along with it.

        **`poll_tail` and not `poll_track_gestures`, deliberately.** The full
        poll also drives `poll_led`, whose blink phase is
        `int(time.monotonic() / 0.25)` — real wall-clock time. Pulling that
        into a shared harness makes every LED assertion in every test that uses
        this engine depend on when the suite happened to run, and it did:
        `test_pad_never_goes_solid_green_before_the_engine_confirms` passed on
        one run and failed on the next with nothing changed between them. A
        test that flakes is not a stricter test. Callers that want the LED half
        drive it explicitly, with a clock they control.
        """
        loop = gesture.loop
        gesture.sync_from_sl(self.state[loop])
        length = self.loop_len[loop]
        if length:
            gesture.sync_loop_len(length)
        self.deliver_wrap(gesture)
        gesture.poll_tail()

    def deliver_wrap(self, gesture) -> None:
        """Feed the playhead across a wrap, if this loop has crossed one.

        A wrap is a change, so it belongs in a change-only delivery loop like
        any state transition.
        """
        loop = gesture.loop
        length = self.loop_len[loop]
        if loop not in self._wrapped or not length:
            return
        self._wrapped.discard(loop)
        # Two positions, because that is what the real listener delivers and
        # what `detect_loop_wrap` reads: near the end, then back at the start.
        # Sending only the zero would never trip the detector.
        gesture.sync_loop_pos(length * 0.99)
        gesture.sync_loop_pos(0.0)
