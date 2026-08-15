"""Per-loop APC footswitch state + 16-pad grid wiring."""

from __future__ import annotations

import os
import time

from apc_grid import all_loop_pads, pad_note
from sl_grid_state import GridState
from sl_loop_states import (
    QUANTIZE_WAIT,
    SL_STATE_OFF,
    SL_STATE_PAUSED,
    SL_STATE_PLAYING,
    SL_STATE_RECORDING,
    SL_STATE_WAIT_START,
    SL_STATE_WAIT_STOP,
)

STATE_IDLE = "idle"
STATE_RECORDING = "recording"
STATE_PLAYING = "playing"
STATE_STOPPED = "stopped"

LED_OFF = 0
LED_GREEN = 1
LED_GREEN_BLINK = 2
LED_RED = 3
LED_RED_BLINK = 4
LED_YELLOW = 5
LED_YELLOW_BLINK = 6

# Blink = "queued, lands on the next bar" — the clip-launcher idiom.
#
# A quantized action does not take effect until the next cycle boundary (up to
# one full bar later). Without a distinct armed state the pad looks identical
# before and after the tap, so the player reads it as "my press did nothing",
# presses again, or holds — and holding clears the loop they just recorded.
# That cost an evening on 2026-08-14. Solid = it happened. Blink = it is coming.

# A quantized action waits for the next cycle boundary. If no boundary arrives
# within this long, the grid clock is not running — release the pad rather than
# leaving it dead, and say so. See spec §J: a silent latch here cost an evening.
QUANTIZE_WAIT_TIMEOUT_S = float(os.environ.get("MPE_SL_QUANTIZE_TIMEOUT_S", "6.0"))

# Transition blink: alternate FROM-colour and TO-colour, half a period each.
#
# Ableton has no "queued to stop recording" state — it shows triggered_to_play
# (green blink) and drops the fact that recording is still running. On a looper
# that matters: you are performing into that bar and need to know to keep
# playing. Alternating red/green says "recording -> playing" outright.
#
# Applied ONLY where the ambiguity is real. Stopped -> playing is already
# unambiguous as a plain green blink and stays Ableton-standard.
TRANSITION_BLINK_S = float(os.environ.get("MPE_APC_TRANSITION_BLINK_S", "0.25"))


