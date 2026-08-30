#!/usr/bin/env python3
"""Find Shift's lamp. Bisecting probe — your eyes are the sensor.

WHY A SECOND PROBE. `probe-apc-buttons.py` swept Shift across all 16 channels
at velocity 1 and 127 and nothing lit, and that was recorded as
`device_facts.apc.shift.led` — OPEN, against expectation, because Mitch owns
the device and states every button has an LED. OWNER outranks a negative
result (`device_facts` rule 4: a fact may never be used to call something
impossible).

Re-reading what was actually covered turned up the assumption nobody stated:
**every probe so far aimed at the note Shift SENDS (mk2 0x7A).** On a great
many controllers the note a button transmits and the note its lamp answers to
are different numbers. Thirty-nine notes have never been painted by anything
in this repo — 0x40-0x63, 0x6C-0x6F — and Shift's lamp could be sitting in
there answering to a number nobody has tried.

So this probe drops the assumption and searches the address space instead.

HOW IT WORKS. Bisection, not a linear sweep: light a whole batch, ask whether
anything came on, and halve. Thirty-nine candidates cost about five or six
questions instead of thirty-nine.

THE POSITIVE CONTROL COMES FIRST, and the probe refuses to continue without
it. Without it "nothing lit" cannot be told apart from "the probe never
transmitted" — which is this project's recurring bug shape, and it would be
particularly stupid to reproduce it inside the instrument built to settle the
question. If the known-good grid pad does not light, the answer is about the
probe, not about the hardware.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "sooperlooper"))

from control_registry import (  # noqa: E402
    ATTACHED_VARIANT,
    GRID_NOTE_MIN,
    note_index,
)
from apc_mode import (  # noqa: E402
    AKAI_MANUFACTURER_ID,
    SYSEX_END,
    SYSEX_START,
)

#: Swept in round 3 (2026-08-29), so not unexplored despite being unclaimed.
#: Exempted from the note-literal rule: prior probe coverage, not an address
#: the registry is entitled to own.
ROUND3_COVERED = frozenset(range(0x78, 0x80))

#: Never painted by any probe in this repo. Derived from the registry rather
#: than restated, so the claim is executable: whatever the registry does not
#: claim, minus what round 3 already swept. On the mk2 this is 0x40-0x63 and
#: 0x6C-0x6F. The grid, the track row and the scene column are claimed, so
#: they fall out on their own and cannot drift out of sync with this comment.
UNEXPLORED = [
    n for n in range(128)
    if n not in note_index(ATTACHED_VARIANT) and n not in ROUND3_COVERED
]

#: SysEx framing for the RGB message. Identity bytes, not control addresses —
#: same category as the exempted constants in `apc_mode.py`.
SYSEX_ALL_DEVICES = 0x7F
MK2_PRODUCT = 0x4F
RGB_MESSAGE_TYPE = 0x24
RGB_FULL = 0x7F      # one colour channel at full

#: Full brightness. 0x90 is 10% on the mk2 and reads as unlit in daylight,
#: which is its own way of producing a false negative.
STATUS_BRIGHT = 0x96
VEL_WHITE = 3      # a palette index that is bright on the grid


def _ask(prompt: str) -> bool:
    while True:
        try:
            answer = input(f"{prompt} [y/n] > ").strip().lower()
        except EOFError:
            raise KeyboardInterrupt
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False


def _paint(out, notes, on: bool) -> None:
    for note in notes:
        out.send_message([STATUS_BRIGHT if on else 0x90, note, VEL_WHITE if on else 0])
        time.sleep(0.002)   # the APC stalls on bursts


def _bisect(out, candidates: list[int]) -> list[int]:
    """Narrow a lit batch down to the notes actually responsible."""
    if len(candidates) <= 1:
        return candidates
    half = len(candidates) // 2
    left, right = candidates[:half], candidates[half:]
    _paint(out, left, True)
    lit = _ask(f"  painting {len(left)} notes ({hex(left[0])}-{hex(left[-1])}) "
               f"— is Shift lit?")
    _paint(out, left, False)
    if lit:
        return _bisect(out, left)
    _paint(out, right, True)
    lit = _ask(f"  painting {len(right)} notes ({hex(right[0])}-{hex(right[-1])}) "
               f"— is Shift lit?")
    _paint(out, right, False)
    if lit:
        return _bisect(out, right)
    return []          # it was the pair together, or the answer moved


def main() -> int:
    try:
        import rtmidi
    except ImportError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    out = rtmidi.MidiOut()
    ports = out.get_ports()
    idx = next((i for i, n in enumerate(ports) if "apc" in n.lower()), None)
    if idx is None:
        print("No APC port. Ports:", ports, file=sys.stderr)
        return 1
    out.open_port(idx)
    print(f"APC: {ports[idx]}\n")
    print("Stop the looper session first — nothing else may paint the surface:")
    print("    sudo systemctl stop mpe-looper-session.service\n")

    try:
        # --- positive control: refuse to interpret silence without it -------
        print("STEP 0 — positive control.")
        _paint(out, [GRID_NOTE_MIN], True)
        ok = _ask("  bottom-left GRID PAD should be lit now. Is it?")
        _paint(out, [GRID_NOTE_MIN], False)
        if not ok:
            print("\nSTOP. The probe is not reaching the device, so a dark "
                  "Shift would tell us nothing about the hardware.\n"
                  "Check the port, and that the session is stopped.")
            return 1
        print("  good — the probe transmits and you can see it.\n")

        # --- step 1: is the lamp anywhere in the unexplored space? ----------
        print(f"STEP 1 — painting all {len(UNEXPLORED)} never-tried notes at once.")
        print("  Look at SHIFT specifically. Other buttons may also light; "
              "ignore them.")
        _paint(out, UNEXPLORED, True)
        lit = _ask("  is SHIFT lit?")
        _paint(out, UNEXPLORED, False)

        if lit:
            print("\nSTEP 2 — narrowing down which note owns it.\n")
            found = _bisect(out, list(UNEXPLORED))
            if found:
                print(f"\n*** SHIFT LAMP IS NOTE {hex(found[0])} ***")
                print("Record at MEASURED tier in device_facts.apc.shift.led "
                      "and add led_writers to the `shift` row in "
                      "control_registry.")
                return 0
            print("\nNarrowing lost it — the lamp may need several notes, or "
                  "an answer changed. Re-run; step 1 alone is still a real "
                  "positive.")
            return 0

        # --- step 3: SysEx, the one addressing mode never aimed at Shift ----
        print("\nSTEP 3 — SysEx RGB at Shift's own note.")
        print("  This works on grid pads and was rejected by the track and "
              "scene buttons. It has never been aimed at Shift.")
        from control_registry import required_note
        shift_note = required_note("shift", ATTACHED_VARIANT)
        out.send_message([SYSEX_START, AKAI_MANUFACTURER_ID, SYSEX_ALL_DEVICES,
                          MK2_PRODUCT, RGB_MESSAGE_TYPE, 0x00, 0x08,
                          shift_note, shift_note,
                          0x00, RGB_FULL, 0x00, RGB_FULL, 0x00, RGB_FULL,
                          SYSEX_END])
        time.sleep(0.05)
        lit = _ask(f"  is SHIFT lit? (SysEx RGB at {hex(shift_note)})")
        if lit:
            print("\n*** SHIFT RESPONDS TO SYSEX RGB, NOT TO NOTE-ON ***")
            print("Record at MEASURED tier — this closes apc.shift.led.")
            return 0

        print("\nNo result. What this DOES establish, and it is not nothing:")
        print("  the lamp is not addressable by note-on across the whole "
              "128-note space, nor by SysEx RGB at its own note.")
        print("  Still untried, and the remaining live hypothesis: the lamp is")
        print("  firmware-owned and lights only in a device mode we never "
              "enter (see apc_mode.py — only Notes mode is decoded).")
        print("\nRecord this as a bounded negative, NOT as 'Shift has no LED'.")
        return 0
    except KeyboardInterrupt:
        print("\nstopped.")
        return 1
    finally:
        _paint(out, list(UNEXPLORED) + [GRID_NOTE_MIN], False)


if __name__ == "__main__":
    raise SystemExit(main())
