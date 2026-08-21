"""Per-loop APC footswitch state + 16-pad grid wiring."""

from __future__ import annotations

import os
import time

from apc_grid import DEFAULT_VIEW, GridView, all_clip_pads, pad_note
# LED constants are re-exported: the bench and its tests reach for them here,
# and the pad surface is this module's job even though the policy is not.
from led_table import (  # noqa: F401
    LED_GREEN,
    LED_GREEN_BLINK,
    LED_OFF,
    LED_RED,
    LED_RED_BLINK,
    LED_YELLOW,
    LED_YELLOW_BLINK,
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
from sl_grid_sync import (
    GRID_ANCHOR_FALLBACK_CYCLES,
    GRID_ANCHOR_MAX_S,
    TAIL_CAPTURE_ENABLED,
    TAIL_HOLD_S,
    TAIL_MAX_S,
    TAIL_ABSOLUTE_MAX_S,
    TAIL_THRESH,
    detect_loop_wrap,
    should_defer_phase_anchor,
)
from sl_seam_weld import SCRATCH_LOOP, SEAM_WELD_ENABLED
from sl_loop_states import (
    ACTIVE_PLAY,
    ACTIVE_RECORD,
    SL_STATE_OFF,
    SL_STATE_OFF_MUTED,
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


def log(msg: str) -> None:
    """Timestamped bench log. Untimed lines made a 2 s quantize wait invisible."""
    print(f"[{time.strftime('%H:%M:%S')}.{int(time.time() % 1 * 1000):03d}] {msg}", flush=True)


def poll_footswitches(footswitches: list[LoopFootswitch]) -> None:
    """Periodic bench poll — holds, LED transitions, tail capture completion.

    Tail capture only finishes inside ``poll_tail_capture()`` (peak quiet / max
    timeouts). If this is not called every idle tick, defining-take stop leaves
    ``_tail_capture`` set forever and the pad keeps the green/red animation.
    """
    for fs in footswitches:
        fs.poll_hold()
        fs.poll_led()
        fs.poll_tail_capture()


def _osc_send(osc, path: str, args: list) -> None:
    osc.send_message(path, args)


class LoopFootswitch:
    def __init__(
        self,
        *,
        loop: int,
        hold_ms: float,
        debounce_ms: float,
        num_loops: int = 16,
        quantized: bool = True,
        grid: GridState | None = None,
        on_grid_established=None,
        on_phase_reanchor=None,
        on_grid_dropped=None,
        on_tail_capture_begin=None,
        on_tail_capture_end=None,
    ) -> None:
        self.loop = loop
        self.grid = grid
        self._on_grid_established = on_grid_established
        self._on_phase_reanchor = on_phase_reanchor
        self._on_grid_dropped = on_grid_dropped
        self._on_tail_capture_begin = on_tail_capture_begin
        self._on_tail_capture_end = on_tail_capture_end
        self.loop_len = 0.0
        self.loop_pos = 0.0
        self._loop_pos_seen = False
        self._last_loop_pos = 0.0
        self._phase_reanchor_at = 0.0
        # Is this loop waiting for cycle boundaries? False in free-form, where
        # arming a quantize wait strands the pad on a boundary that never comes.
        self.quantized = quantized
        self.num_loops = num_loops
        self.hold_s = hold_ms / 1000.0
        self.debounce_s = debounce_ms / 1000.0
        self._pending: str | None = None
        self._pending_since = 0.0
        self._osc = None
        self._midi_out = None
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
        self._led_last: int | None = None
        self._tail_capture = False
        self._tail_capture_since = 0.0
        self._tail_silence_since: float | None = None
        self._in_peak = 0.0
        self._in_peak_seen = False
        self._tail_saw_loud = False
        self._tail_stop_sent = False
        self._tail_deferred = False
        self._scratch_active = False
        self._tail_ending = False
        self._merge_pending = False
        self._deferred_grid_clock: tuple[float, int] | None = None
        self._on_prepare_scratch: callable | None = None
        self._on_start_scratch: callable | None = None
        self._on_stop_scratch: callable | None = None
        self._on_request_seam_merge: callable | None = None

    def bind(self, osc, midi_out, note: int | None) -> None:
        self._osc = osc
        self._midi_out = midi_out
        self._note = note

    def set_note(self, note: int | None) -> None:
        """Move this track to a different pad, or off-screen (None).

        Banking does not change what a track *is* — only where, or whether, it
        is drawn. `_led_last` is cleared so the next paint is unconditional:
        the pad this track lands on was showing some other track a moment ago,
        and the "same velocity, skip the write" guard would otherwise leave the
        previous track's colour sitting there.
        """
        self._note = note
        self._led_last = None

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

    def sync_in_peak(self, peak: float) -> None:
        self._in_peak = max(0.0, float(peak))
        self._in_peak_seen = True
        if self._tail_capture and self._in_peak >= TAIL_THRESH:
            self._tail_saw_loud = True

    def _stop_scratch_capture(self) -> None:
        if not self._scratch_active:
            return
        self._scratch_active = False
        if self._on_stop_scratch is not None:
            self._on_stop_scratch(self.loop)
        log(f"loop {self.loop}: scratch tail record stopped (loop {SCRATCH_LOOP})")

    def _maybe_start_scratch(self) -> None:
        """Parallel tail capture on scratch loop while main plays at fixed length."""
        if (
            self._scratch_active
            or self._tail_ending
            or self._merge_pending
            or not self._tail_stop_sent
            or not self._tail_capture
        ):
            return
        if not self._tail_playback_ready():
            return
        if not SEAM_WELD_ENABLED or self._on_start_scratch is None:
            return
        self._scratch_active = True
        log(
            f"loop {self.loop}: scratch tail record on loop {SCRATCH_LOOP} "
            f"(pos={self.loop_pos:.3f}s / {self.loop_len:.3f}s)"
        )
        self._on_start_scratch(self.loop)

    def _cancel_tail_capture(self) -> None:
        if self._tail_capture and self._on_tail_capture_end is not None:
            self._on_tail_capture_end(self.loop)
        self._stop_scratch_capture()
        self._tail_ending = False
        self._merge_pending = False
        had_deferred = self._deferred_grid_clock is not None or self._phase_reanchor_at > 0.0
        self._tail_capture = False
        self._tail_capture_since = 0.0
        self._tail_silence_since = None
        self._tail_saw_loud = False
        self._tail_stop_sent = False
        self._tail_deferred = False
        if had_deferred:
            self._flush_deferred_grid_side_effects()

    def _flush_deferred_grid_side_effects(self) -> None:
        """Apply grid clock + phase re-anchor after tail weld — not during it.

        establish_grid_clock resets phase; doing that while scratch capture or
        merge is in flight can bake a stutter into the loop buffer.
        """
        if self._deferred_grid_clock is not None and self._on_grid_established is not None:
            bpm, bars = self._deferred_grid_clock
            self._deferred_grid_clock = None
            log(
                f"loop {self.loop}: applying deferred grid clock — "
                f"{bars} bar(s) @ {bpm:.1f} BPM"
            )
            self._on_grid_established(bpm, bars)
        if self._phase_reanchor_at > 0.0:
            self._try_commit_phase_reanchor(force_wrap=True)

    def _finish_tail_capture(self, reason: str) -> None:
        if not self._tail_capture:
            return
        self._tail_capture = False
        self._tail_capture_since = 0.0
        self._tail_silence_since = None
        self._tail_saw_loud = False
        self._tail_stop_sent = False
        self._tail_deferred = False
        self._scratch_active = False
        self._tail_ending = False
        self._merge_pending = False
        if self._on_tail_capture_end is not None:
            self._on_tail_capture_end(self.loop)
        self._pad_down = False
        self._pad_down_at = 0.0
        self._hold_fired = False
        log(f"loop {self.loop}: seam weld done ({reason})")
        self._flush_deferred_grid_side_effects()
        self._sync_led()
        self._mark_action()

    def _should_seam_merge(self) -> bool:
        """Only merge when release was audible — empty scratch has nothing to weld."""
        if not SEAM_WELD_ENABLED or not self._tail_stop_sent:
            return False
        if not self._tail_saw_loud:
            return False
        return self._on_request_seam_merge is not None

    def _end_tail_capture(self, reason: str) -> None:
        """Stop scratch capture and optionally run Tier 3 merge before finish."""
        if self._tail_ending or not self._tail_capture:
            return
        self._tail_ending = True
        if self._scratch_active:
            self._stop_scratch_capture()
        if self._should_seam_merge():
            log(f"loop {self.loop}: seam merge queued ({reason})")
            self._merge_pending = True
            accepted = self._on_request_seam_merge(
                self.loop,
                lambda: self._after_seam_merge(reason),
            )
            if not accepted:
                log(
                    f"loop {self.loop}: seam merge declined — finishing without reload"
                )
                self._merge_pending = False
                self._finish_tail_capture(reason)
            return
        if self._tail_saw_loud:
            log(
                f"loop {self.loop}: seam merge skipped ({reason}) — "
                f"no merge hook or SEAM_WELD off"
            )
        self._finish_tail_capture(reason)

    def _after_seam_merge(self, reason: str) -> None:
        self._merge_pending = False
        self._finish_tail_capture(reason)

    def set_tail_capture_hooks(
        self,
        on_begin,
        on_end,
    ) -> None:
        """Subscribe/unsubscribe in_peak_meter during defining-take tail capture."""
        self._on_tail_capture_begin = on_begin
        self._on_tail_capture_end = on_end

    def set_seam_weld_hooks(
        self,
        on_prepare_scratch,
        on_start_scratch,
        on_stop_scratch,
        on_request_merge,
    ) -> None:
        """Tier 3: scratch loop capture + offline seam merge."""
        self._on_prepare_scratch = on_prepare_scratch
        self._on_start_scratch = on_start_scratch
        self._on_stop_scratch = on_stop_scratch
        self._on_request_seam_merge = on_request_merge

    def _tail_playback_ready(self) -> bool:
        return self.sl_state in ACTIVE_PLAY

    def poll_tail_capture(self) -> None:
        """Stop-then-weld: fixed loop length + parallel scratch + offline merge."""
        if not self._tail_capture:
            return
        now = time.monotonic()
        elapsed = now - self._tail_capture_since
        if elapsed >= TAIL_ABSOLUTE_MAX_S:
            self._end_tail_capture(f"absolute max {TAIL_ABSOLUTE_MAX_S:.2f}s")
            return
        if self._in_peak_seen and self._in_peak >= TAIL_THRESH:
            self._tail_saw_loud = True
            self._tail_silence_since = None

        if self._tail_deferred and not self._tail_playback_ready():
            return

        if not self._scratch_active:
            self._maybe_start_scratch()
            if not self._scratch_active and elapsed >= TAIL_MAX_S:
                self._end_tail_capture(f"max {TAIL_MAX_S:.2f}s (no scratch)")
            return

        if self._tail_ending or self._merge_pending:
            return

        if self._in_peak_seen and self._in_peak >= TAIL_THRESH:
            return
        if not self._in_peak_seen or not self._tail_saw_loud:
            if elapsed >= TAIL_MAX_S:
                self._end_tail_capture(f"max {TAIL_MAX_S:.2f}s (no release peak)")
            return
        if self._tail_silence_since is None:
            self._tail_silence_since = now
            return
        if (now - self._tail_silence_since) >= TAIL_HOLD_S:
            self._end_tail_capture(
                f"peak<{TAIL_THRESH} for {TAIL_HOLD_S * 1000:.0f}ms"
            )

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

        if sl_state == SL_STATE_PLAYING:
            self._maybe_establish_grid()
            if self._tail_capture and self._tail_stop_sent:
                self._maybe_start_scratch()

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
            self._sync_led()
            return True
        return False

    def sync_loop_len(self, loop_len: float) -> None:
        self.loop_len = float(loop_len)
        # Engine truth only: a length that arrives while we merely *expect*
        # playback would derive a tempo from a take that never landed.
        if self.sl_state == SL_STATE_PLAYING:
            self._maybe_establish_grid()
        elif self._phase_reanchor_at > 0.0:
            self._try_commit_phase_reanchor()
        if self._tail_capture and self._tail_stop_sent:
            self._maybe_start_scratch()

    def sync_loop_pos(self, loop_pos: float) -> None:
        pos = float(loop_pos)
        if self._loop_pos_seen and detect_loop_wrap(
            self._last_loop_pos, pos, self.loop_len
        ):
            if not self._tail_capture:
                self._try_commit_phase_reanchor(force_wrap=True)
        self._last_loop_pos = self.loop_pos if self._loop_pos_seen else pos
        self.loop_pos = pos
        self._loop_pos_seen = True
        if self._phase_reanchor_at > 0.0 and not self._tail_capture:
            self._try_commit_phase_reanchor()

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
            if self._tail_capture:
                self._deferred_grid_clock = (bpm, bars)
                log(
                    f"loop {self.loop}: grid clock deferred until tail weld completes"
                )
            else:
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
            if not self._tail_capture:
                self._try_commit_phase_reanchor()

    def _try_commit_phase_reanchor(self, *, force_wrap: bool = False) -> None:
        if self._tail_capture:
            return
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

    def _set_led(self, velocity: int, *, force: bool = False) -> None:
        if self._midi_out is None or self._note is None:
            return  # banked off-screen: this track has no pad to paint
        velocity = max(0, min(127, velocity))
        if not force and velocity == self._led_last:
            return  # do not spam the surface every poll
        self._led_last = velocity
        self._midi_out.send_message([0x90, self._note, velocity])

    def _led_target(self) -> tuple[int, ...]:
        return led_for(
            self.sl_state,
            pending=self._pending,
            tail_capture=self._tail_capture,
        )

    def _sync_led(self) -> None:
        """Paint the pad from engine truth plus unconfirmed intent.

        All the policy lives in `led_for`; this just applies the result. A
        one-element sequence is a steady colour, anything longer animates and
        `poll_led` drives it from here.
        """
        seq = self._led_target()
        if len(seq) > 1:
            self._led_transition = seq
            return
        self._led_transition = None
        self._set_led(seq[0], force=True)

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
        self._cancel_tail_capture()
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

    def _gesture(self, edge: str) -> None:
        if self._tail_capture:
            if edge == "down":
                self._cancel_tail_capture()
                self._mark_action()
            return
        if self._debounced():
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
            tail_capture_enabled=TAIL_CAPTURE_ENABLED,
        )
        if not (plan.commands or plan.queue_stop or plan.arm_grid or plan.begin_tail_capture):
            return
        if plan.note:
            log(f"loop {self.loop}: {plan.note}")
        if plan.arm_grid and self.grid is not None:
            self.grid.arm(self.loop)
        if plan.begin_tail_capture:
            self._tail_capture = True
            self._tail_capture_since = time.monotonic()
            self._tail_silence_since = None
            self._in_peak = 0.0
            self._in_peak_seen = False
            self._tail_saw_loud = False
            self._scratch_active = False
            self._tail_deferred = plan.tail_deferred
            self._tail_stop_sent = True
            if plan.commands:
                for cmd in plan.commands:
                    self._hit(cmd)
            elif self.sl_state in ACTIVE_RECORD:
                self._hit("record")
            if self._on_prepare_scratch is not None:
                self._on_prepare_scratch(self.loop)
            self._expect(STATE_PLAYING)
            if self._on_tail_capture_begin is not None:
                self._on_tail_capture_begin(self.loop)
            self._sync_led()
            self._mark_action()
            log(
                f"loop {self.loop}: -> {edge} done (stop-then-weld, "
                f"state={self.state}, sl_state={self.sl_state})"
            )
            return
        for cmd in plan.commands:
            self._hit(cmd)
        if plan.queue_stop:
            self._stop_queued = True
        if plan.begin_quantize_wait:
            self._begin_quantize_wait()
        self._expect(plan.expect)

        self._sync_led()
        self._mark_action()
        log(f"loop {self.loop}: -> {edge} done (state={self.state}, sl_state={self.sl_state})")

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
        """Drive the transition blink. Cheap no-op unless one is active."""
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
        log(f"loop {self.loop}: -> hold clear")
        self._clear_loop()


def build_footswitches(
    *,
    osc,
    midi_out,
    num_loops: int,
    hold_ms: float,
    debounce_ms: float,
    quantized: bool = True,
    grid: GridState | None = None,
    view: GridView | None = None,
    on_grid_established=None,
    on_phase_reanchor=None,
    on_grid_dropped=None,
) -> tuple[dict[int, LoopFootswitch], list[LoopFootswitch]]:
    """One footswitch per track, bound to the pad showing it in `view`.

    A footswitch exists for **every** track, not just the visible eight: a
    banked-off track keeps playing, keeps receiving engine state, and keeps its
    pending intent. Only its pad binding goes away (note=None), and comes back
    on the next bank change. The returned by-note map covers the current bank
    only and is rebuilt by `apply_view()`.
    """
    view = view or DEFAULT_VIEW
    footswitches: list[LoopFootswitch] = []
    for loop_i in range(num_loops):
        fs = LoopFootswitch(
            loop=loop_i,
            hold_ms=hold_ms,
            debounce_ms=debounce_ms,
            num_loops=num_loops,
            quantized=quantized,
            grid=grid,
            on_grid_established=on_grid_established,
            on_phase_reanchor=on_phase_reanchor,
            on_grid_dropped=on_grid_dropped,
        )
        fs.bind(osc, midi_out, view.note_for_loop(loop_i))
        footswitches.append(fs)
    return notes_for_view(footswitches, view), footswitches


def notes_for_view(
    footswitches: list[LoopFootswitch], view: GridView
) -> dict[int, LoopFootswitch]:
    """Pad note -> footswitch, for the tracks visible in `view`."""
    by_note: dict[int, LoopFootswitch] = {}
    for fs in footswitches:
        note = view.note_for_loop(fs.loop)
        if note is not None:
            by_note[note] = fs
    return by_note


def apply_view(
    midi_out,
    *,
    footswitches: list[LoopFootswitch],
    view: GridView,
) -> dict[int, LoopFootswitch]:
    """Move the viewport: clear the clip row, rebind pads, repaint. New by-note map.

    Clearing the whole row first — rather than only the pads that changed —
    is deliberate. Whatever the arithmetic says, a pad left lit by the previous
    bank is a track the player believes is running and isn't, and that is the
    one failure of this feature they cannot debug from the surface. One sweep
    of eight notes costs nothing and makes it impossible.
    """
    for row, col in all_clip_pads():
        midi_out.send_message([0x90, pad_note(row, col), LED_OFF])
    for fs in footswitches:
        fs.release_pad()
        fs.set_note(view.note_for_loop(fs.loop))
        fs._sync_led()
    return notes_for_view(footswitches, view)


def footswitches_by_loop(footswitches: list[LoopFootswitch]) -> dict[int, LoopFootswitch]:
    return {fs.loop: fs for fs in footswitches}


def reset_all_loops(
    osc,
    midi_out,
    *,
    num_loops: int,
    footswitches: list[LoopFootswitch],
) -> None:
    """Stop playback and clear every loop; reset bench LED/state.

    Also drops the grid: with no clips left there is no tempo, so the next
    take defines it again — same as a fresh session.
    """
    for fs in footswitches:
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
    for fs in footswitches:
        fs.awaiting_quantize = False
        fs._stop_queued = False
        fs._cancel_tail_capture()
        fs._led_transition = None
        fs._expect(STATE_IDLE)
        fs._sync_led()
    for row, col in all_clip_pads():
        midi_out.send_message([0x90, pad_note(row, col), LED_OFF])
    print(f"-> track reset: cleared {num_loops} loops", flush=True)


def stop_all_loops(
    osc,
    *,
    num_loops: int,
    footswitches: list[LoopFootswitch],
) -> None:
    """Pause every loop without clearing audio; LEDs -> stopped (yellow).

    Nothing is playing now, so the grid position resets to zero: the next clip
    launched starts from the top of the bar instead of joining a cycle that has
    been running unheard.
    """
    # Stop All is NOT quantized. Per-clip stop waits for the bar because it is
    # a musical edit; Stop All is a transport action — when you hit it you want
    # silence now, not at the end of the bar.
    #
    # mute_quantized is lifted for the duration, then restored, so the per-clip
    # behaviour is untouched. SL drains its non-realtime queue in order, so the
    # restore cannot overtake the mute.
    for fs in footswitches:
        fs._cancel_tail_capture()
    osc.send_message("/sl/-1/set", ["mute_quantized", 0.0])
    osc.send_message("/sl/-1/hit", "mute_on")
    osc.send_message("/sl/-1/hit", "pause_on")
    osc.send_message("/sl/-1/set", ["mute_quantized", 1.0])
    grid = next((fs.grid for fs in footswitches if fs.grid is not None), None)
    if grid is not None and grid.established and grid.bpm:
        osc.send_message("/set", ["tempo", float(grid.bpm)])  # zeroes the phase
        log(f"grid position reset to zero ({grid.bpm:.3f} BPM)")
    for fs in footswitches:
        fs.awaiting_quantize = False
        # OFF_MUTED (20) is idle/empty after global mute — not a clip to stop.
        # Treating it like active set pending=stopped on every empty pad (yellow
        # blink storm on the second Stop All in a session). Pi log 2026-08-19.
        if fs.sl_state not in (SL_STATE_OFF, SL_STATE_OFF_MUTED) or fs._tail_capture:
            fs._expect(STATE_STOPPED)
        fs._sync_led()
    print(f"-> stop all: paused {num_loops} loops", flush=True)
