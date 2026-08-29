#!/usr/bin/env python3
"""Measure what the APC's non-grid buttons can actually show.

Run this ON the appliance, with the looper session stopped so nothing else is
painting:

    mpe looper stop-session          # or: sudo systemctl stop mpe-looper-session
    python3 scripts/probe-apc-buttons.py

Why it exists: `device_facts.apc.scene.led_colours` and `.track.led_colours`
rest on a vendor document, and that same document's implication that Shift has
no LED is wrong — Mitch's device has an LED on every button. Rule 4 in
`device_facts` says a vendor-tier fact may never be used to call something
impossible. So these get measured, and this is the instrument.

It lights ONE CLASS AT A TIME so the answer is unmissable, steps on Enter, and
records what you type. Output is a markdown block ready to paste into
`device_facts.py` as MEASURED facts with today's date.

There is no automatic pass/fail here and there cannot be: the sensor is your
eyes. What the script guarantees is that every combination gets shown, in a
known order, with nothing else writing to the surface.
"""

from __future__ import annotations

import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "sooperlooper"))

from apc_panel import (  # noqa: E402
    NOTE_SHIFT_MK1,
    NOTE_SHIFT_MK2,
    SCENE_COLUMN_MK1,
    SCENE_COLUMN_MK2,
    TRACK_BUTTON_NOTES_MK1,
)

#: Channels worth trying. On the mk2 grid the channel selects brightness and
#: blink (a MEASURED fact — see device_facts.apc.grid.mk2_encoding). Whether
#: the buttons honour the same scheme is exactly what is unknown.
CHANNELS = (
    (0x90, "ch0 (mk1 normal / mk2 10% brightness)"),
    (0x96, "ch6 (mk2 100% brightness)"),
    (0x9D, "ch13 (mk2 blink 1/8)"),
)

#: Velocities: the mk1 semantic table, then mk2 palette entries for the
#: primaries. If the buttons are single-colour these all look the same.
VELOCITIES = (
    (0, "off"),
    (1, "mk1 green / mk2 palette 1"),
    (2, "mk1 green blink"),
    (3, "mk1 red"),
    (5, "mk1 yellow / mk2 palette RED"),
    (13, "mk2 palette YELLOW"),
    (21, "mk2 palette GREEN"),
    (127, "max"),
)


def classes(label: str):
    if label == "mk2":
        return [
            ("scene launch", list(SCENE_COLUMN_MK2)),
            ("track select", list(range(0x64, 0x6C))),
            ("shift", [NOTE_SHIFT_MK2]),
        ]
    return [
        ("scene launch", list(SCENE_COLUMN_MK1)),
        ("track select", list(TRACK_BUTTON_NOTES_MK1)),
        ("shift", [NOTE_SHIFT_MK1]),
    ]


def main() -> int:
    try:
        import rtmidi
    except ImportError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    midi_out = rtmidi.MidiOut()
    ports = midi_out.get_ports()
    idx = next((i for i, n in enumerate(ports) if "apc" in n.lower()), None)
    if idx is None:
        print("No APC port. Ports:", ports, file=sys.stderr)
        return 1
    midi_out.open_port(idx)
    name = ports[idx]
    label = "mk2" if ("mk2" in name.lower() or "mkii" in name.lower()) else "mk1"
    print(f"APC: {name}  ->  treating as {label}\n")
    print("Nothing else should be painting the surface. If the looper session "
          "is running, stop it and start over.\n")

    observations: list[tuple[str, str, str, str]] = []
    try:
        for class_name, notes in classes(label):
            for status, chan_desc in CHANNELS:
                for vel, vel_desc in VELOCITIES:
                    for note in notes:
                        midi_out.send_message([status, note, vel])
                        time.sleep(0.002)   # the APC stalls on bursts
                    prompt = (f"[{class_name}] {chan_desc}, velocity {vel} "
                              f"({vel_desc})\n  what do you see? > ")
                    try:
                        seen = input(prompt).strip()
                    except EOFError:
                        raise KeyboardInterrupt
                    observations.append(
                        (class_name, chan_desc, f"{vel} ({vel_desc})", seen)
                    )
                # Leave the class dark before the next channel.
                for note in notes:
                    midi_out.send_message([0x90, note, 0])
                    time.sleep(0.002)
    except KeyboardInterrupt:
        print("\n\nstopped early — partial results below\n")
    finally:
        for _class_name, notes in classes(label):
            for note in notes:
                midi_out.send_message([0x90, note, 0])
                time.sleep(0.002)

    print("\n\n--- paste into device_facts.py ---\n")
    print(f"Measured on {date.today().isoformat()}, device reported as {label}"
          f" ({name}).\n")
    print("| class | channel | velocity | observed |")
    print("|---|---|---|---|")
    for row in observations:
        print("| " + " | ".join(row) + " |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
