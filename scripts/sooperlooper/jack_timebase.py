#!/usr/bin/env python3
"""Task 0 spike only — JACK transport timebase master (not for production).

Verifies whether SooperLooper honours JACK BBT phase. Production clock should be a
compiled JACK client (see Documents/specs/looper-transport-clock-spec.md §G path).

This process runs a Python timebase callback on the JACK realtime thread — outside
DECISIONS.md audio/DSP ban in letter, but not policy-compliant for ship. Use only
for the Task 0 gate; do not leave running on the appliance.

Optional OSC on MPE_JACK_TIMEBASE_OSC_PORT (default 9960): ``/bpm <float>``.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
import time

DEFAULT_BPM = float(os.environ.get("MPE_LOOPER_BPM", "120"))
DEFAULT_METER = os.environ.get("MPE_LOOPER_METER", "4/4")
OSC_PORT = int(os.environ.get("MPE_JACK_TIMEBASE_OSC_PORT", "9960"))
CLIENT_NAME = os.environ.get("MPE_JACK_TIMEBASE_CLIENT", "mpe-timebase-spike")


def advance_tick_remainder(
    remainder: float,
    nframes: int,
    *,
    ticks_per_beat: float,
    bpm: float,
    frame_rate: float,
) -> tuple[float, int]:
    """Fractional tick accumulator — returns (new_remainder, whole_ticks_to_add)."""
    increment = nframes * ticks_per_beat * bpm / (frame_rate * 60.0)
    remainder += increment
    whole = int(remainder)
    remainder -= float(whole)
    return remainder, whole


def bbt_at_frame(
    frame: float,
    *,
    frame_rate: float,
    bpm: float,
    beats_per_bar: int,
    ticks_per_beat: float,
) -> tuple[int, int, int]:
    """Bar/beat/tick at absolute frame (1-indexed bar/beat)."""
    minutes = frame / (frame_rate * 60.0)
    abs_tick = minutes * bpm * ticks_per_beat
    abs_beat_f = abs_tick / ticks_per_beat
    bar0 = int(abs_beat_f / beats_per_bar)
    beat = int(abs_beat_f - (bar0 * beats_per_bar) + 1)
    tick = int(abs_tick - (int(abs_beat_f) * ticks_per_beat))
    return bar0 + 1, beat, tick


class TimebaseMaster:
    """BBT publisher for Task 0 spike. BPM is a plain float (GIL-atomic reads)."""

    def __init__(
        self,
        *,
        bpm: float = DEFAULT_BPM,
        meter: str = DEFAULT_METER,
        ticks_per_beat: int = 960,
    ) -> None:
        beats_per_bar, beat_type = (int(x) for x in meter.split("/", 1))
        self._beats_per_bar = beats_per_bar
        self._beat_type = beat_type
        self._ticks_per_beat = float(ticks_per_beat)
        self._bpm = max(1.0, min(999.0, float(bpm)))
        self._tick_remainder = 0.0

    def set_bpm(self, bpm: float) -> None:
        self._bpm = max(1.0, min(999.0, float(bpm)))

    @property
    def bpm(self) -> float:
        return self._bpm

    def activate(self, client, *, conditional: bool = False) -> None:
        import jack

        self._jack = jack

        @client.set_timebase_callback(conditional=conditional)
        def _callback(state, nframes, pos, new_pos) -> None:
            bpm = self.bpm
            pos.beats_per_minute = bpm
            pos.beats_per_bar = float(self._beats_per_bar)
            pos.beat_type = float(self._beat_type)
            pos.ticks_per_beat = self._ticks_per_beat
            pos.valid |= jack.POSITION_BBT

            if new_pos:
                self._tick_remainder = 0.0
                bar, beat, tick = bbt_at_frame(
                    float(pos.frame),
                    frame_rate=float(pos.frame_rate),
                    bpm=bpm,
                    beats_per_bar=self._beats_per_bar,
                    ticks_per_beat=self._ticks_per_beat,
                )
                pos.bar = bar
                pos.beat = beat
                pos.tick = tick
                pos.bar_start_tick = (bar - 1) * self._beats_per_bar * int(self._ticks_per_beat)
            else:
                self._tick_remainder, add = advance_tick_remainder(
                    self._tick_remainder,
                    nframes,
                    ticks_per_beat=self._ticks_per_beat,
                    bpm=bpm,
                    frame_rate=float(pos.frame_rate),
                )
                pos.tick += add
                tpb = int(pos.ticks_per_beat)
                while pos.tick >= tpb:
                    pos.tick -= tpb
                    pos.beat += 1
                    if pos.beat > pos.beats_per_bar:
                        pos.beat = 1
                        pos.bar += 1
                        pos.bar_start_tick += int(pos.beats_per_bar * pos.ticks_per_beat)


def _start_osc_server(master: TimebaseMaster, port: int) -> threading.Thread | None:
    try:
        from pythonosc import dispatcher as osc_dispatcher
        from pythonosc import osc_server
    except ImportError:
        print("jack-timebase: python-osc not installed; /bpm control disabled", flush=True)
        return None

    disp = osc_dispatcher.Dispatcher()
    disp.map("/bpm", lambda _addr, *args: master.set_bpm(float(args[0])) if args else None)

    server = osc_server.ThreadingOSCUDPServer(("127.0.0.1", port), disp)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"jack-timebase: OSC /bpm on 127.0.0.1:{port}", flush=True)
    return thread


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bpm", type=float, default=DEFAULT_BPM, help="Tempo (default: env MPE_LOOPER_BPM)")
    parser.add_argument("--meter", default=DEFAULT_METER, help="Time signature, e.g. 4/4")
    parser.add_argument("--no-start-transport", action="store_true", help="Do not roll JACK transport on start")
    parser.add_argument("--osc-port", type=int, default=OSC_PORT, help="OSC port for /bpm")
    args = parser.parse_args(argv)

    try:
        import jack
    except ImportError as exc:
        print(f"jack-timebase: {exc} (install python3-jack-client on Pi)", file=sys.stderr)
        return 1

    try:
        client = jack.Client(CLIENT_NAME)
    except jack.JackError as exc:
        print(f"jack-timebase: could not open JACK client: {exc}", file=sys.stderr)
        return 1

    master = TimebaseMaster(bpm=args.bpm, meter=args.meter)
    master.activate(client)
    client.activate()

    if not args.no_start_transport:
        client.transport_start()

    _start_osc_server(master, args.osc_port)
    print(
        f"jack-timebase [SPIKE]: {CLIENT_NAME} @ {master.bpm:.1f} BPM ({args.meter}); "
        f"transport={'rolling' if not args.no_start_transport else 'unchanged'}",
        flush=True,
    )

    stop = threading.Event()

    def _handle_sig(_signum, _frame) -> None:
        stop.set()

    signal.signal(signal.SIGINT, _handle_sig)
    signal.signal(signal.SIGTERM, _handle_sig)

    try:
        while not stop.is_set():
            time.sleep(0.25)
    finally:
        try:
            client.transport_stop()
        except Exception:
            pass
        try:
            client.deactivate()
            client.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
