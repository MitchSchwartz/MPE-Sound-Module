#!/usr/bin/env python3
"""APC mini 16-track clip row + Shift/Stop-All transport — eval bench.

Ableton-style: the 16 tracks are one horizontal line on the bottom row, eight
visible at a time. Up/Down page the viewport by eight; Shift+Left/Right nudge
it by one. Short tap = footswitch cycle, hold ~2 s = clear loop.
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
    apply_view,
    build_footswitches,
    footswitches_by_loop,
    reset_all_loops,
    stop_all_loops,
)
from apc_faders import MASTER, fader_for_cc, is_control_change, resolve_fader_ccs  # noqa: E402
from apc_grid import GRID_COLS, GRID_ROWS, NUM_LOOPS, GridView, is_clip_note  # noqa: E402
from apc_transport import (  # noqa: E402
    ShiftHoldCombo,
    bank_delta_for_arrow,
    resolve_apc_transport_notes,
    resolve_arrow_notes,
)
from led_table import LED_OFF  # noqa: E402
from loop_mix import CoalescingSender, LoopMix  # noqa: E402
from sl_bench_listener import SlBenchStateListener  # noqa: E402
from sl_seam_weld import SCRATCH_LOOP, SEAM_WELD_ENABLED, SeamWeldWorker  # noqa: E402
from sl_grid_state import GridState, display_bpm  # noqa: E402
from sl_grid_sync import (  # noqa: E402
    RESTART_SENTINEL,
    TAIL_CAPTURE_ENABLED,
    apply_freeform,
    apply_grid_sync,
    establish_grid_clock,
    expected_sentinel,
    set_grid_active,
)


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
    # Faders are CC. Without this branch --dump-midi renders them as raw hex,
    # which is exactly the tool you reach for to confirm which CC each fader
    # sends on an unfamiliar APC variant.
    if cmd == 0xB0 and len(msg) >= 3:
        return f"ch={ch} cc={msg[1]} value={msg[2]}"
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
    fader_interval_ms = float(os.environ.get("MPE_APC_FADER_INTERVAL_MS", "10"))

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
        apply_grid_sync(_send, num_loops=num_loops)
        print("bench: grid sync applied at startup (SL internal tempo)", flush=True)

    grid = GridState()

    def on_grid_established(bpm: float, bars: int) -> None:
        """First take landed: capture its tempo, then turn the grid on.

        Until now every loop had sync=0 so the defining take could record
        instantly. From here clips count in to the next bar.
        """
        establish_grid_clock(_send, bpm)
        set_grid_active(_send, num_loops=num_loops, active=True)
        print(
            f"bench: grid established — {bars} bar(s) @ {bpm:.1f} BPM, "
            f"cycle=1 bar (smart_eighths off). Later clips count in to the bar.",
            flush=True,
        )

    def on_phase_reanchor(bpm: float) -> None:
        """Re-send tempo at the defining take's downbeat after a late PLAYING report."""
        establish_grid_clock(_send, bpm)
        print(
            f"bench: phase re-anchored @ {bpm:.1f} BPM (loop wrap)",
            flush=True,
        )

    def on_grid_dropped() -> None:
        """Last clip cleared: back to no grid, exactly as a track reset leaves it."""
        set_grid_active(_send, num_loops=num_loops, active=False)
        print("bench: grid dropped — next take defines a new one", flush=True)

    # The one owner of the viewport. Everything that needs to know which track
    # is in which column reads it from here — the LED painter, the pad handler
    # and the fader layer — so the three cannot drift apart.
    view = GridView(num_loops=num_loops)

    # Blank the whole 8x8 before anything paints. Only the bottom row is ours
    # now, so nothing else would ever write rows 1-7: LEDs left lit by the
    # previous build (or by Ableton, or by a crash) would sit there all session
    # advertising tracks that are not on those pads.
    for _note in range(GRID_ROWS * GRID_COLS):
        midi_out.send_message([0x90, _note, LED_OFF])

    by_note, footswitches = build_footswitches(
        osc=osc,
        midi_out=midi_out,
        num_loops=num_loops,
        hold_ms=hold_ms,
        debounce_ms=debounce_ms,
        quantized=grid_active,
        view=view,
        grid=grid if grid_active else None,
        on_grid_established=on_grid_established if grid_active else None,
        on_phase_reanchor=on_phase_reanchor if grid_active else None,
        on_grid_dropped=on_grid_dropped if grid_active else None,
    )
    for fs in footswitches:
        fs._sync_led()

    def on_engine_settings(control: str, value: float) -> None:
        """Notice the engine restarted underneath us, and put the grid back.

        `apply_grid_sync` used to run exactly once, at bench startup. Nothing
        re-applied it — so after `mpe looper sl-restart` the engine came back
        with SooperLooper's defaults (`smart_eighths` back ON, no internal sync
        source, default cycle and fade) while the bench carried on believing its
        configuration was in force. The next take recorded in the wrong mode and
        nothing anywhere said so. Same shape as the orphan: a component
        reporting healthy while the thing it configures is gone.

        Reconciling on a *sentinel* rather than on a timer matters. Re-sending
        `tempo` is the phase reset (Engine::set_tempo zeroes the counters), so a
        blind periodic re-apply would knock the grid out of phase every cycle.
        Here it fires only when the engine is demonstrably not the one we set up,
        where resetting phase is not just safe but correct — its phase is gone
        anyway.
        """
        if not grid_active or control != RESTART_SENTINEL:
            return
        if value == expected_sentinel():
            return
        print(f"bench: !! engine reset detected ({control}={value}, expected "
              f"{expected_sentinel()}) — re-applying grid config", flush=True)
        apply_grid_sync(_send, num_loops=num_loops)
        if grid.established and grid.bpm:
            establish_grid_clock(_send, grid.bpm)
            set_grid_active(_send, num_loops=num_loops, active=True)
            print(f"bench: grid restored — {grid.bpm:.1f} BPM, 1-bar cycle",
                  flush=True)
        else:
            print("bench: no grid to restore — next take defines one", flush=True)

    loop_fader_ccs, master_cc, _fader_label = resolve_fader_ccs(
        port_name, variant=apc_variant
    )
    mix = LoopMix(num_loops=num_loops, view=view)
    faders = CoalescingSender(_send, interval_s=fader_interval_ms / 1000.0)

    def on_wet(loop_index: int, value: float) -> None:
        mix.seed_from_engine(loop_index, value)

    by_loop = footswitches_by_loop(footswitches)
    state_listener = SlBenchStateListener(
        by_loop, on_global=on_engine_settings, on_wet=on_wet
    )
    state_listener.start()
    state_listener.register(osc, num_loops=num_loops)
    state_listener.wire_tail_capture(footswitches)
    seam_worker: SeamWeldWorker | None = None
    if SEAM_WELD_ENABLED and TAIL_CAPTURE_ENABLED:
        seam_worker = SeamWeldWorker(_send)
        for fs in footswitches:
            fs.set_seam_weld_hooks(
                on_prepare_scratch=lambda loop, w=seam_worker: w.prepare_scratch(
                    SCRATCH_LOOP
                ),
                on_start_scratch=lambda loop, w=seam_worker: w.start_scratch_record(
                    SCRATCH_LOOP
                ),
                on_stop_scratch=lambda loop, w=seam_worker: w.stop_scratch_record(
                    SCRATCH_LOOP
                ),
                on_request_merge=lambda loop, done, w=seam_worker: w.request(
                    loop, SCRATCH_LOOP, done=done
                ),
            )
        print(
            f"bench: Tier 3 seam weld on (scratch loop {SCRATCH_LOOP})",
            flush=True,
        )
    if not TAIL_CAPTURE_ENABLED:
        print(
            "bench: MPE_SL_TAIL_CAPTURE off — defining-take tail capture disabled",
            flush=True,
        )

    arrow_notes = resolve_arrow_notes(port_name, variant=apc_variant)
    # One shift latch for the whole event loop. ShiftHoldCombo keeps its own
    # `_shift_down`, so a second combo watching the same note would need its own
    # feed and could disagree with the first about whether Shift is held.
    shift_held = False

    def set_view(new_view: GridView) -> None:
        """Move the viewport: repaint the pads, rebind the faders.

        Both halves must happen together. Repainting alone leaves eight faders
        still writing the previous bank's levels; rebinding alone leaves the
        pads lying about which track is where.
        """
        nonlocal view, by_note
        if new_view.offset == view.offset:
            return
        view = new_view
        by_note = apply_view(midi_out, footswitches=footswitches, view=view)
        mix.set_view(view)
        last = view.offset + 7
        print(f"bank: tracks {view.offset + 1}-{last + 1} of {num_loops}", flush=True)

    def handle_arrow(note: int) -> bool:
        direction = arrow_notes.get(note)
        if direction is None:
            return False
        delta = bank_delta_for_arrow(direction, shift_down=shift_held)
        if delta:
            set_view(view.scrolled(delta))
        return True

    track_reset = ShiftHoldCombo(
        shift_note=shift_note,
        target_note=stop_all_note,
        hold_s=track_reset_hold_ms / 1000.0,
    )

    print(
        f"APC [{idx}] {port_name} ({apc_label}) | bottom row -> 8 of {num_loops} tracks "
        f"(Up/Down page 8, Shift+Left/Right nudge 1) | "
        f"OSC {host}:{port} | {len(by_note)} pads | "
        f"Shift=0x{shift_note:02X} StopAll=0x{stop_all_note:02X} | "
        f"short tap=cycle hold>={hold_ms:.0f}ms clear | "
        f"Shift+StopAll release=stop all | "
        f"Shift+StopAll held>={track_reset_hold_ms:.0f}ms=clear all | "
        f"faders CC{loop_fader_ccs[0]}..{loop_fader_ccs[-1]} -> the 8 visible "
        f"tracks, CC{master_cc} -> all loops (master)",
        flush=True,
    )
    if args.dump_midi:
        print("dump-midi: ON — watch for Shift/Stop All note numbers", flush=True)

    def poll_holds() -> None:
        for fs in footswitches:
            fs.poll_tail_capture()
            fs.poll_hold()
            fs.poll_led()

    def tick_faders() -> None:
        """Ramp smoothed wet toward targets between CC events."""
        faders.tick(now=time.monotonic())

    def handle_cc(cc: int, value: int) -> None:
        fader = fader_for_cc(cc, loop_fader_ccs=loop_fader_ccs, master_cc=master_cc)
        if fader is None:
            return
        now = time.monotonic()
        if fader == MASTER:
            affected = range(num_loops)
        elif isinstance(fader, int):
            affected = [n for n in view.loops_for_column(fader) if n < num_loops]
        else:
            affected = ()
        for loop in affected:
            faders.seed_current(f"/sl/{loop}/set", mix.wet_for(loop))
        faders.submit(mix.messages_for(fader, value), now=now)
        faders.tick(now=now)

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
            tick_faders()
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

        if is_control_change(st) and len(msg) >= 3:
            handle_cc(n, vel)
            poll_holds()
            maybe_track_transport()
            state_listener.maybe_reregister()
            continue

        down = midi_note_down(st, vel)
        if down is not None and n == shift_note:
            shift_held = down
        if down and handle_arrow(n):
            poll_holds()
            maybe_track_transport()
            state_listener.maybe_reregister()
            continue

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
        elif down is not None and is_clip_note(n):
            print(f"ignored clip pad note {n} (no track in this bank)", flush=True)

        poll_holds()
        maybe_track_transport()
        state_listener.maybe_reregister()

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except KeyboardInterrupt:
        sys.exit(0)
