#!/usr/bin/env python3
"""Deterministic MPE load generator — identical performance every run.

Removes the human variable from A/B audio measurements: same notes, same pressure
curves, same timing, so DSP load is reproducible between conditions.
"""
import sys, time, math
import rtmidi

SECS = float(sys.argv[1]) if len(sys.argv) > 1 else 75.0
VOICES = 3            # tuned to match Mitch playing: DSP median ~42
BASE_NOTE = 48
STEP_S = 0.5          # re-trigger a voice every 0.5 s, staggered
CTRL_HZ = 50          # pressure/bend updates per second per voice

out = rtmidi.MidiOut()
ports = out.get_ports()
idx = next((i for i, p in enumerate(ports) if "Midi Through" in p), None)
if idx is None:
    print(f"no Surge MIDI port in {ports}", file=sys.stderr); sys.exit(1)
out.open_port(idx)
print(f"midi-load: -> {ports[idx]}  {VOICES} voices, {SECS:.0f}s", flush=True)

t0 = time.monotonic()
last_note = [0.0] * VOICES
playing = [None] * VOICES
try:
    while True:
        now = time.monotonic()
        el = now - t0
        if el >= SECS:
            break
        for v in range(VOICES):
            ch = v + 1                      # MPE member channels 1..6
            if el - last_note[v] >= STEP_S * VOICES:
                if playing[v] is not None:
                    out.send_message([0x80 | ch, playing[v], 0])
                n = BASE_NOTE + (int(el / STEP_S) + v * 5) % 24
                out.send_message([0x90 | ch, n, 100])
                playing[v] = n
                last_note[v] = el
            # MPE expression: pressure + pitch bend, deterministic sine
            pres = int(64 + 60 * math.sin(el * 2.0 + v))
            out.send_message([0xD0 | ch, max(0, min(127, pres))])
            bend = int(8192 + 3000 * math.sin(el * 1.3 + v * 0.7))
            out.send_message([0xE0 | ch, bend & 0x7F, (bend >> 7) & 0x7F])
        time.sleep(1.0 / CTRL_HZ)
finally:
    for v in range(VOICES):
        if playing[v] is not None:
            out.send_message([0x80 | (v + 1), playing[v], 0])
        out.send_message([0xB0 | (v + 1), 123, 0])   # all notes off
    out.close_port()
    print("midi-load: done", flush=True)
