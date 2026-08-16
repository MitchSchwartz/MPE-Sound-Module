#!/usr/bin/env python3
"""Send MIDI clock (24 PPQN) to an external port — e.g. Boss RC-5 loop sync."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from patch_browser.midi_clock import (  # noqa: E402
    MIDI_CLOCK,
    MIDI_START,
    MIDI_STOP,
    find_clock_output_port_index,
    tick_interval_seconds,
)

DEFAULT_BPM = 120.0
MAX_CATCHUP_TICKS = 4
SLEEP_CAP_S = 0.01


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    return float(raw)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _open_output(port_index: int):
    import rtmidi

    midi_out = rtmidi.MidiOut()
    midi_out.open_port(port_index)
    return midi_out


def run_clock(
    *,
    bpm: float,
    port_index: int,
    auto_start: bool,
    run_seconds: float | None,
) -> int:
    import rtmidi

    probe = rtmidi.MidiOut()
    port_names = list(probe.get_ports())
    if port_index < 0 or port_index >= len(port_names):
        print(f"Error: port index {port_index} out of range: {port_names!r}", file=sys.stderr)
        return 1

    interval = tick_interval_seconds(bpm)
    midi_out = _open_output(port_index)
    print(
        f"MIDI clock → {port_names[port_index]!r} @ {bpm:g} BPM "
        f"({interval * 1000:.2f} ms/tick, 24 PPQN)",
        flush=True,
    )

    if auto_start:
        midi_out.send_message([MIDI_START])
        print("Sent MIDI Start (0xFA)", flush=True)

    deadline = time.monotonic() + run_seconds if run_seconds is not None else None
    next_tick = time.monotonic()

    try:
        while deadline is None or time.monotonic() < deadline:
            now = time.monotonic()
            if now >= next_tick:
                midi_out.send_message([MIDI_CLOCK])
                next_tick += interval
                catchup = 0
                while now >= next_tick and catchup < MAX_CATCHUP_TICKS:
                    midi_out.send_message([MIDI_CLOCK])
                    next_tick += interval
                    catchup += 1
                if catchup >= MAX_CATCHUP_TICKS:
                    next_tick = now + interval
            sleep_for = min(max(next_tick - time.monotonic(), 0.0), SLEEP_CAP_S)
            if sleep_for:
                time.sleep(sleep_for)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            midi_out.send_message([MIDI_STOP])
        except Exception:
            pass
        try:
            midi_out.close_port()
        except Exception:
            pass
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bpm",
        type=float,
        default=_env_float("MPE_MIDI_CLOCK_BPM", DEFAULT_BPM),
        help=f"Tempo (default env MPE_MIDI_CLOCK_BPM or {DEFAULT_BPM:g})",
    )
    parser.add_argument(
        "--port-substring",
        default=os.environ.get("MPE_MIDI_CLOCK_PORT", "").strip() or None,
        help="Prefer ALSA port name containing this substring",
    )
    parser.add_argument(
        "--port-index",
        type=int,
        default=None,
        help="Explicit RtMidi OUT port index (overrides auto-detect)",
    )
    parser.add_argument(
        "--list-ports",
        action="store_true",
        help="List RtMidi OUT ports and exit",
    )
    parser.add_argument(
        "--no-start",
        action="store_true",
        help="Do not send MIDI Start on launch",
    )
    parser.add_argument(
        "--seconds",
        type=float,
        default=None,
        help="Run for N seconds then exit (default: run until stopped)",
    )
    args = parser.parse_args()

    try:
        import rtmidi
    except ImportError:
        print("Error: python-rtmidi required for midi-clock-out", file=sys.stderr)
        return 1

    probe = rtmidi.MidiOut()
    port_names = list(probe.get_ports())

    if args.list_ports:
        for index, name in enumerate(port_names):
            skip = " (skipped)" if find_clock_output_port_index([name]) is None else ""
            print(f"{index}: {name}{skip}")
        return 0

    if args.port_index is not None:
        port_index = args.port_index
    else:
        port_index = find_clock_output_port_index(
            port_names, prefer_substring=args.port_substring
        )
    if port_index is None:
        print(f"Error: no suitable MIDI OUT port in {port_names!r}", file=sys.stderr)
        print("Plug in a USB MIDI interface and set MPE_MIDI_CLOCK_PORT if needed.", file=sys.stderr)
        return 1

    auto_start = _env_bool("MPE_MIDI_CLOCK_AUTO_START", True) and not args.no_start
    return run_clock(
        bpm=args.bpm,
        port_index=port_index,
        auto_start=auto_start,
        run_seconds=args.seconds,
    )


if __name__ == "__main__":
    raise SystemExit(main())
