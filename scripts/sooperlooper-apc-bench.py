#!/usr/bin/env python3
"""APC mini 16-clip grid + Shift/Stop-All transport — eval bench.

Rows 0 and 3 (loops 0–15): short tap = footswitch cycle, hold ~2 s = clear loop.
Shift + Stop All Clips (release) = stop all loops. Shift + Stop All held 3 s = clear all.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "sooperlooper"))
from apc_footswitch import (  # noqa: E402
    build_footswitches,
    footswitches_by_loop,
    reset_all_loops,
    stop_all_loops,
)
from apc_grid import NUM_LOOPS, loop_index_for_note  # noqa: E402
from apc_transport import (  # noqa: E402
    NOTE_SHIFT_MK2,
    NOTE_STOP_ALL_CLIPS_MK2,
    ShiftHoldCombo,
)
from sl_bench_listener import SlBenchStateListener  # noqa: E402
from sl_grid_sync import apply_grid_sync  # noqa: E402


def midi_note_down(st: int, vel: int) -> bool | None:
    cmd = st & 0xF0
    if cmd == 0x90:
        return vel > 0
    if cmd == 0x80:
        return False
    return None


def _format_midi(msg: list[int]) -> str:
    if not msg:
        return str(msg)
    st = msg[0]
    cmd = st & 0xF0
    ch = st & 0x0F
    if cmd in (0x90, 0x80) and len(msg) >= 3:
        kind = "note_on" if cmd == 0x90 and msg[2] > 0 else "note_off"
        return f"ch={ch} {kind} note=0x{msg[1]:02X}({msg[1]}) vel={msg[2]}"
    return " ".join(f"0x{b:02X}" for b in msg)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dump-midi",
        action="store_true",
        help="Log every raw MIDI message (hex) — use to verify Shift/Stop All notes",
    )
    args = parser.parse_args()

    port_hint = os.environ.get("MPE_APC_MIDI_PORT", "APC")
    host = os.environ.get("MPE_SL_OSC_HOST", "127.0.0.1")
    port = int(os.environ.get("MPE_SL_OSC_PORT", "9951"))
    hold_ms = float(os.environ.get("MPE_APC_HOLD_MS", "2000"))
    debounce_ms = float(os.environ.get("MPE_APC_DEBOUNCE_MS", "200"))
    num_loops = int(os.environ.get("MPE_SL_LOOPS", str(NUM_LOOPS)))
    shift_note = int(os.environ.get("MPE_APC_SHIFT_NOTE", str(NOTE_SHIFT_MK2)))
    stop_all_note = int(
        os.environ.get("MPE_APC_STOP_ALL_NOTE", str(NOTE_STOP_ALL_CLIPS_MK2))
    )
    track_reset_hold_ms = float(os.environ.get("MPE_APC_TRACK_RESET_HOLD_MS", "3000"))
    sync_mode = os.environ.get("MPE_SL_SYNC_MODE", "grid").strip().lower()

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

    if sync_mode not in ("free", "freeform", "0", "off"):
        apply_grid_sync(lambda path, a: osc.send_message(path, a), num_loops=num_loops)
        print("bench: grid sync applied at startup (JACK transport)", flush=True)

    by_note, footswitches = build_footswitches(
        osc=osc,
        midi_out=midi_out,
        num_loops=num_loops,
        hold_ms=hold_ms,
        debounce_ms=debounce_ms,
    )
    for fs in footswitches:
        fs._sync_led()

    by_loop = footswitches_by_loop(footswitches)
    state_listener = SlBenchStateListener(by_loop)
    state_listener.start()
    state_listener.register(osc, num_loops=num_loops)

    track_reset = ShiftHoldCombo(
        shift_note=shift_note,
        target_note=stop_all_note,
        hold_s=track_reset_hold_ms / 1000.0,
    )

    print(
        f"APC [{idx}] {ports_in[idx]} | clip pads rows 0+3 -> loops 0..{num_loops - 1} | "
        f"OSC {host}:{port} | {len(by_note)} pads | "
        f"Shift=0x{shift_note:02X} StopAll=0x{stop_all_note:02X} | "
        f"short tap=cycle hold>={hold_ms:.0f}ms clear | "
        f"Shift+StopAll release=stop all | "
        f"Shift+StopAll held>={track_reset_hold_ms:.0f}ms=clear all",
        flush=True,
    )
    if args.dump_midi:
        print("dump-midi: ON — watch for Shift/Stop All note numbers", flush=True)

    def poll_holds() -> None:
        for fs in footswitches:
            fs.poll_hold()

    def maybe_track_transport() -> None:
        if track_reset.poll_long():
            print("transport: Shift+StopAll long -> track reset", flush=True)
            reset_all_loops(
                osc,
                midi_out,
                num_loops=num_loops,
                footswitches=footswitches,
            )
        elif track_reset.poll_short():
            print("transport: Shift+StopAll short -> stop all", flush=True)
            stop_all_loops(
                osc,
                num_loops=num_loops,
                footswitches=footswitches,
            )

    while True:
        packet = midi_in.get_message()
        if packet is None:
            poll_holds()
            maybe_track_transport()
            state_listener.maybe_reregister()
            time.sleep(0.002)
            continue

        msg, _delta = packet
        if args.dump_midi and msg:
            print(f"midi: {_format_midi(list(msg))}", flush=True)

        if not msg or len(msg) < 2:
            poll_holds()
            maybe_track_transport()
            state_listener.maybe_reregister()
            continue

        st, n = msg[0], msg[1]
        vel = msg[2] if len(msg) > 2 else 0
        down = midi_note_down(st, vel)
        if down is not None and n in (shift_note, stop_all_note):
            label = "Shift" if n == shift_note else "StopAll"
            print(f"transport: {label} {'down' if down else 'up'}", flush=True)
            track_reset.note_event(n, down)
            maybe_track_transport()
            poll_holds()
            state_listener.maybe_reregister()
            continue

        if down is not None and n in by_note:
            if down:
                by_note[n].on_pad_down()
            else:
                by_note[n].on_pad_up()
        elif down is not None and loop_index_for_note(n) is not None:
            print(
                f"ignored clip pad note {n} (loop {loop_index_for_note(n)} >= {num_loops})",
                flush=True,
            )

        poll_holds()
        maybe_track_transport()
        state_listener.maybe_reregister()

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except KeyboardInterrupt:
        sys.exit(0)
