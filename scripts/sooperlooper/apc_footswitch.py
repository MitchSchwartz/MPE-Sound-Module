"""Per-loop APC footswitch state + 16-pad grid wiring."""

from __future__ import annotations

import time

from apc_grid import all_loop_pads, pad_note
from sl_grid_sync import apply_grid_sync
from sl_master_clock import (
    apply_internal_master,
    capture_from_hud_file,
    clear_master_clock,
    load_master_clock,
)

STATE_IDLE = "idle"
STATE_RECORDING = "recording"
STATE_PLAYING = "playing"
STATE_STOPPED = "stopped"

LED_OFF = 0
LED_GREEN = 1
LED_RED = 3
LED_YELLOW = 5

_master_loop_established = False
_master_sync_mode: str | None = None  # "loop0" | "internal"


def _osc_send(osc, path: str, args: list) -> None:
    osc.send_message(path, args)


def _refresh_grid_sync(osc, *, num_loops: int) -> None:
    global _master_sync_mode
    apply_grid_sync(lambda path, args: _osc_send(osc, path, args), num_loops=num_loops)
    _master_sync_mode = "loop0"


def _capture_master_clock_from_hud() -> None:
    """Best-effort snapshot after loop 0 lands (hud monitor publishes cycle_len)."""
    for _ in range(8):
        if capture_from_hud_file():
            return
        time.sleep(0.05)


def _detach_loop_master(osc, *, num_loops: int) -> None:
    """Loop 0 cleared — keep quantize grid via internal tempo reference."""
    global _master_sync_mode
    clock = load_master_clock()
    if clock:
        apply_internal_master(
            lambda path, args: _osc_send(osc, path, args),
            clock,
            num_loops=num_loops,
        )
        _master_sync_mode = "internal"
        print(
            f"master-clock: detached loop 0 — internal {clock['tempo']:.1f} BPM, "
            f"cycle {clock['cycle_len']:.3f}s",
            flush=True,
        )
    else:
        _master_sync_mode = None
        _refresh_grid_sync(osc, num_loops=num_loops)


def _ensure_master_playing(osc) -> None:
    """Quantized slaves need an active grid clock."""
    if not _master_loop_established:
        return
    if _master_sync_mode == "internal":
        osc.send_message("/set", ["tap_tempo", 0.0])
        return
    osc.send_message("/sl/0/hit", "trigger")


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

    def bind(self, osc, midi_out, note: int) -> None:
        self._osc = osc
        self._midi_out = midi_out
        self._note = note

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

    def _clear_loop(self) -> None:
        global _master_loop_established, _master_sync_mode
        if self.loop == 0 and _master_loop_established:
            self._hit("undo_all")
            self.state = STATE_IDLE
            self._sync_led()
            _detach_loop_master(self._osc, num_loops=self.num_loops)
            self._mark_action()
            return
        self._hit("undo_all")
        self.state = STATE_IDLE
        self._sync_led()
        self._mark_action()

    def _tap(self) -> None:
        if self._debounced():
            print(f"loop {self.loop}: -> tap ignored (debounce)", flush=True)
            return

        if self.state == STATE_IDLE:
            if self.loop > 0:
                _ensure_master_playing(self._osc)
            self._hit("record")
            self.state = STATE_RECORDING
        elif self.state == STATE_RECORDING:
            self._hit("record")
            if self.loop == 0:
                global _master_loop_established, _master_sync_mode
                _master_loop_established = True
                _refresh_grid_sync(self._osc, num_loops=self.num_loops)
                _capture_master_clock_from_hud()
                _master_sync_mode = "loop0"
            # SL may stay in WaitStop until cycle boundary — keep red until playing.
            if self.loop == 0 or not _master_loop_established:
                self.state = STATE_PLAYING
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


def reset_all_loops(
    osc,
    midi_out,
    *,
    num_loops: int,
    footswitches: list[LoopFootswitch],
) -> None:
    """Stop playback and clear every loop; reset bench LED/state."""
    global _master_loop_established, _master_sync_mode
    _master_loop_established = False
    _master_sync_mode = None
    clear_master_clock()
    for loop in range(num_loops):
        osc.send_message(f"/sl/{loop}/hit", "pause")
        osc.send_message(f"/sl/{loop}/hit", "undo_all")
    for fs in footswitches:
        fs.state = STATE_IDLE
        fs._sync_led()
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
        if fs.state != STATE_IDLE:
            fs.state = STATE_STOPPED
        fs._sync_led()
    print(f"-> stop all: paused {num_loops} loops", flush=True)
