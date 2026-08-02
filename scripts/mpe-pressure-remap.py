#!/usr/bin/env python3
"""MPE pressure-floor MIDI remapper — controllers → virtual port → Surge."""

from __future__ import annotations

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from patch_browser.patch_pressure import PatchPressureStore  # noqa: E402
from patch_browser.pressure_midi import (  # noqa: E402
    VIRTUAL_PORT_NAME,
    remap_midi_message,
    should_skip_midi_port,
)


class PressureRemapDaemon:
    def __init__(self) -> None:
        self._floor = PatchPressureStore.read_live_floor()
        self._live_mtime = 0.0
        self._out = None
        self._inputs: list = []

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

    def _forward(self, message, _data=None) -> None:
        if not message or self._out is None:
            return
        self._refresh_floor()
        out_msg = remap_midi_message(list(message), self._floor)
        self._out.send_message(out_msg)

    def run(self) -> int:
        try:
            import rtmidi
        except ImportError:
            print("Error: python-rtmidi required for mpe-pressure-remap", file=sys.stderr)
            return 1

        self._out = rtmidi.MidiOut()
        self._out.open_virtual_port(VIRTUAL_PORT_NAME)
        print(f"MPE pressure remap: virtual port {VIRTUAL_PORT_NAME!r}")

        probe = rtmidi.MidiIn()
        ports = probe.get_ports()
        del probe

        opened = 0
        for index, name in enumerate(ports):
            if should_skip_midi_port(name):
                continue
            midi_in = rtmidi.MidiIn()
            try:
                midi_in.open_port(index)
            except Exception as exc:
                print(f"Warning: could not open MIDI in {name!r}: {exc}")
                continue
            midi_in.set_callback(self._forward)
            self._inputs.append(midi_in)
            opened += 1
            print(f"  Listening: {name}")

        if opened == 0:
            print("Warning: no physical MIDI inputs opened — waiting for hotplug is not implemented", file=sys.stderr)

        try:
            while True:
                time.sleep(1.0)
        except KeyboardInterrupt:
            return 0


def main() -> int:
    return PressureRemapDaemon().run()


if __name__ == "__main__":
    raise SystemExit(main())
