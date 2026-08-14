#!/usr/bin/env python3
"""APC pad footswitch + Shift/Stop-All track reset — eval bench only.

Per clip pad (short tap, release before hold):
  idle            -> record
  recording       -> end record + play
  playing         -> pause (stop, loop stays in RAM)
  stopped (saved) -> trigger (play loop again from top)
Hold (~1 s, do not release) -> undo_all (clear that loop)

Shift + Stop All Clips (APC mk2), held 3 s -> pause + undo_all on every loop,
all clip LEDs off, footswitch state idle (full track reset).
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "sooperlooper"))
from apc_grid import NUM_LOOPS, all_loop_pads, pad_note  # noqa: E402
from apc_transport import (  # noqa: E402
    NOTE_SHIFT_MK2,
    NOTE_STOP_ALL_CLIPS_MK2,
    ShiftHoldCombo,
)

STATE_IDLE = "idle"
STATE_RECORDING = "recording"
STATE_PLAYING = "playing"
STATE_STOPPED = "stopped"

LED_OFF = 0
LED_GREEN = 1
LED_RED = 3
LED_YELLOW = 5


def midi_note_down(st: int, vel: int) -> bool | None:
    """Return True=note on, False=note off, None=not a note message."""
    cmd = st & 0xF0
    if cmd == 0x90:
        return vel > 0
    if cmd == 0x80:
        return False
    return None


def pad_event(st: int, n: int, vel: int, note: int) -> bool | None:
    """Return True=down, False=up, None=unrelated."""
    if n != note:
        return None
    cmd = st & 0xF0
    if cmd == 0x90:
        return vel > 0
    if cmd == 0x80:
        return False
    return None


class LoopFootswitch:
    def __init__(
        self,
        *,
        loop: int,
        hold_ms: float,
        debounce_ms: float,
    ) -> None:
        self.loop = loop
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
        print(f"-> {cmd} (state={self.state})", flush=True)

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
        self._hit("undo_all")
        self.state = STATE_IDLE
        self._sync_led()
        self._mark_action()

    def _tap(self) -> None:
        if self._debounced():
            print("-> tap ignored (debounce)", flush=True)
            return

        if self.state == STATE_IDLE:
            self._hit("record")
            self.state = STATE_RECORDING
        elif self.state == STATE_RECORDING:
            self._hit("record")
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
        print(f"-> tap done (state={self.state})", flush=True)

    def on_pad_down(self) -> None:
        self._pad_down = True
        self._pad_down_at = time.monotonic()
        self._hold_fired = False
        print("pad down", flush=True)

    def on_pad_up(self) -> None:
        held = time.monotonic() - self._pad_down_at
        print(f"pad up held={held:.3f}s hold_fired={self._hold_fired}", flush=True)
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
        print("-> hold clear", flush=True)
        self._clear_loop()


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
        fs._sync_led()
    for row, col, _loop_i in all_loop_pads():
        if _loop_i >= num_loops:
            continue
        note = pad_note(row, col)
        midi_out.send_message([0x90, note, LED_OFF])
    print(f"-> track reset: cleared {num_loops} loops", flush=True)


def main() -> int:
    row = int(os.environ.get("MPE_APC_PAD_ROW", "0"))
    col = int(os.environ.get("MPE_APC_PAD_COL", "0"))
    loop = int(os.environ.get("MPE_SL_LOOP", "0"))
    port_hint = os.environ.get("MPE_APC_MIDI_PORT", "APC")
    host = os.environ.get("MPE_SL_OSC_HOST", "127.0.0.1")
    port = int(os.environ.get("MPE_SL_OSC_PORT", "9951"))
    note = int(os.environ.get("MPE_APC_NOTE", str(pad_note(row, col))))
    hold_ms = float(os.environ.get("MPE_APC_HOLD_MS", "1000"))
    debounce_ms = float(os.environ.get("MPE_APC_DEBOUNCE_MS", "200"))
    num_loops = int(os.environ.get("MPE_SL_LOOPS", str(NUM_LOOPS)))
    shift_note = int(os.environ.get("MPE_APC_SHIFT_NOTE", str(NOTE_SHIFT_MK2)))
    stop_all_note = int(
        os.environ.get("MPE_APC_STOP_ALL_NOTE", str(NOTE_STOP_ALL_CLIPS_MK2))
    )
    track_reset_hold_ms = float(os.environ.get("MPE_APC_TRACK_RESET_HOLD_MS", "3000"))

    try:
        import rtmidi
        from pythonosc import udp_client
    except ImportError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    midi_in = rtmidi.MidiIn()
    midi_out = rtmidi.MidiOut()
    ports_in = midi_in.get_ports()
    idx = next((i for i, n in enumerate(ports_in) if port_hint.lower() in n.lower()), None)
    if idx is None:
        print("No APC port. Ports:", ports_in, file=sys.stderr)
        return 1

    midi_in.open_port(idx)
    midi_out.open_port(idx)
    osc = udp_client.SimpleUDPClient(host, port)
    footswitch = LoopFootswitch(loop=loop, hold_ms=hold_ms, debounce_ms=debounce_ms)
    footswitch.bind(osc, midi_out, note)
    footswitch._sync_led()
    track_reset = ShiftHoldCombo(
        shift_note=shift_note,
        target_note=stop_all_note,
        hold_s=track_reset_hold_ms / 1000.0,
    )

    print(
        f"APC [{idx}] {ports_in[idx]} | pad ({row},{col}) note {note} | "
        f"OSC {host}:{port} | loops={num_loops} | "
        f"short tap=cycle hold>={hold_ms:.0f}ms clear | "
        f"Shift+StopAll held>={track_reset_hold_ms:.0f}ms=track reset",
        flush=True,
    )

    def maybe_track_reset() -> None:
        if track_reset.poll():
            reset_all_loops(
                osc,
                midi_out,
                num_loops=num_loops,
                footswitches=[footswitch],
            )

    while True:
        packet = midi_in.get_message()
        if packet is None:
            footswitch.poll_hold()
            maybe_track_reset()
            time.sleep(0.002)
            continue

        msg, _delta = packet
        if not msg or len(msg) < 2:
            footswitch.poll_hold()
            maybe_track_reset()
            continue

        st, n = msg[0], msg[1]
        vel = msg[2] if len(msg) > 2 else 0
        down = midi_note_down(st, vel)
        if down is not None and n in (shift_note, stop_all_note):
            track_reset.note_event(n, down)
            maybe_track_reset()
            footswitch.poll_hold()
            continue

        edge = pad_event(st, n, vel, note)
        if edge is True:
            footswitch.on_pad_down()
        elif edge is False:
            footswitch.on_pad_up()

        footswitch.poll_hold()
        maybe_track_reset()

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except KeyboardInterrupt:
        sys.exit(0)
