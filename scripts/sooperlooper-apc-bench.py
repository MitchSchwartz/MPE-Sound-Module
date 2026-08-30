#!/usr/bin/env python3
"""APC mini 16-track clip row + Shift/Stop-All transport — eval bench.

Ableton-style: the 16 tracks are one horizontal line on the bottom row, eight
visible at a time. Up/Down page the viewport by eight; Shift+Left/Right nudge
it by one. Short tap = gesture cycle, hold ~2 s = clear loop.
Shift + Stop All Clips (release) = stop all loops. Shift + Stop All held 3 s = clear all.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "sooperlooper"))
from track_gesture import (  # noqa: E402
    apply_view,
    build_track_gestures,
    gestures_by_loop,
    poll_track_gestures,
    reset_all_loops,
    stop_all_loops,
)
from apc_faders import MASTER, fader_for_cc, is_control_change, resolve_fader_ccs  # noqa: E402
from apc_grid import GRID_COLS, GRID_ROWS, NUM_LOOPS, GridView, is_clip_note, is_reserved_grid_note  # noqa: E402
from apc_transport import (  # noqa: E402
    Mk1ShiftGhostFilter,
    ShiftHoldCombo,
    TransportButtonLeds,
    bank_delta_for_arrow,
    resolve_apc_transport_notes,
    resolve_arrow_notes,
    resolve_scene_launch_notes,
    resolve_stale_lamp_note,
    scene_row_for_note,
)
from led_table import LED_OFF  # noqa: E402
from apc_link import LinkHealth, PacedMidiOut  # noqa: E402
from apc_mode import grid_silent_reason, parse_mode_sysex  # noqa: E402
from apc_panel import is_stop_all, scene_press_row  # noqa: E402
from midi_subscription import wait_for_subscription  # noqa: E402
from running_code import running_code_sha  # noqa: E402
from slot_runtime import SlotRuntime  # noqa: E402
from slot_surface import SlotSurface  # noqa: E402
from loop_mix import CoalescingSender, LoopMix  # noqa: E402
from sl_bench_listener import SlBenchStateListener  # noqa: E402
from looper_engine_events import LooperEngineEventWatch, poll_interval_s  # noqa: E402
from sl_grid_state import GridState  # noqa: E402
from sl_grid_sync import (  # noqa: E402
    RING_OUT_ENABLED,
    apply_freeform,
    apply_grid_sync,
    establish_grid_clock,
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


def run_bench(argv: list[str] | None = None, *, osc_session=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--measure-latency",
        type=int,
        metavar="N",
        help="Collect N MIDI-in→OSC-out samples and exit (criterion 42)",
    )
    parser.add_argument(
        "--dump-midi",
        action="store_true",
        help="Log every raw MIDI message (hex) — use to verify Shift/Stop All notes",
    )
    args = parser.parse_args(argv)

    if osc_session is None:
        from sl_osc_session import SlOscSession

        osc_session = SlOscSession().start()

    port_hint = os.environ.get("MPE_APC_MIDI_PORT", "APC")
    host = os.environ.get("MPE_SL_OSC_HOST", "127.0.0.1")
    port = int(os.environ.get("MPE_SL_OSC_PORT", "9951"))
    hold_ms = float(os.environ.get("MPE_APC_HOLD_MS", "2000"))
    debounce_ms = float(os.environ.get("MPE_APC_DEBOUNCE_MS", "200"))
    hold_blink_start_ms = float(os.environ.get("MPE_APC_HOLD_BLINK_START_MS", "500"))
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

    # Last mode the APC announced. None until it says something -- the
    # device does not report on connect, only on change.
    apc_mode_state: dict = {"mode": None}

    midi_in = rtmidi.MidiIn()
    midi_out = rtmidi.MidiOut()
    ports_in = midi_in.get_ports()
    idx = next((i for i, n in enumerate(ports_in) if port_hint.lower() in n.lower()), None)
    if idx is None:
        print("No APC port. Ports:", ports_in, file=sys.stderr)
        return 1

    midi_in.open_port(idx)
    # rtmidi drops SysEx by default, so the APC's mode announcements never
    # arrived. Without them a Notes-mode switch presents as a silently dead
    # grid -- the failure that cost a debugging session on 2026-08-28.
    # The APC sends these only on an actual mode change, so this adds no
    # traffic to the hot loop.
    try:
        midi_in.ignore_types(sysex=False, timing=True, active_sense=True)
    except Exception as exc:  # pragma: no cover - depends on rtmidi build
        print(f"Warning: could not enable SysEx input: {exc}", flush=True)
    midi_out.open_port(idx)
    port_name = ports_in[idx]

    # Every write to the APC goes through the pacer. It is a 12 Mbit
    # full-speed device two hubs deep, sharing that chain with a Scarlett
    # streaming audio; a 64-message burst stalls its endpoint (-EPIPE) and the
    # device drops off the bus and re-enumerates. Measured 2026-08-27: four
    # session starts in six left the pads dead this way.
    raw_midi_out = midi_out
    midi_out = PacedMidiOut(raw_midi_out)

    # open_port() reports success whether or not the ALSA subscription took.
    # On 2026-08-27 it did not — twice — because systemd started this process
    # in the same second it SIGKILLed the previous one, and the banner below
    # printed a complete, correct device line over dead pads for 17 minutes.
    # Ask the kernel rather than trusting the library; refuse to run blind, the
    # way sl-osc-session refuses when it cannot bind its port.
    device_key = port_name.split(":")[0] or "APC"
    has_reader, has_writer = wait_for_subscription(device_key)
    if not has_reader:
        print(
            f"bench: FAIL — opened {port_name!r} but nothing is subscribed to it.\n"
            f"  ALSA shows no reader for this device, so no pad press can arrive.\n"
            f"  Usually a restart race: the previous session still held the device.\n"
            f"  Fix: systemctl stop mpe-looper-session, wait for the process to go,\n"
            f"       then start it.\n"
            f"  Refusing to run blind — a bench that receives nothing looks exactly\n"
            f"  like an idle one.",
            file=sys.stderr,
        )
        return 1
    if not has_writer:
        print(f"bench: WARN — no writer to {port_name!r}; LEDs will not light.",
              file=sys.stderr)
    if shift_note <= 0 or stop_all_note <= 0:
        shift_note, stop_all_note, apc_label = resolve_apc_transport_notes(
            port_name, variant=apc_variant
        )
    else:
        apc_label = apc_variant or "env"

    # The pacer encodes pad colour per model; it cannot know the model until
    # now. Set before the 64-pad blank below, which is the first LED write.
    midi_out.apc_label = apc_label
    osc = osc_session.client
    midi_osc_latencies: list[float] = []
    midi_osc_pending: list[float] = []
    if args.measure_latency:
        # Tap the CLIENT, not the bench's _send helper. TrackGesturees are handed the raw
        # client by build_track_gestures(osc=...) and send /hit through it directly, so a
        # hook in _send sees nothing a pad ever does. Measured on the appliance
        # 2026-08-19: 267 pad presses, zero samples, no result printed.
        from latency_tap import LatencyTapClient

        osc = LatencyTapClient(osc, midi_osc_pending, midi_osc_latencies)
    measure_deadline = (
        time.monotonic()
        + float(os.environ.get("MPE_MEASURE_LATENCY_DEADLINE_S", "300"))
        if args.measure_latency
        else None
    )

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
        establish_grid_clock(_send, bpm, bars=bars)
        # `establish_grid_clock` zeroes the engine's phase, so the bench's bar
        # line starts counting from the same instant.
        grid.mark_phase_zero(time.monotonic())
        set_grid_active(_send, num_loops=num_loops, active=True)
        print(
            f"bench: grid established — {bars} bar(s) @ {bpm:.1f} BPM, "
            f"cycle=1 bar (smart_eighths off). Later clips count in to the bar.",
            flush=True,
        )

    def on_phase_reanchor(bpm: float) -> None:
        """Re-send tempo at the defining take's downbeat after a late PLAYING report."""
        establish_grid_clock(_send, bpm, bars=grid.bars or 1)
        grid.mark_phase_zero(time.monotonic())
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
    # Startup only: nothing else is happening yet, and the surface must be
    # blank before anything paints over it. ~96 ms at the pacing rate.
    midi_out.drain()

    scene_launch_notes = resolve_scene_launch_notes(apc_label)
    multigrid = os.environ.get("MPE_SL_MULTIGRID", "0") == "1"
    mk1_ghost: Mk1ShiftGhostFilter | None = None
    if apc_label == "mk1":
        mk1_ghost = Mk1ShiftGhostFilter(
            shift_note=shift_note,
            stop_all_note=stop_all_note,
            scene_launch_notes=scene_launch_notes,
        )

    def on_tail_change(loop: int, active: bool) -> None:
        """Subscribe the input meter only while that loop is ringing out.

        A standing subscription on all 15 loops is traffic spent answering a
        question that is only asked about one loop, for about a bar, after a
        take closes.
        """
        if osc_session is not None:
            osc_session.set_peak_updates(loop, active)

    by_note, gestures = build_track_gestures(
        osc=osc,
        midi_out=midi_out,
        num_loops=num_loops,
        hold_ms=hold_ms,
        debounce_ms=debounce_ms,
        hold_blink_start_ms=hold_blink_start_ms,
        quantized=grid_active,
        view=view,
        grid=grid if grid_active else None,
        on_grid_established=on_grid_established if grid_active else None,
        on_phase_reanchor=on_phase_reanchor if grid_active else None,
        on_grid_dropped=on_grid_dropped if grid_active else None,
        on_tail_change=on_tail_change,
        multigrid=multigrid,
    )
    if not multigrid:
        for fs in gestures:
            fs._sync_led()

    def on_looper_engine_started() -> None:
        """Reconcile bench grid state when the looper engine restarts (criterion 40).

        ``looper.engine.started`` is emitted explicitly by wire-sooperlooper-graph.sh
        after graph verify — not inferred from config drift.
        """
        if not grid_active:
            return
        print("bench: looper.engine.started — re-applying grid config", flush=True)
        apply_grid_sync(_send, num_loops=num_loops)
        if grid.established and grid.bpm:
            establish_grid_clock(_send, grid.bpm, bars=grid.bars or 1)
            set_grid_active(_send, num_loops=num_loops, active=True)
            print(f"bench: grid restored — {grid.bpm:.1f} BPM, 1-bar cycle",
                  flush=True)
        else:
            print("bench: no grid to restore — next take defines one", flush=True)

    engine_event_watch = LooperEngineEventWatch(on_looper_engine_started)
    last_engine_event_poll = 0.0
    engine_event_poll_s = poll_interval_s()

    loop_fader_ccs, master_cc, _fader_label = resolve_fader_ccs(
        port_name, variant=apc_variant
    )
    mix = LoopMix(num_loops=num_loops, view=view)
    faders = CoalescingSender(_send, interval_s=fader_interval_ms / 1000.0)

    def on_wet(loop_index: int, value: float) -> None:
        mix.seed_from_engine(loop_index, value)

    by_loop = gestures_by_loop(gestures)
    # Multi-clip matrix. OFF by default: it takes over all eight rows including
    # row 0, replacing the single-clip record gesture Mitch plays with today.
    # That is not a change to make live without him having tried it, so it is
    # opt-in until it has earned the default.
    slot_surface = None
    if multigrid:
        slot_runtime = SlotRuntime(
            send=_send,
            clips_dir=Path(
                os.environ.get("MPE_SL_CLIPS_DIR",
                               str(Path.home() / ".mpe" / "looper-clips"))
            ),
            num_tracks=num_loops,
            log=lambda m: print(f"slots: {m}", flush=True),
            # The grid's bar line, so a launch is quantized even when nothing
            # is playing. Before this the only boundary was a loop wrap, which
            # requires audio — so after Stop All every launch fired instantly.
            grid_boundary=lambda: grid.next_boundary(time.monotonic()),
            # A clip started into silence IS the downbeat. Without this the
            # grid keeps counting from whenever the phase was last zeroed, and
            # every later clip lines up with nothing anyone can hear.
            mark_phase_zero=lambda: grid.mark_phase_zero(time.monotonic()),
        )
        slot_surface = SlotSurface(
            runtime=slot_runtime,
            gestures_by_loop=by_loop,
            view=view,
            midi_out=midi_out,
            num_tracks=num_loops,
            scene_launch_notes=scene_launch_notes,
            hold_s=hold_ms / 1000.0,
            hold_blink_start_s=hold_blink_start_ms / 1000.0,
            log=lambda m: print(f"slots: {m}", flush=True),
        )
        slot_surface.repaint_scenes(force=True)
        print(
            f"bench: MULTIGRID on — 8 slots x {num_loops} tracks; "
            f"rows are slots, columns are tracks. Kill switch: MPE_SL_MULTIGRID=0",
            flush=True,
        )

    state_listener = SlBenchStateListener(by_loop, on_wet=on_wet, session=osc_session)
    state_listener.start()
    if slot_surface is not None:
        state_listener.attach_surface(slot_surface)
    state_listener.register(osc, num_loops=num_loops)
    print(
        "bench: ring-out capture "
        + ("on (take closes into a one-pass overdub)" if RING_OUT_ENABLED
           else "off (MPE_SL_RING_OUT=0 — plain stop)"),
        flush=True,
    )

    arrow_notes = resolve_arrow_notes(port_name, variant=apc_variant)
    # One shift latch for the whole event loop. ShiftHoldCombo keeps its own
    # `_shift_down`, so a second combo watching the same note would need its own
    # feed and could disagree with the first about whether Shift is held.
    shift_held = False

    stop_all_took_shift = False
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
        by_note = apply_view(
            midi_out, gestures=gestures, view=view, multigrid=multigrid
        )
        if slot_surface is not None:
            slot_surface.set_view(view)
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
    transport_leds = TransportButtonLeds(
        midi_out=midi_out,
        shift_note=shift_note,
        stop_all_note=stop_all_note,
        stale_lamp_note=resolve_stale_lamp_note(apc_label),
        scene_launch_notes=scene_launch_notes,
        hold_s=track_reset_hold_ms / 1000.0,
        apc_label=apc_label,
    )

    print(f"bench: running code {running_code_sha()}", flush=True)
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


    def poll_engine_events(now_mono: float) -> None:
        nonlocal last_engine_event_poll
        if now_mono - last_engine_event_poll >= engine_event_poll_s:
            last_engine_event_poll = now_mono
            engine_event_watch.poll()

    def reopen_apc() -> bool:
        """Reopen the APC after it re-enumerated. Returns True if it took.

        The device comes back with a new USB device number and usually a new
        rtmidi port index, so the index resolved at startup is worthless —
        re-resolve by name. Our old ALSA client survives the re-enumeration
        still subscribed to a device that no longer exists, which is exactly
        why nothing noticed for hours: it has to be closed explicitly.
        """
        nonlocal midi_in, raw_midi_out, by_note
        try:
            midi_in.close_port()
            raw_midi_out.close_port()
        except Exception:
            pass
        try:
            ports = midi_in.get_ports()
            new_idx = next(
                (i for i, n in enumerate(ports) if port_hint.lower() in n.lower()),
                None,
            )
            if new_idx is None:
                return False
            midi_in.open_port(new_idx)
            raw_midi_out.open_port(new_idx)
        except Exception as exc:
            print(f"bench: APC reopen failed: {exc}", file=sys.stderr, flush=True)
            return False
        # The device came back dark and its LED cache is now a lie, so repaint
        # everything rather than diffing against a surface that no longer
        # exists.
        midi_out.reset()
        for _n in range(GRID_ROWS * GRID_COLS):
            midi_out.send_message([0x90, _n, LED_OFF])
        by_note = apply_view(
            midi_out, gestures=gestures, view=view, multigrid=multigrid
        )
        if slot_surface is not None:
            slot_surface.repaint(force=True)
            slot_surface.repaint_scenes(force=True)
        transport_leds.repaint()
        return True

    link_health = LinkHealth(
        device_key,
        on_lost=reopen_apc,
        log=lambda m: print(f"bench: {m}", flush=True),
    )

    def poll_holds() -> None:
        # Ask the kernel, on a timer, whether we still have the device. This is
        # the check that was missing: open_port succeeded once at startup and
        # nothing ever asked again, so a re-enumeration left the bench running
        # against a dead input while printing a healthy banner.
        link_health.poll()
        midi_out.pump()
        poll_track_gestures(gestures, multigrid=multigrid)
        if slot_surface is not None:
            slot_surface.poll_hold()
            slot_surface.poll_hold_led()
            if multigrid:
                slot_surface.poll_led_repaint()

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

    def poll_transport_leds() -> None:
        transport_leds.poll()
        if slot_surface is not None:
            slot_surface.repaint_scenes()

    def maybe_track_transport() -> None:
        if track_reset.poll_long():
            print("transport: Shift+StopAll long -> track reset", flush=True)
            transport_leds.on_reset_fired()
            reset_all_loops(
                osc,
                midi_out,
                num_loops=num_loops,
                gestures=gestures,
            )
            if slot_surface is not None:
                slot_surface.reset()
        elif track_reset.poll_short():
            print("transport: Shift+StopAll short -> stop all", flush=True)
            stop_all_loops(
                osc,
                num_loops=num_loops,
                gestures=gestures,
            )
            if slot_surface is not None:
                # Queued intent dies with the audio. Without this a deferred
                # launch outlived the panic button and restarted the track.
                slot_surface.on_stop_all()

    while True:
        if (
            args.measure_latency
            and measure_deadline is not None
            and time.monotonic() >= measure_deadline
        ):
            print(
                f"measure-latency: deadline expired with n={len(midi_osc_latencies)} "
                f"(need {args.measure_latency})",
                file=sys.stderr,
                flush=True,
            )
            return 1
        if args.measure_latency and len(midi_osc_latencies) >= args.measure_latency:
            ordered = sorted(midi_osc_latencies)
            p50 = ordered[len(ordered) // 2]
            p99 = ordered[int(round(0.99 * (len(ordered) - 1)))]
            print(
                f"live: n={len(ordered)} p50={p50:.3f}ms p99={p99:.3f}ms max={ordered[-1]:.3f}ms",
                flush=True,
            )
            return 0
        packet = midi_in.get_message()
        if packet is None:
            poll_holds()
            poll_transport_leds()
            maybe_track_transport()
            tick_faders()
            state_listener.maybe_reregister()

            poll_engine_events(time.monotonic())
            time.sleep(0.002)
            continue

        msg, _delta = packet
        if args.dump_midi and msg:
            print(f"midi: {_format_midi(list(msg))}", flush=True)

        announced = parse_mode_sysex(msg)
        if announced is not None:
            apc_mode_state["mode"] = announced
            print(f"APC mode: {announced.describe()}", flush=True)
            reason = grid_silent_reason(announced)
            if reason:
                print(f"APC: {reason}", flush=True)
            poll_engine_events(time.monotonic())
            continue

        if not msg or len(msg) < 2:
            poll_holds()
            poll_transport_leds()
            maybe_track_transport()
            state_listener.maybe_reregister()

            poll_engine_events(time.monotonic())
            continue

        st, n = msg[0], msg[1]
        vel = msg[2] if len(msg) > 2 else 0

        if is_control_change(st) and len(msg) >= 3:
            handle_cc(n, vel)
            poll_holds()
            poll_transport_leds()
            maybe_track_transport()
            state_listener.maybe_reregister()

            poll_engine_events(time.monotonic())
            continue

        down = midi_note_down(st, vel)
        now_mono = time.monotonic()
        if mk1_ghost is not None and down is not None:
            mk1_ghost.note_event(n, down, now=now_mono)
            if mk1_ghost.consume(n, down, now=now_mono):
                poll_holds()
                poll_transport_leds()
                maybe_track_transport()
                state_listener.maybe_reregister()
                poll_engine_events(now_mono)
                continue

        # The bottom button is a scene launcher alone and Stop All Clips with
        # Shift. Which one it is has to be latched at press-down: if we asked
        # the live `shift_held` again on release, letting go of Shift first
        # would send the down to the transport combo and the up to the scene
        # handler, and the combo would sit there holding a button forever.
        if down is not None and is_stop_all(apc_label, n):
            if down:
                stop_all_took_shift = shift_held
            routing_shift = stop_all_took_shift
        else:
            routing_shift = shift_held
        scene_row = (
            scene_press_row(
                n,
                scene_notes=scene_launch_notes,
                apc_label=apc_label,
                shift_held=routing_shift,
            )
            if down is not None
            else None
        )
        if scene_row is not None:
            if slot_surface is not None and down:
                slot_surface.scene_press(scene_row)
            elif args.dump_midi:
                print(f"ignored scene launch note {n} (set MPE_SL_MULTIGRID=1)", flush=True)
            poll_holds()
            poll_transport_leds()
            maybe_track_transport()
            state_listener.maybe_reregister()
            poll_engine_events(now_mono)
            continue

        if slot_surface is not None and down is not None and slot_surface.handles(n):
            if down:
                slot_surface.note_down(n)
            else:
                slot_surface.note_up(n)
            poll_holds()
            poll_transport_leds()
            maybe_track_transport()
            state_listener.maybe_reregister()
            poll_engine_events(now_mono)
            continue

        if down is not None and is_reserved_grid_note(n):
            if args.dump_midi:
                print(f"ignored reserved grid note {n} (rows 1-7: set MPE_SL_MULTIGRID=1)", flush=True)
            poll_holds()
            poll_transport_leds()
            maybe_track_transport()
            state_listener.maybe_reregister()
            poll_engine_events(now_mono)
            continue

        if down is not None and n == shift_note:
            shift_held = down
        if down and handle_arrow(n):
            poll_holds()
            poll_transport_leds()
            maybe_track_transport()
            state_listener.maybe_reregister()

            poll_engine_events(time.monotonic())
            continue

        if down is not None and n in (shift_note, stop_all_note):
            label = "Shift" if n == shift_note else "StopAll"
            print(f"transport: {label} {'down' if down else 'up'}", flush=True)
            track_reset.note_event(n, down)
            transport_leds.note_event(n, down)
            maybe_track_transport()
            poll_holds()
            state_listener.maybe_reregister()

            poll_engine_events(time.monotonic())
            continue

        if down is not None and n in by_note:
            if args.measure_latency:
                # Stamp BOTH edges. A short tap sends its OSC on pad-up, so timing from
                # pad-down measures how long the finger was held, not how long the code
                # took: an 80 ms synthetic hold produced an 80 ms "latency" on
                # 2026-08-19. The slot holds the most recent MIDI event, which is the
                # one that caused whatever send comes next.
                midi_osc_pending[:] = [time.monotonic()]
            if down:
                by_note[n].on_pad_down()
            else:
                by_note[n].on_pad_up()
        elif down is not None and is_clip_note(n):
            print(f"ignored clip pad note {n} (no track in this bank)", flush=True)

        poll_holds()
        poll_transport_leds()
        maybe_track_transport()
        state_listener.maybe_reregister()

        poll_engine_events(time.monotonic())

    return 0


def main() -> int:
    return run_bench()


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except KeyboardInterrupt:
        sys.exit(0)