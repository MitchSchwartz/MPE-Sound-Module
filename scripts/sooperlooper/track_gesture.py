"""Per-loop APC gesture state + pad grid wiring (8 visible of 15 tracks).

## Who owns the ring-out

This module runs on **two threads**. `SlOscSession` serves the engine's
auto-updates on a `ThreadingOSCUDPServer`, which hands *each datagram* to its
own thread; the bench's idle loop calls `poll_track_gestures` from the main
thread on every iteration (~485 Hz idle, and once per MIDI message besides).

`self._tail` has exactly one owner: **`poll_tail`, on the idle loop.** Nothing
on an OSC thread may create, end or abandon a ring-out. The OSC side records
what it saw into `_tail_inbox` and returns; `poll_tail` drains that in arrival
order and is the only code that touches the phase.

That is not stylistic. `_end_tail` is read-guard-clear, and it sends
`hit overdub` — **which is a TOGGLE**. Two threads could both pass the `tail is
None` guard, both clear it, and both send: the first ends the overdub and the
second STARTS a new one, recording room tone over the take behind a green pad.
The `sl_state == OVERDUBBING` guard narrows the window and cannot close it,
because `sl_state` is written by the same OSC thread that is racing. Four sites
could reach `_end_tail`/`_begin_tail` from an OSC thread — `sync_in_peak`,
`sync_loop_pos`'s wrap, and both arms of `sync_from_sl` — against `poll_tail`
on the main loop, at 485 Hz.

A lock would have made the double-fire impossible too. A single owner makes a
*second* owner impossible, which is the thing that keeps coming back: the exact
same toggle bug already shipped once from a stale OVERDUBBING report (see
`sync_from_sl`), was fixed there, and returned here wearing threads instead. So
the rule is enforced by `tests/test_clock_tail_ownership.py`, which reads the
source and fails naming the file and line if any OSC-thread entry point ever
reaches a tail mutator again.

Cost of the seam: one empty-`deque` drain per gesture per idle poll, on top of
the `is None` guard that was already there. **Measured** with `timeit`,
200 000 calls: 0.052 µs against 0.026 µs, so **+0.026 µs/call**. At 15 gestures
x 485 Hz that is 0.019 % of an x86 core, and ~0.06 % of a Pi 5 core on the
lifecycle review's x3 extrapolation. No subprocess, no timer, no new thread —
`DECISIONS.md` § 2026-08-18.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable

import os
import time

from apc_grid import DEFAULT_VIEW, GridView, all_clip_pads, pad_note
from led_compositor import LAYER_GESTURE
# LED constants are re-exported: the bench and its tests reach for them here,
# and the pad surface is this module's job even though the policy is not.
from sl_limits import MAX_USABLE_LOOPS
from led_table import (  # noqa: F401
    LED_GREEN,
    LED_GREEN_BLINK,
    LED_OFF,
    LED_RED,
    LED_RED_BLINK,
    LED_YELLOW,
    LED_YELLOW_BLINK,
    accelerating_hold_blink_on,
    led_for,
)
from loop_model import (
    STATE_IDLE,
    STATE_PLAYING,
    STATE_RECORDING,
    STATE_STOPPED,
    effective_state,
    pending_resolved,
    plan_gesture,
)
from sl_grid_state import GridState, derive_tempo
from tail_phase import (  # noqa: E402
    EXIT_WRAP,
    TAIL_TRACE_PATH,
    TailPhase,
    append_trace,
    cap_for,
)
from sl_grid_sync import (
    GRID_ANCHOR_FALLBACK_CYCLES,
    GRID_ANCHOR_MAX_S,
    RING_OUT_ENABLED,
    apply_established_grid,
    detect_loop_wrap,
    should_defer_phase_anchor,
)
from sl_loop_states import (
    ACTIVE_PLAY,
    ACTIVE_RECORD,
    SL_STATE_OFF,
    SL_STATE_OFF_MUTED,
    SL_STATE_OVERDUBBING,
    SL_STATE_PAUSED,
    SL_STATE_PLAYING,
    SL_STATE_RECORDING,
    SL_STATE_WAIT_START,
    SL_STATE_WAIT_STOP,
)

# A quantized action waits for the next cycle boundary. If no boundary arrives
# within this long, the grid clock is not running — release the pad rather than
# leaving it dead, and say so. See spec §J: a silent latch here cost an evening.
QUANTIZE_WAIT_TIMEOUT_S = float(os.environ.get("MPE_SL_QUANTIZE_TIMEOUT_S", "6.0"))

# How long to believe an unconfirmed intent before deferring to the engine.
#
# Generous on purpose: a quantized mute or trigger legitimately takes until the
# next bar, which at 60 BPM is four seconds. Expiring early would flip the pad
# back to its old colour mid-wait — exactly the "did my press register?" doubt
# the blink exists to remove. If it expires, the engine never acted and the pad
# should tell the truth about that.
PENDING_TIMEOUT_S = float(os.environ.get("MPE_SL_PENDING_TIMEOUT_S", "6.0"))

# Transition blink: alternate FROM-colour and TO-colour, half a period each.
TRANSITION_BLINK_S = float(os.environ.get("MPE_APC_TRANSITION_BLINK_S", "0.25"))

# -- the ring-out inbox: what an OSC thread may say about a tail ------------
#
# Four facts, recorded by whichever OSC dispatcher thread observed them and
# acted on by `poll_tail` alone. Each carries the time it was OBSERVED, not the
# time it was drained, so moving the decision to the idle loop does not move the
# cap window or the decay hold.
TAIL_BEGIN = "begin"        # the engine entered OVERDUBBING (a take just closed)
TAIL_ABANDON = "abandon"    # it left OVERDUBBING by some other route
TAIL_PEAK = "peak"          # one input-peak sample
TAIL_WRAP = "wrap"          # the playhead came round

#: Ceiling on queued peaks. Peaks arrive at MPE_SL_BENCH_PEAK_MS (25 ms => 40 Hz)
#: for the ONE loop that is ringing out, and the idle loop drains at ~485 Hz, so
#: the steady-state depth is 0-1. Reaching this means the main loop has been
#: stalled for ~25 s, which is a different emergency.
#:
#: The drop is COUNTED and logged, never silent. Losing peaks silently is the
#: precise failure this whole phase was rebuilt after: every tail peak was
#: dropped by a listener guard in 2026-08-26 and the ring-out was cut to a fixed
#: window with nobody the wiser (PI5-LOOPER-SEAM-WRAP.md). A queue that discards
#: evidence quietly reads exactly like a queue that is working.
#:
#: Control events (begin/abandon/wrap) are never dropped: there are at most a
#: handful per take, and losing one is the failure direction that leaves a live
#: overdub armed with nothing to end it.
TAIL_INBOX_LIMIT = int(os.environ.get("MPE_SL_TAIL_INBOX_LIMIT", "1024"))


def log(msg: str) -> None:
    """Timestamped bench log. Untimed lines made a 2 s quantize wait invisible."""
    print(f"[{time.strftime('%H:%M:%S')}.{int(time.time() % 1 * 1000):03d}] {msg}", flush=True)


def poll_track_gestures(gestures: list[TrackGesture], *, multigrid: bool = False) -> None:
    """Periodic bench poll — holds and LED transitions.

    When ``multigrid`` is on, skip gesture hold (``SlotSurface`` owns hold-
    clear) but still advance blink phase — ``SlotSurface.repaint`` reads
    ``current_led()``.
    """
    # The ring-out cap runs in BOTH modes and before anything else. It is the
    # one exit that does not depend on the peak meter arriving, so it is what
    # stops an overdub running forever if the feed is missing.
    for fs in gestures:
        fs.poll_tail()
    if multigrid:
        for fs in gestures:
            fs.poll_led()
        return
    for fs in gestures:
        fs.poll_hold()
        fs.poll_led()


def _osc_send(osc, path: str, args: list) -> None:
    osc.send_message(path, args)


class TrackGesture:
    #: Monotonic id across all loops, so one trace file can be read as a
    #: sequence of takes without joining on timestamps.
    _tail_traces = 0

    def __init__(
        self,
        *,
        loop: int,
        hold_ms: float,
        debounce_ms: float,
        hold_blink_start_ms: float = 500.0,
        num_loops: int = MAX_USABLE_LOOPS,
        quantized: bool = True,
        grid: GridState | None = None,
        on_grid_established=None,
        on_phase_reanchor=None,
        on_grid_dropped=None,
        on_tail_change=None,
        multigrid: bool = False,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.loop = loop
        self.grid = grid
        self._multigrid = multigrid
        self._on_grid_established = on_grid_established
        self._on_phase_reanchor = on_phase_reanchor
        self._on_grid_dropped = on_grid_dropped
        #: Called (loop, active) when the ring-out phase starts or ends, so the
        #: surface can register the peak meter and colour the pad.
        self._on_tail_change = on_tail_change
        #: Called at each loop wrap. This is the ONE boundary signal in the
        #: bench: the same detector that ends the ring-out overdub also
        #: releases a queued slot switch, so the two cannot disagree about
        #: where the bar line is.
        self._on_wrap = None
        self.loop_len = 0.0
        self.loop_pos = 0.0
        self._loop_pos_seen = False
        # True while an overdub started by closing a take is still running.
        #
        # OWNED BY `poll_tail`, ON THE IDLE LOOP. Created, ended and abandoned
        # there and nowhere else — see the module docstring for why a second
        # writer here is audible rather than theoretical.
        self._tail: TailPhase | None = None
        #: What the OSC dispatcher threads have seen, in arrival order.
        #: `deque.append`/`popleft` are atomic under CPython, so the seam needs
        #: no lock — and needing no lock is the point: there is one owner, not
        #: several taking turns.
        self._tail_inbox: deque[tuple[str, float, float]] = deque()
        #: Peaks refused because the inbox was over `TAIL_INBOX_LIMIT`, so the
        #: drop can be reported instead of inferred from a short trace.
        self._tail_peaks_dropped = 0
        #: Injected at construction, the way SlotRuntime takes its clock. The
        #: tail's cap is a statement about elapsed time, and a phase whose
        #: clock is only injectable at some call sites is one a test can drive
        #: halfway.
        self._now = now
        self._last_loop_pos = 0.0
        self._phase_reanchor_at = 0.0
        # Is this loop waiting for cycle boundaries? False in free-form, where
        # arming a quantize wait strands the pad on a boundary that never comes.
        self.quantized = quantized
        self.num_loops = num_loops
        self.hold_s = hold_ms / 1000.0
        self.hold_blink_start_s = hold_blink_start_ms / 1000.0
        self.debounce_s = debounce_ms / 1000.0
        self._pending: str | None = None
        self._pending_since = 0.0
        self._osc = None
        self._compositor = None
        self._note: int | None = None
        self._pad_down = False
        self._pad_down_at = 0.0
        self._hold_fired = False
        self._last_action_at = 0.0
        self.sl_state = SL_STATE_OFF
        self.awaiting_quantize = False
        self._wait_since = 0.0
        self._stop_queued = False
        self._led_transition: tuple[int, int] | None = None
        self._loop_pos_at = 0.0

    def bind(self, osc, compositor, note: int | None) -> None:
        self._osc = osc
        self._compositor = compositor
        self._note = note

    def set_note(self, note: int | None) -> None:
        """Move this track to a different pad, or off-screen (None).

        Banking does not change what a track *is* — only where, or whether, it
        is drawn. The pad this track is leaving loses this gesture's opinion in
        the same call, so it cannot sit there showing the previous track's
        colour. That used to be handled by clearing `_led_last` and letting the
        next unconditional paint overwrite it — which worked only because
        `apply_view` blanked the whole row first.
        """
        if self._note is not None and self._note != note:
            self._submit({self._note: None})
        self._note = note

    def release_pad(self) -> None:
        """Abandon an in-flight pad gesture without firing it.

        Banking while a pad is held would otherwise strand `_pad_down` on the
        track that just left the screen: poll_hold() runs for every track,
        visible or not, so ~hold_s later it would fire the long-press and clear
        a loop the player never let go of. The matching note-off, meanwhile,
        now dispatches to whichever track took over that pad — harmless only
        because on_pad_up() is guarded by its own `_pad_down`.
        """
        self._pad_down = False
        self._hold_fired = False

    @property
    def state(self) -> str:
        """The loop's state in the bench's vocabulary — DERIVED, never stored.

        This used to be an assignable field written the instant a command was
        sent, so it disagreed with the engine for as long as the engine took to
        answer, and any poll arriving in between could clobber it. Now there is
        one source of truth (`sl_state`) plus an explicitly unconfirmed intent
        (`_pending`) that expires on its own.
        """
        return effective_state(self.sl_state, self._pending)

    @property
    def pending(self) -> str | None:
        """Unconfirmed bench intent — same field ``led_for`` reads."""
        return self._pending

    def _expect(self, state: str | None) -> None:
        self._pending = state
        self._pending_since = time.monotonic()

    def _expire_pending(self) -> None:
        """Drop an intent the engine has confirmed, contradicted, or ignored."""
        if self._pending is None:
            return
        if pending_resolved(self.sl_state, self._pending):
            self._pending = None
        elif (time.monotonic() - self._pending_since) > PENDING_TIMEOUT_S:
            log(f"loop {self.loop}: !! '{self._pending}' never confirmed in "
                f"{PENDING_TIMEOUT_S:.0f}s (sl_state={self.sl_state}) — "
                f"deferring to the engine")
            self._pending = None

    # `_flush_deferred_grid_side_effects` stood here until 2026-08-30, holding a
    # `_deferred_grid_clock` tuple and calling `_on_grid_established` from it.
    # Deleted: the method had **zero callers** and the field was never assigned
    # anything but None, so the whole deferral was a description of behaviour
    # that does not run. That is not harmless in this file — it made a fifth
    # candidate answer to "what establishes the grid?", and reading a codebase
    # for its owner is the job this branch exists to make possible. Same
    # treatment as the dead `TAIL_*` seam constants (audit cycle 1, finding E).
    #
    # The live deferral is `_phase_reanchor_at` plus `_try_commit_phase_reanchor`,
    # driven from `sync_loop_len` / `sync_loop_pos`, and it is untouched.

    def sync_from_sl(self, sl_state: int) -> bool:
        """Mirror SooperLooper state → bench LED (all loops incl. 0)."""
        prev_sl = self.sl_state
        before = self.state
        led_before = self._led_target()
        self.sl_state = sl_state
        self._expire_pending()

        if sl_state == SL_STATE_WAIT_STOP:
            # Hold taps only after stop-record is sent (WAIT_STOP); during the
            # arm phase (WAIT_START) a tap means cancel and must reach SL. The
            # hold is time-bounded either way — see _waiting_for_quantize.
            if not self.awaiting_quantize:
                self._begin_quantize_wait()
        else:
            self.awaiting_quantize = False

        if sl_state == SL_STATE_RECORDING and self._stop_queued:
            # Recording just began. Send the stop now; SL quantizes it to the
            # next boundary, giving exactly one cycle of audio.
            self._stop_queued = False
            self._hit("record")
            self._begin_quantize_wait()

        # THIS RUNS ON AN OSC DISPATCHER THREAD, so it only records what the
        # engine said. `poll_tail` decides. See the module docstring.
        if sl_state == SL_STATE_OVERDUBBING:
            # On the TRANSITION only. This ran on every OVERDUBBING report, so
            # a repeated or stale one re-armed the phase after it had already
            # ended — and the cap then sent `overdub` with nothing armed to
            # turn off, which turns overdub back ON. A loop quietly recording
            # the room behind a green pad.
            if prev_sl != SL_STATE_OVERDUBBING:
                self._tail_inbox.append((TAIL_BEGIN, 0.0, self._now()))
        elif prev_sl == SL_STATE_OVERDUBBING:
            # Ended by the pad, or by the engine. Either way stop watching —
            # and do NOT send an overdub-off, because whatever ended it already
            # did. Sending one here would toggle overdub back ON.
            self._tail_inbox.append((TAIL_ABANDON, 0.0, self._now()))

        if sl_state == SL_STATE_PLAYING:
            self._maybe_establish_grid()

        if self.grid is not None:
            if self.grid.note_loop_content(self.loop, sl_state != SL_STATE_OFF):
                log(f"loop {self.loop}: last clip cleared — grid dropped, "
                    f"next take defines a new one")
                if self._on_grid_dropped is not None:
                    self._on_grid_dropped()
        # Repaint whenever the *pixel* changes, not just when the state does.
        # Comparing states alone left a queued launch blinking green forever:
        # the loop was already Playing when the launch landed, so neither
        # sl_state nor the derived state moved, and nothing ever asked the LED
        # to catch up.
        changed = (before != self.state or prev_sl != sl_state
                   or led_before != self._led_target())
        if changed:
            log(f"loop {self.loop}: SL sync sl={sl_state} bench={self.state}")
            # Always. `_sync_led` is where `_led_transition` is armed, and the
            # transition is READ under multigrid — `SlotSurface` paints the pad
            # from `current_led()`. Skipping the whole call here to avoid
            # painting also skipped the arming, so the record-to-play tail
            # blink never happened on the matrix and the pad jumped straight to
            # green. `_set_led` already suppresses the paint under multigrid;
            # that is the only half that should ever be conditional.
            self._sync_led()
            return True
        return False

    def set_wrap_callback(self, callback) -> None:
        """Install the wrap listener. Set by `SlotSurface`, which does not
        exist yet when the gestures are built."""
        self._on_wrap = callback

    def sync_loop_len(self, loop_len: float) -> None:
        self.loop_len = float(loop_len)
        # Engine truth only: a length that arrives while we merely *expect*
        # playback would derive a tempo from a take that never landed.
        if self.sl_state == SL_STATE_PLAYING:
            self._maybe_establish_grid()
        elif self._phase_reanchor_at > 0.0:
            self._try_commit_phase_reanchor()

    def sync_loop_pos(self, loop_pos: float) -> None:
        pos = float(loop_pos)
        # `_last_loop_pos` is set one update behind (see below), so the shared
        # detector fires ~40 ms after the wrap. That is harmless for grid
        # re-anchoring and not for the overdub, where every late millisecond
        # records pass two on top of pass one — so that check gets the true
        # previous position.
        if self._loop_pos_seen and detect_loop_wrap(
            self.loop_pos, pos, self.loop_len
        ):
            # Recorded, not acted on — this is an OSC thread. `poll_tail` runs
            # on every bench iteration (idle AND per MIDI message), so the
            # overdub closes within one ~2.1 ms period of this line. Set that
            # against the 20 ms `loop_pos` reporting interval the wrap is
            # already quantised to, and against the failure it removes, which
            # is a whole extra PASS of room recorded over the take.
            self._tail_inbox.append((TAIL_WRAP, 0.0, self._now()))
            if self._on_wrap is not None:
                self._on_wrap()
        if self._loop_pos_seen and detect_loop_wrap(
            self._last_loop_pos, pos, self.loop_len
        ):
            self._try_commit_phase_reanchor(force_wrap=True)
        self._last_loop_pos = self.loop_pos if self._loop_pos_seen else pos
        self.loop_pos = pos
        self._loop_pos_at = time.monotonic()
        self._loop_pos_seen = True
        if self._phase_reanchor_at > 0.0:
            self._try_commit_phase_reanchor()

    # -- the ring-out (TAIL) phase --------------------------------------
    #
    # Everything from `_begin_tail` to `poll_tail` runs on the IDLE LOOP and
    # only there. The three methods that mutate `self._tail` are named in
    # `tests/test_clock_tail_ownership.py::TAIL_MUTATORS`; adding a caller from
    # an OSC entry point fails that test by name and line.

    def _begin_tail(self, at: float | None = None) -> None:
        """The take just closed into its ring-out.

        Armed off `sl_state == OVERDUBBING` rather than off the command we
        sent, so an overdub the engine never entered cannot leave this latched.

        `at` is when the engine's OVERDUBBING report was OBSERVED, so the cap
        window starts where the ring-out did rather than where the idle loop
        happened to look.

        The cap is one CYCLE — `GridState.cycle_s`, the first take's own length
        (`looper-timing-model-spec.md` §6). Not one bar: since `d06fb08` a first
        take may read as 2, 4 or 8 bars, and `bar_s` is a description of the
        cycle, never a boundary (§1).
        """
        cycle = self.grid.cycle_s if self.grid is not None else None
        cap, cap_source = cap_for(cycle, loop_len=self.loop_len)
        self._tail = TailPhase(started_at=self._now() if at is None else at,
                               cap_s=cap, trace=bool(TAIL_TRACE_PATH))
        if self._on_tail_change is not None:
            self._on_tail_change(self.loop, True)
        log(f"loop {self.loop}: tail phase — ends on decay, capped at "
            f"{cap:.3f}s ({cap_source})")

    def _abandon_tail(self) -> None:
        """Something else ended the overdub. Drop the phase, send nothing."""
        if self._tail is None:
            return
        self._tail = None
        if self._on_tail_change is not None:
            self._on_tail_change(self.loop, False)

    def _end_tail(self, reason: str) -> None:
        """Leave the overdub, once, and say why.

        "Once" is guaranteed by there being ONE CALLER THREAD, not by the guard
        below. Read-guard-clear is not atomic: two threads both saw a live tail
        here, both cleared it and both sent `overdub` — a toggle — so the second
        send started a fresh overdub recording the room over the take. That is
        why `poll_tail` is the only path in.
        """
        tail = self._tail
        if tail is None:
            return
        self._tail = None
        # Only if the engine is actually overdubbing. `overdub` is a TOGGLE:
        # sent when it is not, it starts one. Belt and braces against the phase
        # ever being armed when the engine has already moved on.
        if self.sl_state == SL_STATE_OVERDUBBING:
            self._hit("overdub")
        if self._on_tail_change is not None:
            self._on_tail_change(self.loop, False)
        elapsed = tail.elapsed(self._now())
        log(f"loop {self.loop}: ring-out ended on {reason} after "
            f"{elapsed:.3f}s")
        if TAIL_TRACE_PATH:
            # After the overdub is closed and the pad repainted: tracing is
            # evidence, and evidence must never be in the path of the thing
            # it is measuring.
            TrackGesture._tail_traces += 1
            failure = append_trace(
                TAIL_TRACE_PATH,
                tail_id=TrackGesture._tail_traces,
                loop=self.loop,
                tail=tail,
                reason=reason,
                elapsed=elapsed,
            )
            if failure is not None:
                log(failure)

    def sync_in_peak(self, value: float) -> None:
        """Input peak from the engine — RECORDED HERE, ACTED ON IN `poll_tail`.

        Runs on an OSC dispatcher thread. It used to feed the phase directly
        and end the overdub itself, which is one of the four ways two threads
        could both send the `overdub` toggle. Now it timestamps the sample and
        leaves; the owner drains it within one ~2.1 ms poll, against a 25 ms
        peak interval.

        The queue is bounded and the overflow is COUNTED, not swallowed: a
        peak stream that quietly loses samples looks exactly like a healthy one
        at the reading site, which is how the ring-out came to be cut at a
        fixed window for weeks (PI5-LOOPER-SEAM-WRAP.md).
        """
        if len(self._tail_inbox) >= TAIL_INBOX_LIMIT:
            self._tail_peaks_dropped += 1
            return
        self._tail_inbox.append((TAIL_PEAK, float(value), self._now()))

    def poll_tail(self) -> None:
        """The ring-out's ONE owner. Drain what the OSC threads saw, then tick.

        Deliberately not dependent on the meter: if `in_peak_meter` never
        arrives — unregistered, dropped by a listener guard, input silent — the
        overdub must still end. Every tail peak being silently dropped is a
        thing that has actually happened here (PI5-LOOPER-SEAM-WRAP.md), and
        the result was tails cut to a fixed window with nobody the wiser.

        Called from `poll_track_gestures`, which the bench runs on every
        iteration of its loop — the idle branch at ~485 Hz and once per MIDI
        message besides — so no burst of pad or fader traffic can starve it.
        """
        inbox = self._tail_inbox
        while inbox:
            kind, value, at = inbox.popleft()
            if kind == TAIL_PEAK:
                if self._tail is None:
                    continue          # a peak outside a ring-out means nothing
                reason = self._tail.peak(value, at)
                if reason is not None:
                    self._end_tail(reason)
            elif kind == TAIL_BEGIN:
                # The `is None` guard belongs to the owner. A stale or repeated
                # OVERDUBBING report must not re-arm a phase that has ended:
                # the cap would then send `overdub` with nothing to turn off,
                # which turns it back ON.
                if self._tail is None:
                    self._begin_tail(at)
            elif kind == TAIL_ABANDON:
                self._abandon_tail()
            elif kind == TAIL_WRAP:
                self._end_tail(EXIT_WRAP)
        if self._tail_peaks_dropped:
            dropped, self._tail_peaks_dropped = self._tail_peaks_dropped, 0
            log(f"loop {self.loop}: !! dropped {dropped} tail peak(s) — the "
                f"idle loop fell more than {TAIL_INBOX_LIMIT} samples behind")
        if self._tail is None:
            return
        reason = self._tail.tick(self._now())
        if reason is not None:
            self._end_tail(reason)

    @property
    def in_tail(self) -> bool:
        return self._tail is not None

    def _maybe_establish_grid(self) -> None:
        """The defining take just landed — grid immediately, phase maybe at wrap.

        Grid existence (tempo capture, quantize on for later clips) must land
        the moment the take saves. Only the *phase reset* inside
        establish_grid_clock may defer: a late PLAYING report mid-bar would
        otherwise shove clip 2+ early. See looper-transport-clock-spec §K.6.
        """
        if self.grid is None or not self.grid.is_pending(self.loop):
            return
        if self.loop_len <= 0.0:
            return  # length not reported yet; sync_loop_len will retry
        if self.grid.established:
            return
        derived = self.grid.establish(self.loop, self.loop_len)
        if derived is None:
            return
        bpm, bars = derived
        log(
            f"loop {self.loop}: grid established from this take — "
            f"{self.loop_len:.3f}s = {bars} bar(s) @ {bpm:.1f} BPM. "
            f"Later clips count in and quantize; this clip is now ordinary."
        )
        if self._on_grid_established is not None:
            self._on_grid_established(bpm, bars)
        if should_defer_phase_anchor(
            self.loop_pos, self.loop_len, loop_pos_seen=self._loop_pos_seen
        ):
            self._phase_reanchor_at = time.monotonic()
            log(
                f"loop {self.loop}: phase re-anchor deferred — "
                f"loop_pos={self.loop_pos:.3f}s (wait for wrap or "
                f"≤{GRID_ANCHOR_MAX_S * 1000:.0f} ms)"
            )
            self._try_commit_phase_reanchor()

    def _try_commit_phase_reanchor(self, *, force_wrap: bool = False) -> None:
        if self._phase_reanchor_at <= 0.0:
            return
        if self.grid is None or not self.grid.established:
            self._phase_reanchor_at = 0.0
            return
        ready = force_wrap
        if not ready and self._loop_pos_seen:
            ready = self.loop_pos <= GRID_ANCHOR_MAX_S
        if not ready and self.loop_len > 0.0:
            waited = time.monotonic() - self._phase_reanchor_at
            if waited >= self.loop_len * GRID_ANCHOR_FALLBACK_CYCLES:
                log(
                    f"loop {self.loop}: !! phase re-anchor fallback after "
                    f"{waited:.2f}s — loop_pos={self.loop_pos:.3f}s"
                )
                ready = True
        if not ready:
            return
        derived = derive_tempo(self.loop_len)
        if derived is None:
            self._phase_reanchor_at = 0.0
            return
        bpm, _bars = derived
        self._phase_reanchor_at = 0.0
        log(
            f"loop {self.loop}: phase re-anchored at loop_pos="
            f"{self.loop_pos:.3f}s @ {bpm:.1f} BPM"
        )
        if self._on_phase_reanchor is not None:
            self._on_phase_reanchor(bpm)

    def _path(self, suffix: str) -> str:
        return f"/sl/{self.loop}/{suffix}"

    def _hit(self, cmd: str) -> None:
        self._osc.send_message(self._path("hit"), cmd)
        log(f"loop {self.loop}: -> {cmd} (state={self.state})")

    def _set_led(self, velocity: int) -> None:
        """Submit this track's pad colour. Single-clip mode only.

        Under multigrid the gesture computes colour and writes nothing —
        `SlotSurface` reads `current_led()` and paints. That early return is
        the one place in the LED stack where ownership was genuinely enforced
        before this branch, and it is the shape everything else now has.

        There is no `force=`. It existed because `_led_last` was this object's
        private record of what the device showed, and `_sync_led` passed
        `force=True` unconditionally to defeat it — leaving the flag doing
        nothing but dedup for `poll_led`. The diff is at the wire now.
        """
        if self._multigrid or self._note is None:
            return  # banked off-screen: this track has no pad to paint
        self._submit({self._note: max(0, min(127, velocity))})

    def _submit(self, desired: dict[int, int | None]) -> None:
        if self._compositor is None:
            return
        self._compositor.submit(LAYER_GESTURE, desired)

    def _led_target(self) -> tuple[int, ...]:
        return led_for(
            self.sl_state,
            pending=self._pending,
            tail=self.in_tail,
        )

    def current_led(self) -> int:
        """Colour this track's active slot should show — no MIDI write.

        Multigrid ``SlotSurface`` calls this when painting the active row;
        single-clip mode uses ``_sync_led`` / ``poll_led`` instead.
        """
        if self._hold_led_lock():
            elapsed = time.monotonic() - self._pad_down_at
            if self._hold_targets_cancel():
                blink_on = accelerating_hold_blink_on(
                    elapsed - self.hold_blink_start_s,
                    hold_s=max(self.hold_s - self.hold_blink_start_s, 0.001),
                    blink_after_s=0.0,
                )
                if blink_on is not None:
                    return LED_RED if blink_on else LED_OFF
            return LED_RED
        if self._led_transition is not None:
            seq = self._led_transition
            phase = int(time.monotonic() / TRANSITION_BLINK_S) % len(seq)
            return seq[phase]
        seq = self._led_target()
        if not seq:
            return LED_OFF
        # Cycle the sequence, do not take frame 0 and call it the colour.
        #
        # `led_for` returns a BLINK SEQUENCE; length 1 means hold. This took
        # `seq[0]` unconditionally, so every multi-phase sequence collapsed to
        # its first frame — and only under multigrid, which is what the
        # appliance runs (`MPE_SL_MULTIGRID=1`). Single-clip mode animates the
        # same sequences correctly via `poll_led`, so the surface disagreed
        # with itself depending on a mode nobody changes.
        #
        # What that cost, reported from the instrument 2026-08-30: the ring-out
        # is TAIL_CAPTURE = (RED, GREEN), deliberately the one alternating
        # pattern on the surface, and it showed as plain red — "we're getting
        # red blinking during the tail capture rather than red and green
        # blinking." RECORD_TO_PLAY = (OFF, RED, OFF, GREEN) lost its green
        # half the same way, so "recording -> playing" read as an ordinary
        # queued-to-record blink. Both are states the player acts on mid-take.
        if len(seq) == 1:
            return seq[0]
        phase = int(time.monotonic() / TRANSITION_BLINK_S) % len(seq)
        return seq[phase]

    def _hold_led_lock(self) -> bool:
        """True while hold-warning owns the pad LED (after blink-start delay)."""
        if not self._pad_down or self._hold_fired:
            return False
        return (time.monotonic() - self._pad_down_at) >= self.hold_blink_start_s

    def _sync_led(self) -> None:
        """Paint the pad from engine truth plus unconfirmed intent.

        All the policy lives in `led_for`; this just applies the result. A
        one-element sequence is a steady colour, anything longer animates and
        `poll_led` drives it from here.
        """
        if self._hold_led_lock():
            return
        seq = self._led_target()
        if len(seq) > 1:
            self._led_transition = seq
            return
        self._led_transition = None
        self._set_led(seq[0])

    def _debounced(self) -> bool:
        return (time.monotonic() - self._last_action_at) < self.debounce_s

    def _mark_action(self) -> None:
        self._last_action_at = time.monotonic()

    def _waiting_for_quantize(self) -> bool:
        """True while a quantized action is pending — but never indefinitely.

        If no cycle boundary arrives within QUANTIZE_WAIT_TIMEOUT_S the grid
        clock is not running. Release the pad and say so; a dead pad with no
        explanation is the worst possible failure here.
        """
        if not self.awaiting_quantize:
            return False
        waited = time.monotonic() - self._wait_since
        if waited < QUANTIZE_WAIT_TIMEOUT_S:
            return True
        print(
            f"loop {self.loop}: !! no sync boundary in {waited:.1f}s — grid clock "
            f"is not running (sl_state={self.sl_state}). Releasing pad. "
            f"Check the clock: MPE_SL_GRID_CLOCK=internal needs a tempo; "
            f"'transport' needs a rolling JACK timebase master.",
            flush=True,
        )
        self.awaiting_quantize = False
        return False

    def _begin_quantize_wait(self) -> None:
        self.awaiting_quantize = True
        self._wait_since = time.monotonic()

    def _clear_loop(self) -> None:
        self._stop_queued = False
        if self.grid is not None:
            self.grid.cancel(self.loop)
        self._hit("undo_all")
        self.awaiting_quantize = False
        # Expect idle rather than asserting it. Forcing sl_state here would
        # forge an engine report, and the grid drop hangs off exactly that
        # signal — "no clips, no grid" has to be the engine's verdict, not the
        # bench's. The pad goes dark immediately either way; if the engine never
        # confirms, the intent expires and the pad tells the truth again.
        self._expect(STATE_IDLE)
        self._sync_led()
        self._mark_action()

    def expect_cleared(self) -> None:
        """Someone else emptied this loop — expect idle, so the next gesture records.

        The multi-clip runtime clears the buffer itself (`mute_on` + `undo_all`)
        when a press means "record into a different slot on this track". Without
        being told, `state` still derives `playing` from the last engine report,
        so the very next gesture is a mute: reported from the appliance
        2026-08-27 as the pad going green, then yellow on a second press, with
        no take ever recorded.

        An expectation, not an assertion — the engine still gets to confirm, and
        an intent that never lands expires on its own. Sends no OSC: the caller
        has already cleared the engine and a second `undo_all` from here would
        be the double-command this design exists to prevent.
        """
        self.awaiting_quantize = False
        self._stop_queued = False
        self._led_transition = None
        self._expect(STATE_IDLE)
        self._sync_led()

    def _hold_targets_cancel(self) -> bool:
        """True when a long press should abort a take, not delete a landed clip."""
        if self.sl_state in (
            SL_STATE_RECORDING,
            SL_STATE_WAIT_START,
            SL_STATE_WAIT_STOP,
        ):
            return True
        return self.state == STATE_RECORDING

    def _cancel_recording(self) -> None:
        """Abort an in-progress take (armed or recording)."""
        self._stop_queued = False
        if self.grid is not None:
            self.grid.cancel(self.loop)
        self.awaiting_quantize = False
        if self.sl_state == SL_STATE_WAIT_START:
            self._hit("record")
        else:
            self._hit("undo_all")
        self._expect(STATE_IDLE)
        self._sync_led()
        self._mark_action()

    def synthesised_tap(self) -> None:
        """A complete down+up that the debounce must not eat.

        The debounce exists to reject HARDWARE double-triggers — one physical
        press reported twice by a contact bouncing inside the pad. A tap this
        process generates itself cannot bounce, and the two edges arrive in the
        same microsecond, so `_debounced()` rejected the `up` every time on the
        appliance's real 200 ms window (`MPE_APC_DEBOUNCE_MS`).

        That mattered because the mute/launch half of the gesture lands on the
        UP. Scene launch of a stored, muted clip was therefore a silent no-op
        on hardware while passing every test, because every harness constructs
        the gesture with `debounce_ms=0` — the one value at which the bug
        cannot appear.
        """
        self._gesture("down", debounced=False)
        self._gesture("up", debounced=False)

    def _gesture(self, edge: str, *, debounced: bool = True) -> None:
        if debounced and self._debounced():
            log(f"loop {self.loop}: -> {edge} ignored (debounce)")
            return
        if self._waiting_for_quantize():
            log(f"loop {self.loop}: -> {edge} ignored (quantize wait)")
            return

        self._expire_pending()
        plan = plan_gesture(
            edge=edge,
            sl_state=self.sl_state,
            pending=self._pending,
            grid_established=self.grid is None or self.grid.established,
            is_defining=self.grid is not None and self.grid.is_pending(self.loop),
            quantized=self.quantized,
            tail_capture_enabled=RING_OUT_ENABLED,
        )
        if not (
            plan.commands
            or plan.queue_stop
            or plan.arm_grid
            or plan.cancel_pending
        ):
            return
        if plan.note:
            log(f"loop {self.loop}: {plan.note}")
        if plan.arm_grid and self.grid is not None:
            self.grid.arm(self.loop)
        for cmd in plan.commands:
            self._hit(cmd)
        if plan.queue_stop:
            self._stop_queued = True
        if plan.begin_quantize_wait:
            self._begin_quantize_wait()
        if plan.cancel_pending:
            self._pending = None
            self._pending_since = None
        elif plan.expect is not None:
            self._expect(plan.expect)

        self._sync_led()
        self._mark_action()
        log(f"loop {self.loop}: -> {edge} done (state={self.state}, sl_state={self.sl_state})")

    @property
    def hold_fired(self) -> bool:
        """Did the current gesture already fire its long-press?

        Read by `SlotSurface`, which drives this gesture's `poll_hold` under
        multigrid and needs to know when it fired so the two do not both track
        hold state — two hold flags is how the surface ended up firing the
        blink at one moment and the clear at another.
        """
        return self._hold_fired

    def on_pad_down(self) -> None:
        self._pad_down = True
        self._pad_down_at = time.monotonic()
        self._hold_fired = False
        log(f"loop {self.loop}: pad down (note {self._note})")
        self._gesture("down")

    def on_pad_up(self) -> None:
        held = time.monotonic() - self._pad_down_at
        log(f"loop {self.loop}: pad up held={held:.3f}s hold_fired={self._hold_fired}")
        if self._pad_down and not self._hold_fired:
            # Stop lands on down; release during an active take must not fire
            # mute/launch — pending may already say "playing" before SL confirms.
            if self.sl_state not in (
                SL_STATE_RECORDING,
                SL_STATE_WAIT_START,
                SL_STATE_WAIT_STOP,
            ):
                self._gesture("up")
        self._pad_down = False

    def poll_led(self) -> None:
        """Drive transition blink and hold-warning blink."""
        if self._pad_down and not self._hold_fired and self._note is not None:
            elapsed = time.monotonic() - self._pad_down_at
            if elapsed >= self.hold_blink_start_s:
                if self._hold_targets_cancel():
                    blink_on = accelerating_hold_blink_on(
                        elapsed - self.hold_blink_start_s,
                        hold_s=max(self.hold_s - self.hold_blink_start_s, 0.001),
                        blink_after_s=0.0,
                    )
                    if blink_on is not None:
                        self._set_led(LED_RED if blink_on else LED_OFF)
                        return
                else:
                    self._set_led(LED_RED)
                    return
        if self._led_transition is None:
            return
        seq = self._led_transition
        phase = int(time.monotonic() / TRANSITION_BLINK_S) % len(seq)
        self._set_led(seq[phase])

    def poll_hold(self) -> None:
        if not self._pad_down or self._hold_fired:
            return
        if (time.monotonic() - self._pad_down_at) < self.hold_s:
            return
        self._hold_fired = True
        self._pad_down = False
        if self._hold_targets_cancel():
            log(f"loop {self.loop}: -> hold cancel recording")
            self._cancel_recording()
        else:
            log(f"loop {self.loop}: -> hold clear")
            self._clear_loop()


def build_track_gestures(
    *,
    osc,
    compositor,
    num_loops: int,
    hold_ms: float,
    debounce_ms: float,
    hold_blink_start_ms: float = 500.0,
    quantized: bool = True,
    grid: GridState | None = None,
    view: GridView | None = None,
    on_grid_established=None,
    on_phase_reanchor=None,
    on_grid_dropped=None,
    on_tail_change=None,
    multigrid: bool = False,
) -> tuple[dict[int, TrackGesture], list[TrackGesture]]:
    """One gesture per track, bound to the pad showing it in `view`.

    A gesture exists for **every** track, not just the visible eight: a
    banked-off track keeps playing, keeps receiving engine state, and keeps its
    pending intent. Only its pad binding goes away (note=None), and comes back
    on the next bank change. The returned by-note map covers the current bank
    only and is rebuilt by `apply_view()`.
    """
    view = view or DEFAULT_VIEW
    gestures: list[TrackGesture] = []
    for loop_i in range(num_loops):
        fs = TrackGesture(
            loop=loop_i,
            hold_ms=hold_ms,
            debounce_ms=debounce_ms,
            hold_blink_start_ms=hold_blink_start_ms,
            num_loops=num_loops,
            quantized=quantized,
            grid=grid,
            on_grid_established=on_grid_established,
            on_phase_reanchor=on_phase_reanchor,
            on_grid_dropped=on_grid_dropped,
            on_tail_change=on_tail_change,
            multigrid=multigrid,
        )
        pad = view.note_for_loop(loop_i)
        fs.bind(osc, compositor, pad)
        gestures.append(fs)
    return notes_for_view(gestures, view), gestures


def notes_for_view(
    gestures: list[TrackGesture], view: GridView
) -> dict[int, TrackGesture]:
    """Pad note -> gesture, for the tracks visible in `view`."""
    by_note: dict[int, TrackGesture] = {}
    for fs in gestures:
        note = view.note_for_loop(fs.loop)
        if note is not None:
            by_note[note] = fs
    return by_note


def apply_view(
    compositor,
    *,
    gestures: list[TrackGesture],
    view: GridView,
    multigrid: bool = False,
) -> dict[int, TrackGesture]:
    """Move the viewport: rebind pads, repaint the clip row. New by-note map.

    The clip row is submitted as the gesture layer's WHOLE opinion — every pad
    dark, then each visible gesture's colour over it — so a pad left lit by the
    previous bank cannot survive. That mattered: a pad still lit after a bank
    change is a track the player believes is running and isn't, and it is the
    one failure of this feature they cannot debug from the surface. It used to
    be handled by sending eight explicit OFFs and then eight colours, sixteen
    messages down a 31.25 kbaud link; the compositor diffs the result and sends
    only the pads that actually changed.

    When ``multigrid`` is on the gesture layer says nothing at all: the matrix
    is `SlotSurface`'s, all eight rows of it, and a blank submitted here would
    be a second writer to the row this branch exists to give one owner.
    """
    if not multigrid:
        compositor.replace(LAYER_GESTURE, {
            pad_note(row, col): LED_OFF for row, col in all_clip_pads()
        })
    for fs in gestures:
        fs.release_pad()
        fs.set_note(view.note_for_loop(fs.loop))
        if not multigrid:
            fs._sync_led()
    return notes_for_view(gestures, view)


def gestures_by_loop(gestures: list[TrackGesture]) -> dict[int, TrackGesture]:
    return {fs.loop: fs for fs in gestures}


def reset_all_loops(
    osc,
    compositor,
    *,
    num_loops: int,
    gestures: list[TrackGesture],
) -> None:
    """Stop playback and clear every loop; reset bench LED/state.

    Also drops the grid: with no clips left there is no tempo, so the next
    take defines it again — same as a fresh session.
    """
    for fs in gestures:
        if fs.grid is not None:
            fs.grid.reset()
            if fs._on_grid_dropped is not None:
                fs._on_grid_dropped()
            break
    for loop in range(num_loops):
        # pause_on, never pause: `pause` is a TOGGLE, so it *starts* an already
        # paused loop. Reset would then leave half the grid running — the same
        # root error DECISIONS records for trigger and mute.
        osc.send_message(f"/sl/{loop}/hit", "pause_on")
        osc.send_message(f"/sl/{loop}/hit", "undo_all")
    for fs in gestures:
        fs.expect_cleared()
    # Every loop is empty, so the gesture layer's whole opinion of the clip row
    # is "dark". Under multigrid it has no opinion at all and `SlotSurface`
    # repaints from the runtime it just reset.
    compositor.replace(LAYER_GESTURE, {
        pad_note(row, col): LED_OFF for row, col in all_clip_pads()
    } if not gestures or not gestures[0]._multigrid else {})
    print(f"-> track reset: cleared {num_loops} loops", flush=True)


#: How long to wait before believing SL about what Stop All achieved.
#:
#: SL pushes state asynchronously, so reading `fs.sl_state` in the same breath
#: as the pause returns the PRE-stop value and would confirm whatever was
#: already there. One second is far longer than the observed update latency and
#: still inside the window where a spurious restart shows up.
STOP_ALL_VERIFY_S: float = 1.0

#: What "stopped" is allowed to look like after Stop All.
#: OFF is an empty loop, OFF_MUTED an empty loop after global mute, PAUSED a
#: loop with audio that is holding position.
STOPPED_STATES = frozenset({SL_STATE_OFF, SL_STATE_OFF_MUTED, SL_STATE_PAUSED})


def verify_stop_all(gestures: list["TrackGesture"], *, log=log) -> list[tuple[int, int]]:
    """Report what Stop All ACTUALLY achieved. Returns the loops still active.

    The counterpart to the request `stop_all_loops` sends. Separated in time
    because SL's state arrives by push, so the honest answer does not exist yet
    when the commands go out.

    Reported 2026-08-30: clips restarting 5-10s after Stop All. The only
    evidence was a log line that said "paused 15 loops" unconditionally, so
    "they never stopped" and "they stopped and something restarted them" were
    indistinguishable. This makes them distinguishable, which is the whole
    point -- it is an instrument, not a fix.
    """
    still_active = [
        (fs.loop, fs.sl_state)
        for fs in gestures
        if fs.sl_state not in STOPPED_STATES
    ]
    if still_active:
        detail = ", ".join(f"loop {i} state={st}" for i, st in still_active)
        log(f"stop all VERIFY: {len(still_active)} loop(s) did NOT stop -- {detail}")
    else:
        log(f"stop all VERIFY: all {len(gestures)} loops stopped")
    return still_active


def stop_all_loops(
    osc,
    *,
    num_loops: int,
    gestures: list[TrackGesture],
) -> float:
    """Pause every loop without clearing audio; LEDs -> stopped (yellow).

    Returns the monotonic time at which `verify_stop_all` should be called to
    find out whether it actually worked.

    Nothing is playing now, so the grid position resets to zero: the next clip
    launched starts from the top of the bar instead of joining a cycle that has
    been running unheard.
    """
    grid = next((fs.grid for fs in gestures if fs.grid is not None), None)
    grid_active = bool(grid is not None and grid.established and grid.bpm)

    # Stop All is NOT quantized. Per-clip stop waits for the bar because it is
    # a musical edit; Stop All is a transport action — when you hit it you want
    # silence now, not at the end of the bar.
    #
    # mute_quantized is lifted for the duration, then restored, so the per-clip
    # behaviour is untouched. SL drains its non-realtime queue in order, so the
    # restore cannot overtake the mute.
    #
    # `trigger` is what REWINDS. Without it `pause_on` freezes every loop
    # wherever it happened to be, and the launch path (`pause_off` + `trigger`)
    # then resumes from that stored position — so Stop All followed by a
    # restart came back mid-loop instead of from the top, and the loops came
    # back at different phases from each other. MEASURED 2026-08-30 with four
    # loops stopped: pos 3.719/8.052 (46%), 3.731/8.052 (46%), 11.783/16.104
    # (73%), 3.719/16.104 (23%).
    #
    # quantize is lifted alongside mute_quantized because a quantized trigger
    # is DEFERRED to the next cycle, which here would rewind a loop up to a
    # full cycle after the player asked for silence.
    #
    # NOTE, and it corrects a comment in sl_grid_sync.set_grid_active: trigger
    # DOES lift a pause. MEASURED 2026-08-30 — a loop in state 14 (Paused),
    # sent `trigger` with quantize at 0, went to state 4 (Playing) from
    # position 0. The earlier "verified: a paused loop stays Paused through
    # trigger" was almost certainly read with quantize at CYCLE, where the
    # trigger is merely deferred. A deferred trigger and an ignored trigger
    # look identical from outside, which is the reading-the-same-either-way
    # shape this project keeps paying for.
    osc.send_message("/sl/-1/set", ["mute_quantized", 0.0])
    osc.send_message("/sl/-1/set", ["quantize", 0.0])
    osc.send_message("/sl/-1/hit", "mute_on")
    osc.send_message("/sl/-1/hit", "trigger")
    osc.send_message("/sl/-1/hit", "pause_on")
    # Back to what the grid says this loop should be, NOT unconditionally 1.0:
    # with no grid established every loop is deliberately free-form and a
    # quantize of 1 here would sync the take that is supposed to DEFINE the
    # grid to a cycle inherited from the previous session.
    osc.send_message("/sl/-1/set", ["quantize", 1.0 if grid_active else 0.0])
    osc.send_message("/sl/-1/set", ["mute_quantized", 1.0])
    if grid_active:
        # Through the one seam. This was a raw `/set tempo` with the phase mark
        # hand-paired beside it — a fourth copy of the three lines, and the one
        # that also skipped `smart_eighths` and `eighth_per_cycle`. Harmless
        # while those happen to still hold from establishment, which is exactly
        # the "inferred from something that happens to be true right now" shape
        # `looper-timing-model-spec.md` §7 names as the root of every bug here.
        #
        # `arm_loops=False`: Stop All "resets the grid phase to zero, and keeps
        # the grid" (spec §5). It is not an establishment and must not re-arm.
        apply_established_grid(
            osc.send_message,
            grid,
            num_loops=num_loops,
            now=time.monotonic(),
            arm_loops=False,
        )
        log(f"grid position reset to zero ({grid.bpm:.3f} BPM)")
    for fs in gestures:
        fs.awaiting_quantize = False
        # OFF_MUTED (20) is idle/empty after global mute — not a clip to stop.
        # Treating it like active set pending=stopped on every empty pad (yellow
        # blink storm on the second Stop All in a session). Pi log 2026-08-19.
        if fs.sl_state not in (SL_STATE_OFF, SL_STATE_OFF_MUTED):
            fs._expect(STATE_STOPPED)
        fs._sync_led()
    # NOT "paused 15 loops". This print used to claim the outcome while only
    # ever having sent the request -- it read identically whether every loop
    # paused, some paused, or none did, which is the one bug shape this
    # project keeps paying for. It now says what it DID, and the truth is
    # reported a moment later by `verify_stop_all` once SL's own state
    # updates have arrived.
    print(f"-> stop all: pause requested for {num_loops} loops", flush=True)
    return time.monotonic() + STOP_ALL_VERIFY_S
