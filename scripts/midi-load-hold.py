#!/usr/bin/env python3
"""Hold N simultaneous MPE voices — for capacity-curve ramp tests.

Usage: midi-load-hold.py SECONDS VOICES

Sends note-on on channels 1..VOICES at start, holds with deterministic MPE
modulation, then all-notes-off. No staggered retrigger — voice count is exact.
"""
import math
import sys
import time

import rtmidi

SECS = float(sys.argv[1]) if len(sys.argv) > 1 else 15.0
VOICES = int(sys.argv[2]) if len(sys.argv) > 2 else 8
if VOICES < 1 or VOICES > 15:
    print(f"VOICES must be 1..15 (got {VOICES})", file=sys.stderr)
    sys.exit(1)

BASE_NOTE = 48
CTRL_HZ = 50

out = rtmidi.MidiOut()
ports = out.get_ports()
idx = next((i for i, p in enumerate(ports) if "Midi Through" in p), None)
if idx is None:
    print(f"no Surge MIDI port in {ports}", file=sys.stderr)
    sys.exit(1)
out.open_port(idx)
print(f"midi-load-hold: -> {ports[idx]}  {VOICES} voices held, {SECS:.0f}s", flush=True)

notes = [BASE_NOTE + (v * 2) % 24 for v in range(VOICES)]
try:
    for v in range(VOICES):
        ch = v + 1
        out.send_message([0x90 | ch, notes[v], 100])
    t0 = time.monotonic()
    while True:
        el = time.monotonic() - t0
        if el >= SECS:
            break
        for v in range(VOICES):
            ch = v + 1
            pres = int(64 + 60 * math.sin(el * 2.0 + v))
            out.send_message([0xD0 | ch, max(0, min(127, pres))])
            bend = int(8192 + 3000 * math.sin(el * 1.3 + v * 0.7))
            out.send_message([0xE0 | ch, bend & 0x7F, (bend >> 7) & 0x7F])
        time.sleep(1.0 / CTRL_HZ)
finally:
    for v in range(VOICES):
        ch = v + 1
        out.send_message([0x80 | ch, notes[v], 0])
        out.send_message([0xB0 | ch, 123, 0])
    out.close_port()
    print("midi-load-hold: done", flush=True)
