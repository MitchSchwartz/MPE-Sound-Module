"""Per-loop APC gesture state + pad grid wiring (8 visible of 15 tracks)."""

from __future__ import annotations

import os
import time

from apc_grid import DEFAULT_VIEW, GridView, all_clip_pads, pad_note
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
from sl_grid_sync import (
    GRID_ANCHOR_FALLBACK_CYCLES,
    GRID_ANCHOR_MAX_S,
    RING_OUT_ENABLED,
    detect_loop_wrap,
    should_defer_phase_anchor,
)
from sl_loop_states import (
    ACTIVE_PLAY,
    ACTIVE_RECORD,
    SL_STATE_OFF,
    SL_STATE_OFF_MUTED,
    SL_STATE_OVERDUBBING,
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


def poll_track_gestures(gestures: list[TrackGesture], *, multigrid: bool = False) -> None:
    """Periodic bench poll — holds and LED transitions.

    When ``multigrid`` is on, skip gesture hold (``SlotSurface`` owns hold-
    clear) but still advance blink phase — ``SlotSurface.repaint`` reads
    ``current_led()``.
    """
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
        multigrid: bool = False,
    ) -> None:
        self.loop = loop
        self.grid = grid
        self._multigrid = multigrid
        self._on_grid_established = on_grid_established
        self._on_phase_reanchor = on_phase_reanchor
        self._on_grid_dropped = on_grid_dropped
        self.loop_len = 0.0
        self.loop_pos = 0.0
        self._loop_pos_seen = False
        # True while an overdub started by closing a take is still
        # running. Cleared at the first wrap — one pass of ring-out.
        self._overdub_pass = False
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
        self._loop_pos_at = 0.0
        self._in_peak = 0.0
        self._in_peak_seen = False
        self._deferred_grid_clock: tuple[float, int] | None = None

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

    def sync_in_peak(self, peak: float) -> None:
        self._in_peak = max(0.0, float(peak))
        self._in_peak_seen = True

    def _flush_deferred_grid_side_effects(self) -> None:
        """Apply grid clock + phase re-anchor once it is safe to reset phase."""
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

        if sl_state == SL_STATE_OVERDUBBING:
            self._overdub_pass = True
        elif prev_sl == SL_STATE_OVERDUBBING:
            # Ended by the pad, or by the engine. Either way stop watching.
            self._overdub_pass = False

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
            self._end_overdub_pass()
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

    def _end_overdub_pass(self) -> None:
        """One pass of ring-out, then out of overdub.

        The take closes into an overdub at the boundary, so overdub begins at
        loop position 0 and the next wrap is exactly one pass later. Armed off
        `sl_state == OVERDUBBING` rather than off the command we sent, so an
        overdub the engine never entered cannot leave this latched.

        `loop_pos` arrives every 20 ms, so this can overrun the wrap by up to
        one update. That records a sliver of pass two on top of pass one — the
        alternative is ending early and clipping the ring-out, which is the
        artefact this whole feature exists to remove.
        """
        if not self._overdub_pass:
            return
        self._overdub_pass = False
        self._hit("overdub")
        log(f"loop {self.loop}: overdub off at wrap — one pass of ring-out")

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

    def _set_led(self, velocity: int, *, force: bool = False) -> None:
        if self._multigrid:
            return
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
        return seq[0] if seq else LED_OFF

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
    midi_out,
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
            multigrid=multigrid,
        )
        pad = view.note_for_loop(loop_i)
        fs.bind(osc, midi_out, pad)
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
    midi_out,
    *,
    gestures: list[TrackGesture],
    view: GridView,
    multigrid: bool = False,
) -> dict[int, TrackGesture]:
    """Move the viewport: clear the clip row, rebind pads, repaint. New by-note map.

    Clearing the whole row first — rather than only the pads that changed —
    is deliberate. Whatever the arithmetic says, a pad left lit by the previous
    bank is a track the player believes is running and isn't, and that is the
    one failure of this feature they cannot debug from the surface. One sweep
    of eight notes costs nothing and makes it impossible.

    When ``multigrid`` is on, do not paint row 0 from gesture state —
    ``SlotSurface.repaint`` owns the full matrix.
    """
    for row, col in all_clip_pads():
        midi_out.send_message([0x90, pad_note(row, col), LED_OFF])
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
    midi_out,
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
    for row, col in all_clip_pads():
        midi_out.send_message([0x90, pad_note(row, col), LED_OFF])
    print(f"-> track reset: cleared {num_loops} loops", flush=True)


def stop_all_loops(
    osc,
    *,
    num_loops: int,
    gestures: list[TrackGesture],
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
    osc.send_message("/sl/-1/set", ["mute_quantized", 0.0])
    osc.send_message("/sl/-1/hit", "mute_on")
    osc.send_message("/sl/-1/hit", "pause_on")
    osc.send_message("/sl/-1/set", ["mute_quantized", 1.0])
    grid = next((fs.grid for fs in gestures if fs.grid is not None), None)
    if grid is not None and grid.established and grid.bpm:
        osc.send_message("/set", ["tempo", float(grid.bpm)])  # zeroes the phase
        log(f"grid position reset to zero ({grid.bpm:.3f} BPM)")
    for fs in gestures:
        fs.awaiting_quantize = False
        # OFF_MUTED (20) is idle/empty after global mute — not a clip to stop.
        # Treating it like active set pending=stopped on every empty pad (yellow
        # blink storm on the second Stop All in a session). Pi log 2026-08-19.
        if fs.sl_state not in (SL_STATE_OFF, SL_STATE_OFF_MUTED):
            fs._expect(STATE_STOPPED)
        fs._sync_led()
    print(f"-> stop all: paused {num_loops} loops", flush=True)
