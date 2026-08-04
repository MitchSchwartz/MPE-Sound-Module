#!/usr/bin/env python3
"""MPE pressure-floor MIDI remapper — controllers → virtual port → Surge."""

from __future__ import annotations

import queue
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from patch_browser.patch_pressure import PatchPressureStore  # noqa: E402
from patch_browser.pressure_midi import (  # noqa: E402
    REMAP_OUTPUT_PORT_NAME,
    find_remap_output_port_index,
    is_roli_controller_port,
    list_roli_input_port_names,
    normalize_midi_bytes,
    remap_midi_message,
)

RECONNECT_POLL_S = 1.0


class PressureRemapDaemon:
    def __init__(self) -> None:
        self._floor = PatchPressureStore.read_live_floor()
        self._live_mtime = 0.0
        self._out = None
        self._inputs: list = []
        self._queue: queue.SimpleQueue = queue.SimpleQueue()
        self._connected_roli_names: tuple[str, ...] = ()
        self._next_reconnect_check = 0.0

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

    def _enqueue(self, message, _data=None) -> None:
        flat = normalize_midi_bytes(message)
        if flat:
            self._queue.put(flat)

    def _drain_queue(self) -> int:
        if self._out is None:
            return 0
        sent = 0
        self._refresh_floor()
        while True:
            try:
                raw = self._queue.get_nowait()
            except queue.Empty:
                break
            try:
                out_msg = remap_midi_message(raw, self._floor)
            except (TypeError, ValueError, IndexError) as exc:
                print(f"Warning: skipping bad MIDI message {raw!r}: {exc}", flush=True)
                continue
            if out_msg:
                self._out.send_message(out_msg)
                sent += 1
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
        print(
            f"MPE pressure remap: output → {out_ports[out_index]!r}",
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
