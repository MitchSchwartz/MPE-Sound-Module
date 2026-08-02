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
    normalize_midi_bytes,
    remap_midi_message,
    should_skip_midi_port,
)


class PressureRemapDaemon:
    def __init__(self) -> None:
        self._floor = PatchPressureStore.read_live_floor()
        self._live_mtime = 0.0
        self._out = None
        self._inputs: list = []
        self._queue: queue.SimpleQueue = queue.SimpleQueue()

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
        ports = list(probe.get_ports())
        del probe

        opened = 0
        for index, name in enumerate(ports):
            if should_skip_midi_port(name):
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
            print(f"  Listening: {name}", flush=True)

        if opened == 0:
            print("Error: no physical MIDI inputs opened", file=sys.stderr, flush=True)
            return 1

        try:
            while True:
                self._drain_queue()
                time.sleep(0.002)
        except KeyboardInterrupt:
            return 0


def main() -> int:
    return PressureRemapDaemon().run()


if __name__ == "__main__":
    raise SystemExit(main())
