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
from apc_transport import ShiftHoldCombo, resolve_apc_transport_notes  # noqa: E402
from sl_bench_listener import SlBenchStateListener  # noqa: E402
from sl_grid_state import GridState  # noqa: E402
from sl_grid_sync import (  # noqa: E402
    anchor_phase,
    apply_freeform,
    apply_grid_sync,
    set_count_in,
)


def transport_is_rolling() -> bool:
    """True only if a JACK timebase master is actually publishing BBT."""
    try:
        import jack

        from jack_transport_util import transport_rolling

        client = jack.Client("mpe-bench-probe", no_start_server=True)
        try:
            state, pos = client.transport_query()
            return transport_rolling(state) and dict(pos or {}).get("beat") is not None
        finally:
            client.close()
    except Exception as exc:  # jack missing, server down, no transport
        print(f"bench: transport probe failed ({exc})", file=sys.stderr, flush=True)
        return False


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
    shift_note = int(os.environ.get("MPE_APC_SHIFT_NOTE", "0"))
    stop_all_note = int(os.environ.get("MPE_APC_STOP_ALL_NOTE", "0"))
    apc_variant = os.environ.get("MPE_APC_VARIANT", "").strip() or None
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
    port_name = ports_in[idx]
    if shift_note <= 0 or stop_all_note <= 0:
        shift_note, stop_all_note, apc_label = resolve_apc_transport_notes(
            port_name, variant=apc_variant
        )
    else:
        apc_label = apc_variant or "env"
    osc = udp_client.SimpleUDPClient(host, port)

    def _send(path: str, a: list) -> None:
        osc.send_message(path, a)

    grid_active = True
    if sync_mode in ("free", "freeform", "0", "off"):
        grid_active = False
        apply_freeform(_send, num_loops=num_loops)
        print("bench: freeform sync applied at startup (no quantize)", flush=True)
    else:
        grid_clock = os.environ.get("MPE_SL_GRID_CLOCK", "internal").strip().lower()
        if grid_clock == "transport" and not transport_is_rolling():
            print(
                "bench: REFUSING grid — MPE_SL_GRID_CLOCK=transport but no JACK "
                "timebase master is rolling. SL would park in WaitStart forever "
                "and every pad would stop responding. Start one "
                "(scripts/start-jack-timebase.sh) or use MPE_SL_GRID_CLOCK=internal. "
                "Falling back to free-form.",
                file=sys.stderr,
                flush=True,
            )
            grid_active = False
            apply_freeform(_send, num_loops=num_loops)
        else:
            apply_grid_sync(_send, num_loops=num_loops, clock=grid_clock)
            if grid_clock != "transport":
                anchor_phase(_send)
            print(
                f"bench: grid sync applied at startup (clock={grid_clock})",
                flush=True,
            )

    grid = GridState()

    def on_grid_established(bpm: float, bars: int) -> None:
        """First take landed: capture its tempo, then turn the grid on.

        Until now every loop had sync=0 so the defining take could record
        instantly. From here clips count in to the bar and quantize.
        """
        osc.send_message("/set", ["tempo", float(bpm)])
        set_count_in(_send, num_loops=num_loops, count_in=True)
        print(
            f"bench: grid established — {bars} bar(s) @ {bpm:.1f} BPM. "
            f"Later clips count in to the bar.",
            flush=True,
        )

    by_note, footswitches = build_footswitches(
        osc=osc,
        midi_out=midi_out,
        num_loops=num_loops,
        hold_ms=hold_ms,
        debounce_ms=debounce_ms,
        quantized=grid_active,
        grid=grid if grid_active else None,
        on_grid_established=on_grid_established if grid_active else None,
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
        f"APC [{idx}] {port_name} ({apc_label}) | clip pads rows 0+3 -> loops 0..{num_loops - 1} | "
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
