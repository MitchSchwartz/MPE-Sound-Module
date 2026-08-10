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
from patch_browser.pressure_midi import (  # noqa: E402
    REMAP_OUTPUT_PORT_NAME,
    find_remap_output_port_index,
    is_roli_controller_port,
    list_roli_input_port_names,
    remap_midi_message,
)

RECONNECT_POLL_S = 1.0
CLOCK_REFRESH_S = 0.05


class PressureRemapDaemon:
    def __init__(self) -> None:
        self._floor = PatchPressureStore.read_live_floor()
        self._live_mtime = 0.0
        self._out = None
        self._inputs: list = []
        self._queue: queue.SimpleQueue = queue.SimpleQueue()
        self._connected_roli_names: tuple[str, ...] = ()
        self._next_reconnect_check = 0.0
        self._scheduled: list[tuple[float, int, list[int]]] = []
        self._schedule_seq = 0
        self._offset_ms = resolve_output_offset_ms()
        self._grid_ticks = resolve_quantize_grid_ticks()
        self._clock_snap = read_clock_state()
        self._next_clock_refresh = 0.0
        self._pass_clock = clock_through_enabled()

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

    def _enqueue(self, message, _data=None) -> None:
        flat = prepare_incoming(message)
        if flat:
            self._queue.put(flat)

    def _schedule_message(self, fire_at: float, message: list[int]) -> None:
        self._schedule_seq += 1
        heapq.heappush(self._scheduled, (fire_at, self._schedule_seq, message))

    def _emit_message(self, message: list[int]) -> None:
        if self._out is None:
            return
        try:
            out_msg = remap_midi_message(message, self._floor)
        except (TypeError, ValueError, IndexError) as exc:
            print(f"Warning: skipping bad MIDI message {message!r}: {exc}", flush=True)
            return
        if out_msg:
            self._out.send_message(out_msg)

    def _handle_incoming(self, raw: list[int], now: float) -> None:
        if not raw:
            return
        status = raw[0]
        if self._pass_clock and is_realtime_clock_byte(status):
            self._emit_message(raw)
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
                self._emit_message(raw)
            else:
                self._schedule_message(fire_at, raw)
            return

        self._emit_message(raw)

    def _drain_scheduled(self, now: float) -> int:
        sent = 0
        while self._scheduled and self._scheduled[0][0] <= now:
            _, _, message = heapq.heappop(self._scheduled)
            self._emit_message(message)
            sent += 1
        return sent

    def _drain_queue(self) -> int:
        if self._out is None:
            return 0
        now = time.monotonic()
        self._refresh_clock(now)
        self._refresh_floor()
        sent = self._drain_scheduled(now)
        while True:
            try:
                raw = self._queue.get_nowait()
            except queue.Empty:
                break
            self._handle_incoming(raw, now)
        sent += self._drain_scheduled(time.monotonic())
        return sent

    def _close_inputs(self) -> None:
        for midi_in in self._inputs:
            try:
                midi_in.close_port()
            except Exception:
                pass
        self._inputs.clear()
        self._connected_roli_names = ()

    def _open_inputs(self, probe) -> int:
        import rtmidi

        ports = list(probe.get_ports())
        opened = 0
        connected: list[str] = []
        for index, name in enumerate(ports):
            if not is_roli_controller_port(name):
                continue
            midi_in = rtmidi.MidiIn()
            try:
                midi_in.open_port(index)
            except Exception as exc:
                print(f"Warning: could not open MIDI in {name!r}: {exc}", flush=True)
                continue
            midi_in.set_callback(self._enqueue)
            self._inputs.append(midi_in)
            opened += 1
            connected.append(name)
            print(f"  Listening: {name}", flush=True)
        self._connected_roli_names = tuple(sorted(connected))
        return opened

    def _roli_ports_on_bus(self, probe) -> tuple[str, ...]:
        return tuple(sorted(list_roli_input_port_names(list(probe.get_ports()))))

    def _maybe_reconnect_inputs(self, probe) -> None:
        now = time.monotonic()
        if now < self._next_reconnect_check:
            return
        self._next_reconnect_check = now + RECONNECT_POLL_S

        desired = self._roli_ports_on_bus(probe)
        if not desired:
            if self._connected_roli_names:
                print("ROLI disconnected — closing stale MIDI inputs", flush=True)
                self._close_inputs()
            return

        if desired == self._connected_roli_names and self._inputs:
            return

        print(
            f"ROLI port change {self._connected_roli_names!r} → {desired!r} — reopening inputs",
            flush=True,
        )
        self._close_inputs()
        opened = self._open_inputs(probe)
        if opened == 0:
            print("Warning: ROLI seen on bus but no MIDI inputs opened", flush=True)

    def run(self) -> int:
        try:
            import rtmidi
        except ImportError:
            print("Error: python-rtmidi required for mpe-pressure-remap", file=sys.stderr)
            return 1

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
        if self._open_inputs(probe) == 0:
            print("Error: no physical MIDI inputs opened", file=sys.stderr, flush=True)
            return 1

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
