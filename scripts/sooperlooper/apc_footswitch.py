"""Per-loop APC footswitch state + 16-pad grid wiring."""

from __future__ import annotations

import os
import time

from apc_grid import all_loop_pads, pad_note
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
LED_RED = 3
LED_YELLOW = 5

# A quantized action waits for the next cycle boundary. If no boundary arrives
# within this long, the grid clock is not running — release the pad rather than
# leaving it dead, and say so. See spec §J: a silent latch here cost an evening.
QUANTIZE_WAIT_TIMEOUT_S = float(os.environ.get("MPE_SL_QUANTIZE_TIMEOUT_S", "6.0"))


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
    ) -> None:
        self.loop = loop
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
        changed = before != self.state or prev_sl != sl_state
        if changed:
            print(
                f"loop {self.loop}: SL sync sl={sl_state} bench={self.state}",
                flush=True,
            )
            self._sync_led()
            return True
        return False

    def _path(self, suffix: str) -> str:
        return f"/sl/{self.loop}/{suffix}"

    def _hit(self, cmd: str) -> None:
        self._osc.send_message(self._path("hit"), cmd)
        print(f"loop {self.loop}: -> {cmd} (state={self.state})", flush=True)

    def _set_led(self, velocity: int) -> None:
        if self._midi_out is None:
            return
        self._midi_out.send_message([0x90, self._note, max(0, min(127, velocity))])

    def _sync_led(self) -> None:
        if self.state == STATE_RECORDING:
            self._set_led(LED_RED)
        elif self.state == STATE_PLAYING:
            self._set_led(LED_GREEN)
        elif self.state == STATE_STOPPED:
            self._set_led(LED_YELLOW)
        else:
            self._set_led(LED_OFF)

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
        self._hit("undo_all")
        self.state = STATE_IDLE
        self.awaiting_quantize = False
        self.sl_state = SL_STATE_OFF
        self._sync_led()
        self._mark_action()

    def _tap(self) -> None:
        if self._debounced():
            print(f"loop {self.loop}: -> tap ignored (debounce)", flush=True)
            return
        if self._waiting_for_quantize():
            print(f"loop {self.loop}: -> tap ignored (quantize wait)", flush=True)
            return

        if self.state == STATE_IDLE:
            self._hit("record")
            self.state = STATE_RECORDING
        elif self.state == STATE_RECORDING:
            # Armed but not yet recording (WAIT_START): a second tap means
            # "cancel". Sending `record` again is what SL expects for both.
            self._hit("record")
            self._begin_quantize_wait()
        elif self.state == STATE_PLAYING:
            self._hit("pause")
            self.state = STATE_STOPPED
        elif self.state == STATE_STOPPED:
            self._hit("trigger")
            self.state = STATE_PLAYING
        else:
            self._hit("record")
            self.state = STATE_RECORDING

        self._sync_led()
        self._mark_action()
        print(
            f"loop {self.loop}: -> tap done (state={self.state}, sl_state={self.sl_state})",
            flush=True,
        )

    def on_pad_down(self) -> None:
        self._pad_down = True
        self._pad_down_at = time.monotonic()
        self._hold_fired = False
        print(f"loop {self.loop}: pad down (note {self._note})", flush=True)

    def on_pad_up(self) -> None:
        held = time.monotonic() - self._pad_down_at
        print(
            f"loop {self.loop}: pad up held={held:.3f}s hold_fired={self._hold_fired}",
            flush=True,
        )
        if self._pad_down and not self._hold_fired:
            self._tap()
        self._pad_down = False

    def poll_hold(self) -> None:
        if not self._pad_down or self._hold_fired:
            return
        if (time.monotonic() - self._pad_down_at) < self.hold_s:
            return
        self._hold_fired = True
        self._pad_down = False
        print(f"loop {self.loop}: -> hold clear", flush=True)
        self._clear_loop()


def build_footswitches(
    *,
    osc,
    midi_out,
    num_loops: int,
    hold_ms: float,
    debounce_ms: float,
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
    """Stop playback and clear every loop; reset bench LED/state."""
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
    """Pause every loop without clearing audio; LEDs -> stopped (yellow)."""
    osc.send_message("/sl/-1/hit", "pause")
    for fs in footswitches:
        fs.awaiting_quantize = False
        if fs.state != STATE_IDLE:
            fs.state = STATE_STOPPED
        fs._sync_led()
    print(f"-> stop all: paused {num_loops} loops", flush=True)
