#!/usr/bin/env python3
"""Deterministic MPE load generator — identical performance every run.

Usage: midi-load.py [SECONDS] [--velocity N]

Removes the human variable from A/B audio measurements: same notes, same pressure
curves, same timing, so DSP load is reproducible between conditions. Quiet by
default and never on the manager channel: patch_browser/measure_midi.py.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from patch_browser import measure_midi as mm  # noqa: E402

VOICES = 3  # tuned to match a human player: DSP median ~42


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("seconds", nargs="?", type=float, default=75.0)
    mm.add_velocity_arg(parser)
    args = parser.parse_args()
    try:
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
    print(f"midi-load: -> {name}  {VOICES} voices, {args.seconds:.0f}s, "
          f"velocity {args.velocity}", flush=True)
    try:
        mm.stagger(mm.Sender(out, velocity=args.velocity), VOICES, args.seconds)
    finally:
        out.close_port()
        print("midi-load: done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
