#!/usr/bin/env python3
"""Hold N simultaneous MPE voices — for capacity-curve ramp tests.

Usage: midi-load-hold.py SECONDS VOICES [--velocity N]

One note per MPE member channel (MIDI 2..16), held with deterministic MPE
modulation, then all-notes-off. No staggered retrigger — voice count is exact.
Quiet by default and never on the manager channel: patch_browser/measure_midi.py.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from patch_browser import measure_midi as mm  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("seconds", nargs="?", type=float, default=15.0)
    parser.add_argument("voices", nargs="?", type=int, default=8)
    mm.add_velocity_arg(parser)
    args = parser.parse_args()
    try:
        mm.check_voices(args.voices)
        mm.check_velocity(args.velocity)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1

    mm.release_on_sigterm()
    import rtmidi

    try:
        out, name = mm.open_through_port(rtmidi)
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"midi-load-hold: -> {name}  {args.voices} voices held, {args.seconds:.0f}s, "
          f"velocity {args.velocity}", flush=True)
    try:
        mm.hold(mm.Sender(out, velocity=args.velocity), args.voices, args.seconds)
    finally:
        out.close_port()
        print("midi-load-hold: done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
