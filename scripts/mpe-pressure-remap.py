#!/usr/bin/env python3
"""MPE pressure-floor MIDI remapper — controllers → virtual port → Surge."""

from __future__ import annotations

import heapq
import os
import queue
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from patch_browser.midi_clock import read_clock_state  # noqa: E402
from patch_browser.midi_sync import (  # noqa: E402
    clock_through_enabled,
    is_note_on,
    is_realtime_clock_byte,
    plan_fire_at,
    prepare_incoming,
    resolve_output_offset_ms,
    resolve_quantize_grid_ticks,
    should_schedule,
)
from patch_browser.patch_pressure import PatchPressureStore  # noqa: E402
from patch_browser.poly_voice_tracker import (  # noqa: E402
    PolyVoiceTracker,
    clear_fade_request,
    fade_actuation_enabled,
    read_fade_request,
)
from patch_browser.pressure_midi import (  # noqa: E402
    REMAP_OUTPUT_PORT_NAME,
    find_remap_output_port_index,
    is_roli_controller_port,
    list_roli_input_port_names,
    remap_midi_message,
)

sys.path.insert(0, str(REPO_ROOT / "scripts"))

from midi_device import (  # noqa: E402
    classify_port,
    override_for,
    parse_extra_exclusions,
    parse_overrides,
)
from midi_router import (  # noqa: E402
    RECONNECT_CLOSE,
    RECONNECT_IDLE,
    SourceBinding,
    bind_source,
    reconnect_decision,
    select_router_ports,
    startup_report,
)

RECONNECT_POLL_S = 1.0

# Classic-MIDI routing is off by default: phase 2 lands the mechanism, and
# the phase 5 ear pass is what promotes it. Set MPE_ROUTE_CLASSIC=1 to bind
# non-MPE input ports and translate them. With it off, _open_inputs binds
# exactly the ports it bound before this change.
ROUTE_CLASSIC = os.environ.get("MPE_ROUTE_CLASSIC", "0").strip() not in ("", "0")

# Manual classification, e.g. MPE_MIDI_OVERRIDE="osmose=mpe,keystation=classic".
# First match wins, so a specific rule can precede a broad one.
MIDI_OVERRIDES = parse_overrides(os.environ.get("MPE_MIDI_OVERRIDE"))

# Extra ports to keep away from the router entirely, e.g.
# MPE_ROUTER_EXCLUDE="scarlett" for a DIN jack carrying something the synth
# should not play.
EXTRA_EXCLUSIONS = parse_extra_exclusions(os.environ.get("MPE_ROUTER_EXCLUDE"))
CLOCK_REFRESH_S = 0.05


