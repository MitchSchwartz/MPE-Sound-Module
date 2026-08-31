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
    verify_stop_all,
)
from apc_faders import MASTER, fader_for_cc, is_control_change, resolve_fader_ccs  # noqa: E402
from remote_fader import RemoteFaderReceiver  # noqa: E402
from apc_grid import NUM_LOOPS, GridView, is_clip_note  # noqa: E402
from apc_transport import (  # noqa: E402
    Mk1ShiftGhostFilter,
    ShiftHoldCombo,
    TransportButtonLeds,
    bank_delta_for_arrow,
    resolve_apc_transport_notes,
    resolve_arrow_notes,
    resolve_scene_launch_notes,
)
from binding_table import HOLD, TAP, BindingRouter, for_surface, scene_row  # noqa: E402
from led_compositor import LedCompositor  # noqa: E402
from apc_link import LinkHealth, PacedMidiOut  # noqa: E402
from apc_mode import grid_silent_reason, parse_mode_sysex  # noqa: E402
from midi_subscription import wait_for_subscription  # noqa: E402
from running_code import running_code_sha  # noqa: E402
from slot_runtime import SlotRuntime  # noqa: E402
from slot_surface import SlotSurface  # noqa: E402
from loop_mix import CoalescingSender, LoopMix  # noqa: E402
from sl_bench_listener import SlBenchStateListener  # noqa: E402
from looper_engine_events import LooperEngineEventWatch, poll_interval_s  # noqa: E402
from sl_grid_state import GridState  # noqa: E402
from sl_limits import resolve_num_loops  # noqa: E402
from sl_grid_sync import (  # noqa: E402
    RING_OUT_ENABLED,
    apply_established_grid,
    mark_immediate_downbeat,
    apply_freeform,
    apply_grid_sync,
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
    # Clamped, like every other consumer of this variable. Read raw, an
    # MPE_SL_LOOPS of 16 reinstates the phantom index 15 that `sl_limits`
    # exists to keep out — it answers `get` with plausible defaults and
    # silently discards every `set`, so the surface vouches for a track the
    # engine is not running. `ccde96a` removed the 16 from `pi5.env`, but
    # `measure-soak.sh` and `measure-latency-run.sh` both sed the same value
    # into the persistent `/etc/mpe/mpe.env` and neither restores it, so one
    # measurement run left the door open behind them.
    num_loops = resolve_num_loops()
    # MPE_APC_SHIFT_NOTE / MPE_APC_STOP_ALL_NOTE are gone. They injected two
    # note numbers at runtime, which `control_registry`'s rule 1 forbids
    # ("Note numbers ... live HERE. Nowhere else.") and no test could see. They
    # also skipped the variant resolver entirely, so `apc_label` became the raw
    # env string: every `== "mk2"` consumer then answered mk1, which is the
    # 2026-08-28 "pads barely light up, and I'm seeing blue" regression
    # reachable from a documented-looking env var. Nothing in the repo, the
    # units or /etc/mpe/mpe.env set them. MPE_APC_VARIANT stays — it selects a
    # variant, it does not invent a note.
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
    shift_note, stop_all_note, apc_label = resolve_apc_transport_notes(
        port_name, variant=apc_variant
    )

    # The pacer encodes pad colour per model; it cannot know the model until
    # now. Set before the first LED write, which the compositor makes below.
    midi_out.apc_label = apc_label

    # The one thing in this process that sends an LED byte. Everything that
    # wants a lamp lit submits desired state to it and it decides, once, what
    # the device is told. Before 2026-08-30 ten sites wrote here directly and
    # four of them kept private records of what they thought was showing; see
    # `led_compositor` for what that cost on a reconnect.
    leds = LedCompositor(midi_out, apc_label=apc_label)
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
        instantly. From here clips count in to the next CYCLE boundary — the
        first take's own length, which is one bar only when the take read as
        one (`looper-timing-model-spec.md` §1).
        """
        apply_established_grid(
            _send, grid, num_loops=num_loops, now=time.monotonic(), arm_loops=True
        )
        print(
            f"bench: grid established — {bars} bar(s) @ {bpm:.1f} BPM, "
            f"cycle={grid.cycle_s or 0.0:.3f}s (smart_eighths off). "
            f"Later clips count in to the cycle.",
            flush=True,
        )

    def on_phase_reanchor(_bpm: float) -> None:
        """Re-send tempo at the defining take's downbeat after a late PLAYING report.

        Phase only: the loops were armed when the grid landed, so `arm_loops`
        is False rather than re-sending the per-loop settings into live audio.

        `_bpm` is the gesture's freshly re-derived tempo and is deliberately
        NOT used. This used to send that value with `bars=grid.bars`, which is a
        tempo from one reading paired with a bar count from another — an engine
        cycle belonging to neither. The grid was captured once and stands
        (`looper-timing-model-spec.md` §2); the owner's numbers are the grid.
        The log prints what was actually sent, for the same reason `cap_for`
        returns its own source string.
        """
        apply_established_grid(
            _send, grid, num_loops=num_loops, now=time.monotonic(), arm_loops=False
        )
        print(
            f"bench: phase re-anchored @ {grid.bpm:.1f} BPM (loop wrap)",
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

    # We have no idea what the panel shows: a lamp left lit by the previous
    # build, by Ableton, or by a crash outlives the process, and on a surface
    # where an unowned pad is simply never written it would sit there all
    # session advertising a track that is not on it. `invalidate` asserts the
    # compositor's whole model — every lamp we know how to address, dark,
    # because nothing has submitted anything yet.
    leds.invalidate()
    # Startup only: nothing else is happening yet, and the surface must be
    # blank before anything paints over it. ~120 ms at the pacing rate.
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
        compositor=leds,
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
            # Through the one seam, which also marks phase zero. This block used
            # to send the tempo without it: `Engine::set_tempo` zeroes the
            # engine's counters (engine.cpp:2174-2178), so the engine's downbeat
            # moved to the restart instant while the bench kept counting from
            # wherever the grid last landed. Every quantized launch after a
            # restart was then placed against a bar line the engine did not
            # share — silently, with the surface vouching for it.
            apply_established_grid(
                _send, grid, num_loops=num_loops, now=time.monotonic(), arm_loops=True
            )
            print(
                f"bench: grid restored — {grid.bpm:.1f} BPM, "
                f"{grid.bars or 1}-bar cycle of {grid.cycle_s or 0.0:.3f}s",
                flush=True,
            )
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

    # The touch UI's Vol fader, arriving as if it were the master CC. It is not
    # a second writer of `wet`: it goes through `handle_cc` below, the same
    # entry the hardware master fader uses, so `LoopMix` still composes alone.
    remote_faders = RemoteFaderReceiver()
    if not remote_faders.open():
        print(
            f"bench: WARN — remote volume off ({remote_faders.error}); "
            "the touch Vol fader will trim Surge only.",
            file=sys.stderr,
        )

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
            #
            # Through the seam, not `grid.mark_phase_zero` directly. Marking
            # only the bench's downbeat moves OUR bar line and leaves the
            # engine's where `set_tempo` last put it (engine.cpp:2174-2178), so
            # every clip launched after this one is quantized against a bar
            # line the engine does not share — placed wrong, with the surface
            # vouching for it. `apply_established_grid` sends the tempo, which
            # IS the engine's phase reset, and marks ours in the same call;
            # that pairing is the whole reason the function exists.
            #
            # arm_loops=False: phase only. These loops are already armed, and
            # re-sending ~90 quantize/sync messages into a clip that just
            # started is latency on the one gesture that must feel instant.
            mark_phase_zero=lambda: mark_immediate_downbeat(
                _send, grid, num_loops=num_loops, now=time.monotonic()
            ),
        )
        slot_surface = SlotSurface(
            runtime=slot_runtime,
            gestures_by_loop=by_loop,
            view=view,
            compositor=leds,
            num_tracks=num_loops,
            scene_launch_notes=scene_launch_notes,
            hold_s=hold_ms / 1000.0,
            hold_blink_start_s=hold_blink_start_ms / 1000.0,
            log=lambda m: print(f"slots: {m}", flush=True),
        )
        slot_surface.repaint_scenes()
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
            leds, gestures=gestures, view=view, multigrid=multigrid
        )
        if slot_surface is not None:
            slot_surface.set_view(view)
        mix.set_view(view)
        last = view.offset + 7
        print(f"bank: tracks {view.offset + 1}-{last + 1} of {num_loops}", flush=True)

    def handle_arrow(note: int) -> bool:
        """Move the viewport. The Shift gate is arithmetic in bank_delta_for_arrow.

        `bindings.shift_held` is the one modifier latch. It used to be a
        loop-local read from three points of a 150-line `if`-chain with
        `continue`s between them; it now lives next to the routing decision it
        exists for, which is the only thing that reads it.
        """
        direction = arrow_notes.get(note)
        if direction is None:
            return False
        delta = bank_delta_for_arrow(direction, shift_down=bindings.shift_held)
        if delta:
            set_view(view.scrolled(delta))
        return True

    track_reset = ShiftHoldCombo(
        shift_note=shift_note,
        target_note=stop_all_note,
        hold_s=track_reset_hold_ms / 1000.0,
    )
    transport_leds = TransportButtonLeds(
        compositor=leds,
        shift_note=shift_note,
        stop_all_note=stop_all_note,
        hold_s=track_reset_hold_ms / 1000.0,
        apc_label=apc_label,
    )

    # The banner used to promise banking unconditionally. On mk2 the arrow
    # notes were the scene column, so the promise was false on every boot for
    # the whole life of the feature — the exact failure shape AGENTS.md names:
    # a reading identical whether it is working or broken.
    banking = (
        "(Up/Down page 8, Shift+Left/Right nudge 1)" if arrow_notes
        else "(BANKING UNAVAILABLE: arrow notes unknown on this variant — see "
             "device_facts.apc.bank_arrows.notes; --dump-midi to close it)"
    )
    print(f"bench: running code {running_code_sha()}", flush=True)
    print(
        f"APC [{idx}] {port_name} ({apc_label}) | bottom row -> 8 of {num_loops} tracks "
        f"{banking} | "
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
        # The device came back dark, so our record of what it shows is a lie.
        # `invalidate` is the whole of it — one cache, one forgetting, one
        # re-assertion of the resolved model.
        #
        # This used to be four calls, in an order that mattered and was wrong:
        # repaint the matrix, repaint the scene column, then
        # `transport_leds.repaint()` -> `clear_unwired_surfaces()`, which
        # darkened 56 of the 64 pads the first call had just painted and all
        # eight scene buttons. The surface's own diff cache had already
        # recorded them as painted, so the next fifty poll cycles sent nothing
        # and the erasure was permanent: after any USB glitch the player's
        # stored takes were gone from the grid until the session restarted.
        # Order cannot matter here now — submissions resolve by declared
        # priority, so the same panel comes out whichever way round they run.
        midi_out.reset()
        by_note = apply_view(
            leds, gestures=gestures, view=view, multigrid=multigrid
        )
        if slot_surface is not None:
            slot_surface.repaint()
            slot_surface.repaint_scenes()
        leds.invalidate()
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

    def poll_remote_faders() -> None:
        """Replay a remote master move through the hardware fader's own path."""
        value = remote_faders.poll()
        if value is not None:
            handle_cc(master_cc, value)

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
        """Advance the Stop All hold blink. Nothing else animates here.

        This used to re-derive all eight scene buttons on every call, which is
        every idle iteration at ~485 Hz — `row_has_occupied` plus
        `row_is_fully_playing` over fifteen tracks, eight times, ~16.5 us an
        iteration (3.2 % of a Pi 5 core, extrapolated) to discover nothing had
        moved. It was there because `TransportButtonLeds` darkened the scene
        column behind the surface's back and the surface had to keep painting
        it again. With one writer to the wire the column changes only when the
        clips do, and every path that moves a clip repaints it.
        """
        transport_leds.poll()

    # --- what each control DOES -------------------------------------------
    #
    # One closure per action named in `binding_table.ACTIONS`. They are the
    # bodies of the old event-loop branches, unchanged; what has gone is the
    # `if`-chain that decided which one ran, and with it the possibility that
    # an earlier branch swallows a note a later one is waiting for. That is not
    # a tidier version of the same risk — it is the mk2 banking bug made
    # unexpressible, because `control_registry` refuses two claims on one note
    # and `binding_table` refuses two rows on one gesture.
    #
    # Every closure takes (number, down, control): the note or CC, the edge,
    # and the registry id of the control that sent it.

    def _log_unbound_pad(note: int) -> None:
        """A grid pad with no track under it in this bank.

        Both strings are verbatim what the old chain printed — including the
        one advising `MPE_SL_MULTIGRID=1` while multigrid is on. They are
        `--dump-midi` diagnostics on a path that 15 tracks in an 8-wide window
        cannot reach; correcting them is a separate question from moving the
        routing, so they move unchanged.
        """
        if is_clip_note(note):
            print(f"ignored clip pad note {note} (no track in this bank)", flush=True)
        elif args.dump_midi:
            print(
                f"ignored reserved grid note {note} (rows 1-7: set MPE_SL_MULTIGRID=1)",
                flush=True,
            )

    def act_noop(_number: int, _down: bool, _control: str) -> None:
        """Nothing is wired to this control. Written down, not omitted."""

    def act_scene_launch(note: int, _down: bool, control: str) -> None:
        if slot_surface is not None:
            # The row comes from the control's place in the registry's scene
            # ordering, not from re-finding the note in a tuple.
            slot_surface.scene_press(scene_row(control))
        elif args.dump_midi:
            print(f"ignored scene launch note {note} (set MPE_SL_MULTIGRID=1)", flush=True)

    def act_scene_release_consumed(note: int, _down: bool, _control: str) -> None:
        if args.dump_midi:
            print(f"ignored scene launch note {note} (set MPE_SL_MULTIGRID=1)", flush=True)

    def act_slot_press(note: int, _down: bool, _control: str) -> None:
        if slot_surface is not None and slot_surface.handles(note):
            slot_surface.note_down(note)
        else:
            _log_unbound_pad(note)

    def act_slot_release(note: int, _down: bool, _control: str) -> None:
        if slot_surface is not None and slot_surface.handles(note):
            slot_surface.note_up(note)
        else:
            _log_unbound_pad(note)

    def _stamp_latency() -> None:
        # Stamp BOTH edges. A short tap sends its OSC on pad-up, so timing from
        # pad-down measures how long the finger was held, not how long the code
        # took: an 80 ms synthetic hold produced an 80 ms "latency" on
        # 2026-08-19. The slot holds the most recent MIDI event, which is the
        # one that caused whatever send comes next.
        if args.measure_latency:
            midi_osc_pending[:] = [time.monotonic()]

    def act_clip_press(note: int, _down: bool, _control: str) -> None:
        fs = by_note.get(note)
        if fs is None:
            _log_unbound_pad(note)
            return
        _stamp_latency()
        fs.on_pad_down()

    def act_clip_release(note: int, _down: bool, _control: str) -> None:
        fs = by_note.get(note)
        if fs is None:
            _log_unbound_pad(note)
            return
        _stamp_latency()
        fs.on_pad_up()

    def act_ignore_reserved_row(note: int, _down: bool, _control: str) -> None:
        if args.dump_midi:
            print(
                f"ignored reserved grid note {note} (rows 1-7: set MPE_SL_MULTIGRID=1)",
                flush=True,
            )

    def act_latch_shift(_note: int, down: bool, _control: str) -> None:
        """The event loop's modifier latch.

        First action of the `shift` row, so it lands before `apc_transport`
        sees the same event — which is where the old loop put it: branch 4 set
        it and deliberately did NOT `continue`.
        """
        bindings.set_shift(down)

    def act_transport_note(note: int, down: bool, _control: str) -> None:
        label = "Shift" if note == shift_note else "StopAll"
        print(f"transport: {label} {'down' if down else 'up'}", flush=True)
        track_reset.note_event(note, down)
        transport_leds.note_event(note, down)

    #: When to ask SL what Stop All actually achieved. A one-element list
    #: because this is a closure, not a class.
    stop_all_verify_at: list[float | None] = [None]

    def poll_stop_all_verify(now_mono: float) -> None:
        """Report the TRUTH about the last Stop All, once SL has told us.

        Deliberately separate from `act_stop_all_loops`: SL pushes state, so
        asking in the same breath as the pause returns the pre-stop value and
        confirms whatever was already there.
        """
        due = stop_all_verify_at[0]
        if due is None or now_mono < due:
            return
        stop_all_verify_at[0] = None
        verify_stop_all(gestures)

    def act_stop_all_loops(_note: int, _down: bool, _control: str) -> None:
        print("transport: Shift+StopAll short -> stop all", flush=True)
        stop_all_verify_at[0] = stop_all_loops(
            osc,
            num_loops=num_loops,
            gestures=gestures,
        )
        if slot_surface is not None:
            # Queued intent dies with the audio. Without this a deferred
            # launch outlived the panic button and restarted the track.
            slot_surface.on_stop_all()

    def act_clear_all_loops(_note: int, _down: bool, _control: str) -> None:
        print("transport: Shift+StopAll long -> track reset", flush=True)
        transport_leds.on_reset_fired()
        reset_all_loops(
            osc,
            leds,
            num_loops=num_loops,
            gestures=gestures,
        )
        if slot_surface is not None:
            slot_surface.reset()

    def act_bank_scroll(note: int, _down: bool, _control: str) -> None:
        handle_arrow(note)

    def act_fader_move(cc: int, value: int, _control: str) -> None:
        handle_cc(cc, value)

    # The one place a MIDI event turns into an action. `BindingRouter` refuses
    # to be built if any action a row can reach has no handler here, so a new
    # row cannot ship as a control that silently does nothing.
    bindings = BindingRouter(
        for_surface(apc_label, multigrid=multigrid),
        actions={
            "noop": act_noop,
            "scene_launch": act_scene_launch,
            "scene_release_consumed": act_scene_release_consumed,
            "slot_press": act_slot_press,
            "slot_release": act_slot_release,
            "clip_press": act_clip_press,
            "clip_release": act_clip_release,
            "ignore_reserved_row": act_ignore_reserved_row,
            "latch_shift": act_latch_shift,
            "transport_note": act_transport_note,
            "stop_all_loops": act_stop_all_loops,
            "clear_all_loops": act_clear_all_loops,
            "bank_scroll": act_bank_scroll,
            "fader_move": act_fader_move,
        },
        ghost=mk1_ghost,
    )

    def maybe_track_transport() -> None:
        """Ask the combo whether a threshold passed, then fire the row.

        The milliseconds are `ShiftHoldCombo`'s and stay there. What is no
        longer here is the *consequence*: "Shift+StopAll held clears every
        take" is a row in `binding_table` beside everything else that button
        does, instead of a body forty lines from anything that mentions it.
        """
        if track_reset.poll_long():
            bindings.fire("stop_all_clips", HOLD)
        elif track_reset.poll_short():
            bindings.fire("stop_all_clips", TAP)

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
            poll_stop_all_verify(time.monotonic())
            maybe_track_transport()
            tick_faders()
            poll_remote_faders()
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

        # ONE routing decision, and it is a lookup. What was here until
        # 2026-08-30 was nine `if` branches whose ORDER decided which control
        # won a note — which is not a metaphor for the mk2 banking bug, it is
        # the bug: the scene branch `continue`d forty-five lines before
        # `handle_arrow` was reached, so four buttons did nothing and the boot
        # banner advertised them anyway, under 126 green tests. `binding_table`
        # resolves note -> control -> row, with collisions refused at import in
        # both steps, so there is no order left to get wrong.
        if len(msg) >= 2:
            st, n = msg[0], msg[1]
            vel = msg[2] if len(msg) > 2 else 0
            if is_control_change(st) and len(msg) >= 3:
                bindings.cc_event(n, vel)
            else:
                bindings.note_event(
                    n, down=midi_note_down(st, vel), now=time.monotonic()
                )

        poll_holds()
        poll_transport_leds()
        poll_stop_all_verify(time.monotonic())
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