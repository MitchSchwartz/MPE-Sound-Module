#!/usr/bin/env python3
"""Listen for MIDI clock from a looper pedal (Boss RC-5 USB) — looper as master."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from patch_browser.midi_clock import (  # noqa: E402
    MidiClockTracker,
    find_clock_input_port_index,
    write_clock_state,
)

RECONNECT_POLL_S = 1.0
STATE_WRITE_INTERVAL_S = 0.15


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    return float(raw)


class MidiClockInDaemon:
    def __init__(
        self,
        *,
        port_substring: str | None,
        port_index: int | None,
        stale_after_s: float,
    ) -> None:
        self.port_substring = port_substring
        self.port_index = port_index
        self.tracker = MidiClockTracker(stale_after_s=stale_after_s)
        self._midi_in = None
        self._connected_index: int | None = None
        self._connected_name: str | None = None
        self._next_reconnect = 0.0
        self._last_state_write = 0.0

    def _close_input(self) -> None:
        if self._midi_in is not None:
            # close_port() leaves the ALSA sequencer client allocated — see
            # the same fix in mpe-pressure-remap.py. This class reconnects on
            # a timer, so every retry against an absent pedal leaked one
            # client until ALSA's table filled and other services could not
            # open one at all.
            for step in ("cancel_callback", "close_port", "delete"):
                try:
                    getattr(self._midi_in, step)()
                except Exception:
                    pass
        self._midi_in = None
        self._connected_index = None
        self._connected_name = None
        self.tracker.set_port_name(None)

    def _open_input(self, probe, index: int, name: str) -> bool:
        import rtmidi

        midi_in = rtmidi.MidiIn()
        try:
            midi_in.ignore_types(sysex=False, timing=False, active_sense=True)
            midi_in.open_port(index)
        except Exception as exc:
            print(f"Warning: could not open MIDI in {name!r}: {exc}", flush=True)
            return False

        def _callback(message, _data=None) -> None:
            self.tracker.on_message(message)

        midi_in.set_callback(_callback)
        self._midi_in = midi_in
        self._connected_index = index
        self._connected_name = name
        self.tracker.set_port_name(name)
        print(f"MIDI clock in ← {name!r}", flush=True)
        return True

    def _desired_port(self, probe) -> tuple[int, str] | None:
        ports = list(probe.get_ports())
        if self.port_index is not None:
            if 0 <= self.port_index < len(ports):
                return self.port_index, ports[self.port_index]
            return None
        index = find_clock_input_port_index(
            ports, prefer_substring=self.port_substring
        )
        if index is None:
            return None
        return index, ports[index]

    def _maybe_reconnect(self, probe) -> None:
        now = time.monotonic()
        if now < self._next_reconnect:
            return
        self._next_reconnect = now + RECONNECT_POLL_S

        desired = self._desired_port(probe)
        if desired is None:
            if self._connected_index is not None:
                print("Looper MIDI port gone — waiting", flush=True)
                self._close_input()
            return

        index, name = desired
        if self._connected_index == index and self._midi_in is not None:
            return

        self._close_input()
        self._open_input(probe, index, name)

    def _publish_state(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and (now - self._last_state_write) < STATE_WRITE_INTERVAL_S:
            return
        self._last_state_write = now
        payload = self.tracker.snapshot(now=now)
        payload["role"] = "in"
        write_clock_state(payload)

    def run(self) -> int:
        try:
            import rtmidi
        except ImportError:
            print("Error: python-rtmidi required for midi-clock-in", file=sys.stderr)
            return 1

        probe = rtmidi.MidiIn()
        try:
            while True:
                self._maybe_reconnect(probe)
                self._publish_state()
                time.sleep(0.05)
        except KeyboardInterrupt:
            return 0
        finally:
            self._publish_state(force=True)
            self._close_input()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--port-substring",
        default=os.environ.get("MPE_MIDI_CLOCK_IN_PORT", "RC-5").strip() or None,
        help="Prefer ALSA IN port containing this substring (default RC-5)",
    )
    parser.add_argument(
        "--port-index",
        type=int,
        default=None,
        help="Explicit RtMidi IN port index",
    )
    parser.add_argument(
        "--list-ports",
        action="store_true",
        help="List RtMidi IN ports and exit",
    )
    parser.add_argument(
        "--stale-after",
        type=float,
        default=_env_float("MPE_MIDI_CLOCK_STALE_S", 3.0),
        help="Seconds without clock before synced=false",
    )
    args = parser.parse_args()

    try:
        import rtmidi
    except ImportError:
        print("Error: python-rtmidi required for midi-clock-in", file=sys.stderr)
        return 1

    probe = rtmidi.MidiIn()
    port_names = list(probe.get_ports())

    if args.list_ports:
        for index, name in enumerate(port_names):
            pick = find_clock_input_port_index([name])
            mark = " ← default" if pick == 0 else ""
            print(f"{index}: {name}{mark}")
        return 0

    daemon = MidiClockInDaemon(
        port_substring=args.port_substring,
        port_index=args.port_index,
        stale_after_s=args.stale_after,
    )
    return daemon.run()


if __name__ == "__main__":
    raise SystemExit(main())