def log(msg: str) -> None:
    """Timestamped bench log. Untimed lines made a 2 s quantize wait invisible."""
    print(f"[{time.strftime('%H:%M:%S')}.{int(time.time() % 1 * 1000):03d}] {msg}", flush=True)


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
        on_grid_dropped=None,
    ) -> None:
        self.loop = loop
        self.grid = grid
        self._on_grid_established = on_grid_established
        self._on_grid_dropped = on_grid_dropped
        self.loop_len = 0.0
        # Is this loop waiting for cycle boundaries? False in free-form, where
        # arming a quantize wait strands the pad on a boundary that never comes.
        self.quantized = quantized
        self.num_loops = num_loops
        self.hold_s = hold_ms / 1000.0
        self.debounce_s = debounce_ms / 1000.0
        self.state = STATE_IDLE
        self._osc = None
        self._midi_out = None
        self._note = 0
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

    def bind(self, osc, midi_out, note: int) -> None:
        self._osc = osc
        self._midi_out = midi_out
        self._note = note

    def sync_from_sl(self, sl_state: int) -> bool:
        """Mirror SooperLooper state → bench LED (all loops incl. 0)."""
        prev_sl = self.sl_state
        self.sl_state = sl_state
        before = self.state
        if sl_state == SL_STATE_PLAYING:
            self.awaiting_quantize = False
            self.state = STATE_PLAYING
            self._maybe_establish_grid()
        elif sl_state == SL_STATE_PAUSED:
            self.awaiting_quantize = False
            self.state = STATE_STOPPED
        elif sl_state == SL_STATE_OFF:
            self.awaiting_quantize = False
            self.state = STATE_IDLE
        elif sl_state in QUANTIZE_WAIT:
            self.state = STATE_RECORDING
            # Hold taps only after stop-record is sent (WAIT_STOP); during the
            # arm phase (WAIT_START) a tap means cancel and must reach SL. The
            # hold is time-bounded either way — see _waiting_for_quantize.
            if sl_state == SL_STATE_WAIT_STOP:
                if not self.awaiting_quantize:
                    self._begin_quantize_wait()
            else:
                self.awaiting_quantize = False
        elif sl_state == SL_STATE_RECORDING:
            self.awaiting_quantize = False
            self.state = STATE_RECORDING
            if self._stop_queued:
                # Recording just began. Send the stop now; SL quantizes it to
                # the next boundary, giving exactly one cycle of audio.
                self._stop_queued = False
                self._hit("record")
                self._begin_quantize_wait()
        if self.grid is not None:
            if self.grid.note_loop_content(self.loop, sl_state != SL_STATE_OFF):
                log(f"loop {self.loop}: last clip cleared — grid dropped, "
                    f"next take defines a new one")
                if self._on_grid_dropped is not None:
                    self._on_grid_dropped()
        changed = before != self.state or prev_sl != sl_state
        if changed:
            log(f"loop {self.loop}: SL sync sl={sl_state} bench={self.state}")
            self._sync_led()
            return True
        return False

    def sync_loop_len(self, loop_len: float) -> None:
        self.loop_len = float(loop_len)
        if self.state == STATE_PLAYING:
            self._maybe_establish_grid()

    def _maybe_establish_grid(self) -> None:
        """The defining take just landed — capture the tempo from its length.

        After this the grid stands alone: this clip has no special status and
        can be deleted like any other.
        """
        if self.grid is None or not self.grid.is_pending(self.loop):
            return
        if self.loop_len <= 0.0:
            return  # length not reported yet; sync_loop_len will retry
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

    def _path(self, suffix: str) -> str:
        return f"/sl/{self.loop}/{suffix}"

    def _hit(self, cmd: str) -> None:
        self._osc.send_message(self._path("hit"), cmd)
        log(f"loop {self.loop}: -> {cmd} (state={self.state})")

    def _set_led(self, velocity: int, *, force: bool = False) -> None:
        if self._midi_out is None:
            return
        velocity = max(0, min(127, velocity))
        if not force and velocity == self._led_last:
            return  # do not spam the surface every poll
        self._led_last = velocity
        self._midi_out.send_message([0x90, self._note, velocity])

    def _sync_led(self) -> None:
        # Armed states win: the player needs to see that the tap registered and
        # is queued for the next bar, or they will press again / hold and lose
        # the take. Read from SL, not bench state, so the blink reflects truth.
        if self.sl_state == SL_STATE_WAIT_STOP:
            # Still recording, playback queued: alternate red -> green so the
            # pad shows both the current state and the destination.
            self._led_transition = (LED_RED, LED_GREEN)
            return  # poll_led drives it from here
        self._led_transition = None
        if self.sl_state == SL_STATE_WAIT_START:
            self._set_led(LED_RED_BLINK, force=True)  # queued to record
        elif self.state == STATE_RECORDING:
            self._set_led(LED_RED)
        elif self.state == STATE_PLAYING:
            self._set_led(LED_GREEN, force=True)
        elif self.state == STATE_STOPPED:
            self._set_led(LED_YELLOW, force=True)
        else:
            self._set_led(LED_OFF, force=True)

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
        self.state = STATE_IDLE
        self.awaiting_quantize = False
        self.sl_state = SL_STATE_OFF
        self._sync_led()
        self._mark_action()

    def _tap(self) -> None:
        if self._debounced():
            log(f"loop {self.loop}: -> tap ignored (debounce)")
            return
        if self._waiting_for_quantize():
            log(f"loop {self.loop}: -> tap ignored (quantize wait)")
            return

        if self.state == STATE_IDLE:
            # No grid yet? This take defines it: record free-form and instant,
            # because there is no bar to count in to. Standard looper workflow.
            if self.grid is not None and not self.grid.established:
                self.grid.arm(self.loop)
                log(f"loop {self.loop}: defining the grid (free-form, no count-in)")
            self._hit("record")
            self.state = STATE_RECORDING
        elif self.state == STATE_RECORDING:
            if self.sl_state == SL_STATE_WAIT_START:
                # Armed but not recording yet. Sending `record` now would reach
                # SL as CANCEL. Queue the stop instead and fire it the moment
                # recording actually begins, so a double-tap records exactly
                # one cycle: starts on the next boundary, ends on the one after.
                self._stop_queued = True
                log(f"loop {self.loop}: stop queued — will record exactly one cycle")
                self._sync_led()
                self._mark_action()
                return
            self._hit("record")
            # Only wait for a boundary if this loop is actually quantized.
            # In free-form there is no boundary, so arming the wait swallowed
            # the next tap for QUANTIZE_WAIT_TIMEOUT_S and stranded the pad on
            # red while the loop was already playing (2026-08-14).
            defining = self.grid is not None and self.grid.is_pending(self.loop)
            if self.quantized and not defining:
                self._begin_quantize_wait()
            else:
                self.state = STATE_PLAYING
        elif self.state == STATE_PLAYING:
            # pause_on/pause_off, not the `pause` TOGGLE. A toggle desyncs the
            # moment the bench and engine disagree about the current state.
            self._hit("pause_on")
            self.state = STATE_STOPPED
        elif self.state == STATE_STOPPED:
            # `trigger` alone on a paused loop restarted it while still
            # paused: the pad went green and nothing was heard, and the next
            # press resumed from mid-loop. Lift the pause first, then restart
            # from the top like a clip launcher.
            self._hit("pause_off")
            self._hit("trigger")
            self.state = STATE_PLAYING
        else:
            self._hit("record")
            self.state = STATE_RECORDING

        self._sync_led()
        self._mark_action()
        log(f"loop {self.loop}: -> tap done (state={self.state}, sl_state={self.sl_state})")

    def on_pad_down(self) -> None:
        self._pad_down = True
        self._pad_down_at = time.monotonic()
        self._hold_fired = False
        log(f"loop {self.loop}: pad down (note {self._note})")

    def on_pad_up(self) -> None:
        held = time.monotonic() - self._pad_down_at
        log(f"loop {self.loop}: pad up held={held:.3f}s hold_fired={self._hold_fired}")
        if self._pad_down and not self._hold_fired:
            self._tap()
        self._pad_down = False

    def poll_led(self) -> None:
        """Drive the transition blink. Cheap no-op unless one is active."""
        if self._led_transition is None:
            return
        first, second = self._led_transition
        phase = int(time.monotonic() / TRANSITION_BLINK_S) % 2
        self._set_led(first if phase == 0 else second)

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
    on_grid_established=None,
    on_grid_dropped=None,
) -> tuple[dict[int, LoopFootswitch], list[LoopFootswitch]]:
    """Map APC clip-pad MIDI notes (rows 0 + 3) -> per-loop footswitch."""
    by_note: dict[int, LoopFootswitch] = {}
    footswitches: list[LoopFootswitch] = []
    for row, col, loop_i in all_loop_pads():
        if loop_i >= num_loops:
            continue
        note = pad_note(row, col)
        fs = LoopFootswitch(
            loop=loop_i,
            hold_ms=hold_ms,
            debounce_ms=debounce_ms,
            num_loops=num_loops,
            quantized=quantized,
            grid=grid,
            on_grid_established=on_grid_established,
            on_grid_dropped=on_grid_dropped,
        )
        fs.bind(osc, midi_out, note)
        by_note[note] = fs
        footswitches.append(fs)
    return by_note, footswitches


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
        osc.send_message(f"/sl/{loop}/hit", "pause")
        osc.send_message(f"/sl/{loop}/hit", "undo_all")
    for fs in footswitches:
        fs.state = STATE_IDLE
        fs.awaiting_quantize = False
        fs.sl_state = SL_STATE_OFF
        fs._sync_led()
    for row, col, loop_i in all_loop_pads():
        if loop_i >= num_loops:
            continue
        note = pad_note(row, col)
        midi_out.send_message([0x90, note, LED_OFF])
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
    osc.send_message("/sl/-1/hit", "pause_on")
    grid = next((fs.grid for fs in footswitches if fs.grid is not None), None)
    if grid is not None and grid.established and grid.bpm:
        osc.send_message("/set", ["tempo", float(grid.bpm)])  # zeroes the phase
        log(f"grid position reset to zero ({grid.bpm:.3f} BPM)")
    for fs in footswitches:
        fs.awaiting_quantize = False
        if fs.state != STATE_IDLE:
            fs.state = STATE_STOPPED
        fs._sync_led()
    print(f"-> stop all: paused {num_loops} loops", flush=True)
