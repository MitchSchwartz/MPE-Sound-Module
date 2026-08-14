#!/usr/bin/env python3
"""JACK transport timebase master for SooperLooper grid sync.

Publishes bar/beat/tick (BBT) at a configurable BPM. Optional OSC control on
MPE_JACK_TIMEBASE_OSC_PORT (default 9960): ``/bpm <float>``.
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
CLIENT_NAME = os.environ.get("MPE_JACK_TIMEBASE_CLIENT", "mpe-timebase")


class TimebaseMaster:
    """Realtime-safe BBT publisher; BPM updates from the main thread only."""

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
        self._ticks_per_beat = int(ticks_per_beat)
        self._bpm = max(1.0, min(999.0, float(bpm)))
        self._bpm_lock = threading.Lock()

    def set_bpm(self, bpm: float) -> None:
        with self._bpm_lock:
            self._bpm = max(1.0, min(999.0, float(bpm)))

    def bpm(self) -> float:
        with self._bpm_lock:
            return self._bpm

    def activate(self, client, *, conditional: bool = False) -> None:
        import jack

        self._jack = jack

        @client.set_timebase_callback(conditional=conditional)
        def _callback(state, nframes, pos, new_pos) -> None:
            bpm = self.bpm()
            if new_pos:
                pos.beats_per_bar = float(self._beats_per_bar)
                pos.beats_per_minute = bpm
                pos.beat_type = float(self._beat_type)
                pos.ticks_per_beat = float(self._ticks_per_beat)
                pos.valid |= jack.POSITION_BBT

                minutes = pos.frame / (pos.frame_rate * 60.0)
                abs_tick = minutes * bpm * self._ticks_per_beat
                abs_beat = abs_tick / self._ticks_per_beat

                pos.bar = int(abs_beat / self._beats_per_bar)
                pos.beat = int(abs_beat - (pos.bar * self._beats_per_bar) + 1)
                pos.tick = int(abs_tick - (abs_beat * self._ticks_per_beat))
                pos.bar_start_tick = pos.bar * self._beats_per_bar * self._ticks_per_beat
                pos.bar += 1
            else:
                pos.tick += int(
                    nframes
                    * self._ticks_per_beat
                    * bpm
                    / (pos.frame_rate * 60.0)
                )
                while pos.tick >= pos.ticks_per_beat:
                    pos.tick -= int(pos.ticks_per_beat)
                    pos.beat += 1
                    if pos.beat > pos.beats_per_bar:
                        pos.beat = 1
                        pos.bar += 1
                        pos.bar_start_tick += self._beats_per_bar * self._ticks_per_beat


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
        f"jack-timebase: {CLIENT_NAME} @ {master.bpm():.1f} BPM ({args.meter}); "
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