def lsusb_has_roli() -> bool:
    try:
        out = subprocess.run(
            ["lsusb"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    return "2af4:" in out.stdout.lower()


class PressureRemapDaemon:
    def __init__(self) -> None:
        self._floor = PatchPressureStore.read_live_floor()
        self._live_mtime = 0.0
        self._out = None
        self._inputs: list = []
        self._bindings: list[SourceBinding] = []
        self._queue: queue.SimpleQueue = queue.SimpleQueue()
        self._connected_port_names: tuple[str, ...] = ()
        self._next_reconnect_check = 0.0
        self._scheduled: list[tuple[float, int, SourceBinding, list[int]]] = []
        self._schedule_seq = 0
        self._offset_ms = resolve_output_offset_ms()
        self._grid_ticks = resolve_quantize_grid_ticks()
        self._clock_snap = read_clock_state()
        self._next_clock_refresh = 0.0
        self._pass_clock = clock_through_enabled()
        self._voice_tracker = PolyVoiceTracker()
        self._last_fade_request_id: float | None = None

    def _refresh_floor(self) -> None:
        from patch_browser.patch_pressure import LIVE_STATE_FILE

        try:
            stat = LIVE_STATE_FILE.stat()
        except OSError:
            return
        if stat.st_mtime <= self._live_mtime:
            return
        self._live_mtime = stat.st_mtime
        self._floor = PatchPressureStore.read_live_floor()

    def _refresh_clock(self, now: float) -> None:
        if now < self._next_clock_refresh:
            return
        self._next_clock_refresh = now + CLOCK_REFRESH_S
        self._clock_snap = read_clock_state()

    def _make_callback(self, binding: SourceBinding):
        """One callback per port. rtmidi hands the callback no source
        identity, so the binding has to be closed over here -- otherwise
        every device's messages arrive indistinguishable and the router
        cannot pick a transform."""

        def _enqueue(message, _data=None) -> None:
            flat = prepare_incoming(message)
            if flat:
                self._queue.put((binding, flat))

        return _enqueue

    def _schedule_message(
        self, fire_at: float, binding: SourceBinding, message: list[int]
    ) -> None:
        self._schedule_seq += 1
        heapq.heappush(self._scheduled, (fire_at, self._schedule_seq, binding, message))

    def _send(self, out_msg: list[int]) -> None:
        if not out_msg:
            return
        if fade_actuation_enabled() and self._voice_tracker.observe_message(out_msg):
            self._voice_tracker.persist()
        self._out.send_message(out_msg)

    def _emit_message(self, binding: SourceBinding, message: list[int]) -> None:
        if self._out is None:
            return
        try:
            out_msgs = binding.apply(message, self._floor)
        except (TypeError, ValueError, IndexError) as exc:
            print(f"Warning: skipping bad MIDI message {message!r}: {exc}", flush=True)
            return
        for out_msg in out_msgs:
            self._send(out_msg)

    def _process_fade_request(self) -> None:
        if not fade_actuation_enabled() or self._out is None:
            return
        request = read_fade_request()
        if not request:
            return
        try:
            request_id = float(request.get("request_id"))
            release_count = int(request.get("release_count", 0))
        except (TypeError, ValueError):
            clear_fade_request()
            return
        if release_count <= 0:
            clear_fade_request()
            return
        if self._last_fade_request_id == request_id:
            return
        targets = self._voice_tracker.notes_to_release(release_count)
        if not targets:
            return
        for channel, note in targets:
            self._out.send_message([0x80 | channel, note, 0])
            self._voice_tracker.observe_message([0x80 | channel, note, 0])
        self._voice_tracker.persist()
        self._last_fade_request_id = request_id
        clear_fade_request()

    def _handle_incoming(
        self, binding: SourceBinding, raw: list[int], now: float
    ) -> None:
        if not raw:
            return
        status = raw[0]
        if self._pass_clock and is_realtime_clock_byte(status):
            self._emit_message(binding, raw)
            return

        quantize = self._grid_ticks > 0 and is_note_on(raw)
        if should_schedule(raw, quantize_note_on=quantize):
            fire_at = plan_fire_at(
                now,
                self._clock_snap,
                quantize=quantize,
                grid_ticks=self._grid_ticks,
                offset_ms=self._offset_ms,
            )
            if fire_at <= now:
                self._emit_message(binding, raw)
            else:
                self._schedule_message(fire_at, binding, raw)
            return

        self._emit_message(binding, raw)

    def _drain_scheduled(self, now: float) -> int:
        sent = 0
        while self._scheduled and self._scheduled[0][0] <= now:
            _, _, binding, message = heapq.heappop(self._scheduled)
            self._emit_message(binding, message)
            sent += 1
        return sent

    def _drain_queue(self) -> int:
        if self._out is None:
            return 0
        now = time.monotonic()
        self._refresh_clock(now)
        self._refresh_floor()
        sent = self._drain_scheduled(now)
        self._process_fade_request()
        while True:
            try:
                binding, raw = self._queue.get_nowait()
            except queue.Empty:
                break
            self._handle_incoming(binding, raw, now)
        sent += self._drain_scheduled(time.monotonic())
        return sent

    def _close_inputs(self) -> None:
        # Release anything a translator is holding before the port goes
        # away, or a yanked cable leaves a note sounding with no source
        # left to send its note-off.
        for binding in self._bindings:
            for msg in binding.reset():
                self._send(msg)
        self._bindings = []
        for midi_in in self._inputs:
            try:
                midi_in.close_port()
            except Exception:
                pass
        self._inputs.clear()
        self._connected_port_names = ()

    def _open_inputs(self, probe) -> int:
        import rtmidi

        ports = list(probe.get_ports())
        opened = 0
        connected: list[str] = []
        self._bindings = []
        # One place decides which ports are wanted. This used to compute
        # its own selection, and an exclusion added to the other call site
        # silently did not apply here -- the router kept binding a port the
        # operator had excluded, with nothing in the log to say so.
        wanted = set(self._selected_port_names(ports))
        for index, name in enumerate(ports):
            if name not in wanted:
                continue
            classification = classify_port(
                name, override=override_for(name, MIDI_OVERRIDES)
            )
            midi_in = rtmidi.MidiIn()
            try:
                midi_in.open_port(index)
            except Exception as exc:
                print(f"Warning: could not open MIDI in {name!r}: {exc}", flush=True)
                continue
            binding = bind_source(
                name,
                classification,
                on_promote=lambda port: print(
                    f"  Reclassified: {port} → mpe "
                    "(per-note bend on separate channels; it never announced "
                    "itself, so it was bound as classic)",
                    flush=True,
                ),
            )
            midi_in.set_callback(self._make_callback(binding))
            self._inputs.append(midi_in)
            self._bindings.append(binding)
            opened += 1
            connected.append(name)
            print(
                f"  Listening: {name}  [{classification.kind}: {classification.reason}]",
                flush=True,
            )
        self._connected_port_names = tuple(sorted(connected))
        return opened

    def _selected_port_names(self, ports) -> list[str]:
        return select_router_ports(
            list(ports),
            route_classic=ROUTE_CLASSIC,
            is_mpe_port=is_roli_controller_port,
            extra_exclusions=EXTRA_EXCLUSIONS,
        )

    def _router_ports_on_bus(self, probe) -> tuple[str, ...]:
        return tuple(sorted(self._selected_port_names(probe.get_ports())))

    def _maybe_reconnect_inputs(self, probe) -> None:
        now = time.monotonic()
        if now < self._next_reconnect_check:
            return
        self._next_reconnect_check = now + RECONNECT_POLL_S

        desired = self._router_ports_on_bus(probe)
        action = reconnect_decision(
            desired, self._connected_port_names, have_inputs=bool(self._inputs)
        )
        if action == RECONNECT_IDLE:
            return
        if action == RECONNECT_CLOSE:
            print("No MIDI inputs on bus — closing stale inputs", flush=True)
            self._close_inputs()
            return

        print(
            f"MIDI port change {self._connected_port_names!r} → {desired!r} "
            "— reopening inputs",
            flush=True,
        )
        self._close_inputs()
        try:
            opened = self._open_inputs(probe)
        except Exception as exc:
            print(f"Warning: MIDI reopen failed ({exc!r}) — will retry", flush=True)
            return
        if opened == 0:
            print("Warning: ports seen on bus but no MIDI inputs opened", flush=True)

    def run(self) -> int:
        try:
            import rtmidi
        except ImportError:
            print("Error: python-rtmidi required for mpe-pressure-remap", file=sys.stderr)
            return 1

        # The ROLI-specific settle wait. Skipped when classic routing is on:
        # it blocks 15 s waiting for a device that may legitimately never
        # appear, and the reconnect poll now binds controllers whenever they
        # show up, so nothing is gained by blocking here. Measured on the
        # appliance 2026-08-28: with no ROLI attached this wait ran here AND
        # in the wrapper, delaying a classic-only startup by 32 s.
        if not ROUTE_CLASSIC:
            wait_script = REPO_ROOT / "scripts" / "wait-for-usb-midi.sh"
            if wait_script.is_file():
                subprocess.run(["bash", str(wait_script)], check=False)

        self._out = rtmidi.MidiOut()
        out_ports = list(self._out.get_ports())
        out_index = find_remap_output_port_index(out_ports)
        if out_index is None:
            print(
                f"Error: {REMAP_OUTPUT_PORT_NAME!r} not found in RtMidi outputs: {out_ports!r}",
                file=sys.stderr,
                flush=True,
            )
            return 1
        self._out.open_port(out_index)
        quantize_label = "off" if self._grid_ticks <= 0 else f"{self._grid_ticks} ticks"
        print(
            f"MPE pressure remap: output → {out_ports[out_index]!r} "
            f"(offset={self._offset_ms:.1f} ms, quantize={quantize_label}, "
            f"clock_through={'on' if self._pass_clock else 'off'})",
            flush=True,
        )

        probe = rtmidi.MidiIn()
        report = startup_report(
            self._open_inputs(probe), roli_on_bus=lsusb_has_roli()
        )
        if report:
            print(f"{report}", flush=True)

        try:
            while True:
                self._drain_queue()
                self._maybe_reconnect_inputs(probe)
                time.sleep(0.002)
        except KeyboardInterrupt:
            return 0
        finally:
            self._close_inputs()


def main() -> int:
    return PressureRemapDaemon().run()


if __name__ == "__main__":
    raise SystemExit(main())
