"""Per-loop APC footswitch state + 16-pad grid wiring."""

from __future__ import annotations

import threading
import time

from apc_grid import all_loop_pads, pad_note
from sl_grid_sync import apply_grid_sync
from sl_loop_states import QUANTIZE_WAIT, SL_STATE_PLAYING, SL_STATE_WAIT_STOP

STATE_IDLE = "idle"
STATE_RECORDING = "recording"
STATE_PLAYING = "playing"
STATE_STOPPED = "stopped"

LED_OFF = 0
LED_GREEN = 1
LED_RED = 3
LED_YELLOW = 5

_master_loop_established = False


def master_loop_established() -> bool:
    return _master_loop_established


def _osc_send(osc, path: str, args: list) -> None:
    osc.send_message(path, args)


def _refresh_grid_sync(osc, *, num_loops: int) -> None:
    apply_grid_sync(lambda path, args: _osc_send(osc, path, args), num_loops=num_loops)


def _ensure_master_playing(osc) -> None:
    """Quantized slaves need loop 0 playing to supply cycle boundaries."""
    if not _master_loop_established:
        return
    osc.send_message("/sl/0/hit", "trigger")


def _schedule_grid_sync(osc, *, num_loops: int, delay_s: float = 0.35) -> None:
    """Re-apply grid sync after loop 0 lands — not inline (avoids smothering playback)."""

    def _run() -> None:
        time.sleep(delay_s)
        _refresh_grid_sync(osc, num_loops=num_loops)
        _ensure_master_playing(osc)
        print("grid-sync: re-applied after loop 0 landed", flush=True)

    threading.Thread(target=_run, daemon=True, name="sl-grid-sync-delay").start()


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
        self.sl_state = -1
        self.awaiting_quantize = False

    def bind(self, osc, midi_out, note: int) -> None:
        self._osc = osc
        self._midi_out = midi_out
        self._note = note

    def sync_from_sl(self, sl_state: int) -> bool:
        """Mirror SooperLooper state for quantized slaves only (loop > 0)."""
        if self.loop == 0:
            return False
        self.sl_state = sl_state
        before = self.state
        if sl_state == SL_STATE_PLAYING:
            self.awaiting_quantize = False
            self.state = STATE_PLAYING
        elif sl_state == SL_STATE_WAIT_STOP:
            self.awaiting_quantize = True
            self.state = STATE_RECORDING
        elif sl_state in QUANTIZE_WAIT:
            self.awaiting_quantize = True
            self.state = STATE_RECORDING
        if before != self.state:
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
        return self.loop > 0 and (self.awaiting_quantize or self.sl_state in QUANTIZE_WAIT)

    def _clear_loop(self) -> None:
        global _master_loop_established
        if self.loop == 0:
            _master_loop_established = False
        self._hit("undo_all")
        self.state = STATE_IDLE
        self.awaiting_quantize = False
        self.sl_state = -1
        self._sync_led()
        self._mark_action()

    def _tap(self) -> None:
        global _master_loop_established
        if self._debounced():
            print(f"loop {self.loop}: -> tap ignored (debounce)", flush=True)
            return
        if self._waiting_for_quantize():
            print(f"loop {self.loop}: -> tap ignored (quantize wait)", flush=True)
            return

        if self.state == STATE_IDLE:
            if self.loop > 0:
                _ensure_master_playing(self._osc)
            self._hit("record")
            self.state = STATE_RECORDING
        elif self.state == STATE_RECORDING:
            self._hit("record")
            if self.loop == 0:
                _master_loop_established = True
                self.state = STATE_PLAYING
                self._hit("trigger")
                _schedule_grid_sync(self._osc, num_loops=self.num_loops)
            elif _master_loop_established:
                self.awaiting_quantize = True
            else:
                self.state = STATE_PLAYING
        elif self.state == STATE_PLAYING:
            self._hit("pause")
            self.state = STATE_STOPPED
        elif self.state == STATE_STOPPED:
            if self.loop > 0:
                _ensure_master_playing(self._osc)
            self._hit("trigger")
            self.state = STATE_PLAYING
        else:
            self._hit("record")
            self.state = STATE_RECORDING

        self._sync_led()
        self._mark_action()
        print(f"loop {self.loop}: -> tap done (state={self.state})", flush=True)

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


def slave_footswitches(footswitches: list[LoopFootswitch]) -> dict[int, LoopFootswitch]:
    return {fs.loop: fs for fs in footswitches if fs.loop > 0}


def _reset_footswitch_state(fs: LoopFootswitch) -> None:
    fs.state = STATE_IDLE
    fs.awaiting_quantize = False
    fs.sl_state = -1
    fs._sync_led()


def reset_all_loops(
    osc,
    midi_out,
    *,
    num_loops: int,
    footswitches: list[LoopFootswitch],
) -> None:
    """Stop playback and clear every loop; reset bench LED/state."""
    global _master_loop_established
    _master_loop_established = False
    osc.send_message("/sl/-1/hit", "pause")
    for loop in range(num_loops):
        osc.send_message(f"/sl/{loop}/hit", "undo_all")
    for fs in footswitches:
        _reset_footswitch_state(fs)
    for row, col, loop_i in all_loop_pads():
        if loop_i >= num_loops:
            continue
        note = pad_note(row, col)
        midi_out.send_message([0x90, note, LED_OFF])
    print(f"-> track reset: cleared {num_loops} loops", flush=True)
    _refresh_grid_sync(osc, num_loops=num_loops)


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
