#!/usr/bin/env python3
"""Record a controller's raw MIDI to a replayable file. READ ONLY.

Three uses, one tool (docs/CLASSIC-MIDI-PLAN.md §8):

  * ROLI baseline — the byte-identical regression stream for phase 2.
  * Classic golden stream — real device output, so translator tests stop
    resting on messages this repo made up.
  * Answering what a device actually sends, which beats reading its manual.

Writes JSONL: one {"t_ms", "msg"} per line, t_ms relative to the first message.
Replayable in tests with no hardware.

    capture-midi-stream.py --list
    capture-midi-stream.py --port LUMI --seconds 20 --out roli-baseline.jsonl

It only opens an input. It sends nothing, and it does not touch the routing —
whatever the device is already connected to keeps working while this records.

Channel/note summary at the end is the point for a dual-role device like an APC
in instrument mode: if its instrument notes arrive on a different channel or in
a different note range than its clip-launch notes, the two roles can be
separated at the wire. If they overlap exactly, they cannot, and the device
needs an explicit mode.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

STATUS_NAMES = {
    0x80: "note_off", 0x90: "note_on", 0xA0: "poly_at", 0xB0: "cc",
    0xC0: "program", 0xD0: "chan_at", 0xE0: "bend",
}


def summarise(rows: list[dict]) -> None:
    kinds: Counter[str] = Counter()
    channels: Counter[int] = Counter()
    notes: list[int] = []
    ccs: Counter[int] = Counter()
    velocities: list[int] = []
    for r in rows:
        msg = r["msg"]
        if not msg or msg[0] >= 0xF0:
            kinds["realtime/sysex"] += 1
            continue
        kind = msg[0] & 0xF0
        kinds[STATUS_NAMES.get(kind, hex(kind))] += 1
        channels[msg[0] & 0x0F] += 1
        if kind in (0x80, 0x90) and len(msg) >= 3:
            notes.append(msg[1])
            if kind == 0x90 and msg[2] > 0:
                velocities.append(msg[2])
        if kind == 0xB0 and len(msg) >= 2:
            ccs[msg[1]] += 1

    print(f"\n  messages: {len(rows)}")
    print(f"  kinds:    {dict(kinds)}")
    print(f"  channels: {sorted(c + 1 for c in channels)}  (1-based MIDI channels)")
    if notes:
        print(f"  notes:    {min(notes)}-{max(notes)}  ({len(set(notes))} distinct)")
    if velocities:
        distinct = sorted(set(velocities))
        fixed = len(distinct) == 1
        print(f"  velocity: {min(velocities)}-{max(velocities)}"
              f"  {'FIXED — pads are not velocity sensitive' if fixed else 'variable'}")
    if ccs:
        print(f"  CCs:      {sorted(ccs)}")
    if len(channels) > 1:
        print("\n  NOTE: more than one channel present — roles may be separable "
              "by channel.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--list", action="store_true", help="list input ports and exit")
    ap.add_argument("--port", help="substring of the input port name")
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    import rtmidi

    midi_in = rtmidi.MidiIn()
    ports = midi_in.get_ports()
    if args.list or not args.port:
        print("input ports:")
        for i, name in enumerate(ports):
            print(f"  [{i}] {name}")
        if not args.port:
            print("\nPick one with --port <substring>", file=sys.stderr)
        return 0

    idx = next((i for i, n in enumerate(ports) if args.port.lower() in n.lower()), None)
    if idx is None:
        print(f"no input port matching {args.port!r} in {ports}", file=sys.stderr)
        return 1

    rows: list[dict] = []
    t0: float | None = None

    def on_message(event, _data=None) -> None:
        nonlocal t0
        msg, _ = event
        now = time.perf_counter()
        if t0 is None:
            t0 = now
        rows.append({"t_ms": round((now - t0) * 1000.0, 3), "msg": list(msg)})

    midi_in.open_port(idx)
    midi_in.ignore_types(sysex=False, timing=False, active_sense=True)
    midi_in.set_callback(on_message)

    print(f"recording {ports[idx]!r} for {args.seconds:.0f}s — play now", flush=True)
    deadline = time.monotonic() + args.seconds
    while time.monotonic() < deadline:
        time.sleep(0.05)
    midi_in.cancel_callback()
    midi_in.close_port()

    if not rows:
        print("\nNOTHING CAPTURED — wrong port, or the device sent nothing.",
              file=sys.stderr)
        summarise(rows)
        return 2

    summarise(rows)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")
        print(f"\n  wrote {len(rows)} messages -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
