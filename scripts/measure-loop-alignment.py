#!/usr/bin/env python3
"""Where does a note ACTUALLY land in a recorded loop, relative to the beat?

THE QUESTION THIS ANSWERS
-------------------------
"Am I recording with the timing I think I'm recording?"

Everything before this measured LEGS and summed them. Twice that produced a
number that was internally consistent and wrong -- once because it used the
playback ringbuffer (downstream of where the looper taps the signal), once
because it added the DAC on top of that. Both survived instrument conformance,
because conformance proves a reading is true and never that it is the right
quantity.

So this measures no legs at all. It records a real loop through the real path
and asks where the audio physically landed. Any error in any model shows up
here, in one number, with nothing summed.

THE TRICK THAT MAKES IT SIMPLE
------------------------------
SooperLooper records synced, so the loop starts exactly on a cycle boundary,
which is a beat boundary. So for any note intended to land on a beat:

    alignment error = (onset position within the loop) mod (beat length)

wrapped to +/- half a beat. The loop's absolute beat index never has to be
known, and every note in the loop is an independent sample.

WHAT IT DOES NOT COVER
----------------------
The router's own scheduling. Notes are emitted here at the same instants
plan_fire_at would choose, so this measures whether THE OFFSET VALUE is right.
Whether the live router hits those instants is a separate question.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

OPT_IN_ENV = "MPE_ALLOW_LOOP_MEASURE"

OSC_HOST = os.environ.get("MPE_SL_OSC_HOST", "127.0.0.1")
OSC_PORT = int(os.environ.get("MPE_SL_OSC_PORT", "9951"))

SYNC_SOURCE_MIDI_CLOCK = -2.0
QUANTIZE_CYCLE = 1.0
PPQN = 24                      # MIDI clock ticks per quarter note
MIDI_CLOCK_BYTE = 0xF8
MIDI_START_BYTE = 0xFA
MIDI_STOP_BYTE = 0xFC

NOTE = 60
VELOCITY = 100
NOTE_LEN_S = 0.12

# A reading outside these is a broken instrument, not a slow result.
MAX_PLAUSIBLE_ERROR_MS = 60.0


class Halt(RuntimeError):
    """Any condition under which a number must NOT be reported."""


def _sentinel(name: str, **fields: object) -> None:
    parts = " ".join(f"{k}={v}" for k, v in fields.items())
    print(f"SENTINEL {name} {parts}".rstrip(), flush=True)


def _refuse_unless_opted_in() -> None:
    """This RESETS a loop and drives the looper. Never let it be reachable by
    accident: it would wipe whatever Mitch had recorded."""
    if os.environ.get(OPT_IN_ENV, "").strip() != "1":
        print(
            f"REFUSING TO RUN: {OPT_IN_ENV}=1 is not set.\n\n"
            "  This is a MEASUREMENT INSTRUMENT. It RESETS loop 0, starts a MIDI\n"
            "  clock, changes SooperLooper's sync source, and records over what is\n"
            "  there. It must never be invoked by a service or the UI.\n\n"
            f"      {OPT_IN_ENV}=1 python3 scripts/measure-loop-alignment.py ...\n",
            file=sys.stderr,
        )
        raise SystemExit(3)


# --------------------------------------------------------------------------
# SooperLooper OSC
# --------------------------------------------------------------------------
class SL:
    def __init__(self) -> None:
        from pythonosc import dispatcher, osc_server, udp_client

        self._client = udp_client.SimpleUDPClient(OSC_HOST, OSC_PORT)
        self._replies: dict[str, float] = {}
        self._event = threading.Event()

        disp = dispatcher.Dispatcher()
        disp.set_default_handler(self._on_reply)
        self._server = osc_server.ThreadingOSCUDPServer(("127.0.0.1", 0), disp)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        self._url = f"osc.udp://127.0.0.1:{self._server.server_address[1]}/"

    def _on_reply(self, _addr: str, *args: object) -> None:
        # SooperLooper replies (index, param, value).
        if len(args) >= 3 and isinstance(args[1], str):
            try:
                self._replies[args[1]] = float(args[2])  # type: ignore[arg-type]
            except (TypeError, ValueError):
                pass
        self._event.set()

    def ping(self, timeout: float = 2.0) -> None:
        self._event.clear()
        self._client.send_message("/ping", [self._url, "/reply"])
        if not self._event.wait(timeout):
            raise Halt(
                f"SooperLooper did not answer /ping on {OSC_HOST}:{OSC_PORT} -- "
                "no measurement can be attributed to a looper that is not there"
            )

    def get_global(self, param: str, timeout: float = 2.0) -> float:
        self._replies.pop(param, None)
        self._event.clear()
        self._client.send_message("/get", [param, self._url, "/reply"])
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if param in self._replies:
                return self._replies[param]
            time.sleep(0.02)
        raise Halt(f"no reply for global parameter {param!r}")

    def set_global(self, param: str, value: float) -> None:
        self._client.send_message("/set", [param, float(value)])

    def set_loop(self, loop: int, param: str, value: float) -> None:
        self._client.send_message(f"/sl/{loop}/set", [param, float(value)])

    def hit(self, loop: int, cmd: str) -> None:
        self._client.send_message(f"/sl/{loop}/hit", [cmd])

    def get_loop(self, loop: int, param: str, timeout: float = 2.0) -> float:
        self._replies.pop(param, None)
        self._event.clear()
        self._client.send_message(f"/sl/{loop}/get", [param, self._url, "/reply"])
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if param in self._replies:
                return self._replies[param]
            time.sleep(0.02)
        raise Halt(f"no reply for loop {loop} parameter {param!r}")

    def save_loop(self, loop: int, path: Path, timeout: float = 10.0) -> None:
        if path.exists():
            path.unlink()
        self._client.send_message(
            f"/sl/{loop}/save_loop",
            [str(path), "wav", "little", self._url, "/reply"],
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if path.exists() and path.stat().st_size > 1024:
                time.sleep(0.4)   # let the write finish
                return
            time.sleep(0.1)
        raise Halt(f"SooperLooper never wrote {path} -- nothing to analyse")


# --------------------------------------------------------------------------
# MIDI clock follower + note sender, on the SAME port the router uses
# --------------------------------------------------------------------------
class ClockAndNotes:
    def __init__(self) -> None:
        import rtmidi

        from patch_browser.pressure_midi import (
            REMAP_OUTPUT_PORT_NAME,
            find_remap_output_port_index,
        )

        self._out = rtmidi.MidiOut()
        ports = list(self._out.get_ports())
        index = find_remap_output_port_index(ports)
        if index is None:
            raise Halt(
                f"{REMAP_OUTPUT_PORT_NAME!r} not among RtMidi outputs {ports!r}"
            )
        self._out.open_port(index)
        self.out_port = ports[index]

        self._in = rtmidi.MidiIn()
        in_ports = list(self._in.get_ports())
        in_index = find_remap_output_port_index(in_ports)
        if in_index is None:
            raise Halt(f"no Midi Through INPUT among {in_ports!r} to follow the clock on")
        self._in.ignore_types(timing=False)     # clock is what we are here for
        self._in.open_port(in_index)
        self.in_port = in_ports[in_index]

        self._lock = threading.Lock()
        self.tick_count = 0
        self.last_beat_monotonic: float | None = None
        self._in.set_callback(self._on_midi)

    def _on_midi(self, event, _data=None) -> None:
        message, _delta = event
        if not message:
            return
        status = message[0]
        if status == MIDI_START_BYTE:
            with self._lock:
                self.tick_count = 0
                self.last_beat_monotonic = time.monotonic()
            return
        if status != MIDI_CLOCK_BYTE:
            return
        with self._lock:
            self.tick_count += 1
            if self.tick_count % PPQN == 0:
                self.last_beat_monotonic = time.monotonic()

    def wait_for_clock(self, timeout: float = 10.0) -> float:
        """Return measured beat length in seconds, or halt."""
        deadline = time.monotonic() + timeout
        with self._lock:
            start_ticks = self.tick_count
        while time.monotonic() < deadline:
            time.sleep(0.2)
            with self._lock:
                if self.tick_count - start_ticks >= PPQN * 2:
                    break
        else:
            raise Halt(
                "no MIDI clock arriving on Midi Through -- quantize cannot engage "
                "and there is no grid to measure against"
            )
        # Measure the beat length rather than trusting the configured tempo.
        with self._lock:
            t0 = self.last_beat_monotonic
            n0 = self.tick_count
        time.sleep(2.0)
        with self._lock:
            t1 = self.last_beat_monotonic
            n1 = self.tick_count
        if t0 is None or t1 is None or n1 <= n0:
            raise Halt("clock followed but no beat boundaries observed")
        beats = (n1 - n0) / PPQN
        if beats <= 0:
            raise Halt("clock tick count did not advance a whole beat")
        return (t1 - t0) / max(1, round(beats))

    def next_beat_after(self, when: float, beat_s: float) -> float:
        with self._lock:
            last = self.last_beat_monotonic
        if last is None:
            raise Halt("no beat reference -- clock follower never saw a beat")
        n = math.ceil((when - last) / beat_s)
        return last + n * beat_s

    def note(self, on: bool) -> None:
        self._out.send_message([0x90 if on else 0x80, NOTE, VELOCITY if on else 0])

    def close(self) -> None:
        for obj in (self._in, self._out):
            try:
                obj.close_port()
            except Exception:
                pass


# --------------------------------------------------------------------------
# Analysis
# --------------------------------------------------------------------------
def analyse(path: Path, beat_s: float, threshold_ratio: float = 0.25) -> dict:
    import numpy as np

    with wave.open(str(path), "rb") as wf:
        rate = wf.getframerate()
        channels = wf.getnchannels()
        width = wf.getsampwidth()
        frames = wf.readframes(wf.getnframes())

    if width != 2:
        dtype = {1: np.uint8, 4: np.int32}.get(width)
        if dtype is None:
            raise Halt(f"unsupported sample width {width} in {path}")
        data = np.frombuffer(frames, dtype=dtype).astype(np.float64)
    else:
        data = np.frombuffer(frames, dtype=np.int16).astype(np.float64)
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    if data.size == 0:
        raise Halt(f"{path} contains no audio -- the loop recorded nothing")

    peak = float(np.abs(data).max())
    if peak <= 0:
        raise Halt(f"{path} is digital silence -- nothing was recorded")
    norm = np.abs(data) / peak

    thresh = threshold_ratio
    above = norm > thresh
    # Onset = a rising edge preceded by at least 50 ms below threshold.
    gap = int(0.05 * rate)
    onsets: list[int] = []
    i = 0
    while i < above.size:
        if above[i]:
            if not onsets or (i - onsets[-1]) > gap:
                onsets.append(i)
            i += gap
        else:
            i += 1

    if not onsets:
        raise Halt(
            f"no onsets above {thresh:.2f} of peak in {path} -- the detector is "
            "blind, not the timing perfect"
        )

    beat_frames = beat_s * rate
    errors_ms = []
    for o in onsets:
        phase = o % beat_frames
        if phase > beat_frames / 2:
            phase -= beat_frames          # wrap to +/- half a beat
        errors_ms.append(phase * 1000.0 / rate)

    return {
        "file": str(path),
        "rate": rate,
        "loop_seconds": round(data.size / rate, 4),
        "beat_seconds": round(beat_s, 6),
        "onsets": len(onsets),
        "onset_positions_s": [round(o / rate, 4) for o in onsets],
        "errors_ms": [round(e, 3) for e in errors_ms],
        "median_error_ms": round(statistics.median(errors_ms), 3),
        "mean_error_ms": round(statistics.fmean(errors_ms), 3),
        "sd_ms": round(statistics.stdev(errors_ms), 3) if len(errors_ms) > 1 else None,
    }


# --------------------------------------------------------------------------
def main() -> int:
    _refuse_unless_opted_in()

    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", type=int, default=0)
    ap.add_argument("--notes", type=int, default=6, help="notes to place on beats")
    ap.add_argument(
        "--offset-ms",
        type=float,
        default=None,
        help="offset to test; default = what the appliance would apply",
    )
    ap.add_argument(
        "--inject-ms",
        type=float,
        default=0.0,
        help="POSITIVE CONTROL: extra shift that must appear in the result 1:1",
    )
    ap.add_argument("--out", type=Path, default=Path("/tmp/mpe-loop-align.json"))
    ap.add_argument("--wav", type=Path, default=Path("/tmp/mpe-loop-align.wav"))
    args = ap.parse_args()

    stage = "startup"
    sl = None
    cn = None
    restore: dict[str, object] = {}
    clock_started = False

    try:
        from patch_browser.midi_sync import resolve_output_offset_ms

        offset_ms = (
            args.offset_ms if args.offset_ms is not None else resolve_output_offset_ms()
        )

        stage = "sl-connect"
        sl = SL()
        sl.ping()

        stage = "clock-start"
        active = subprocess.run(
            ["systemctl", "is-active", "midi-clock-out"],
            capture_output=True, text=True,
        ).stdout.strip()
        restore["clock_was"] = active
        if active != "active":
            subprocess.run(["sudo", "systemctl", "start", "midi-clock-out"], check=False)
            clock_started = True
            time.sleep(2.0)

        stage = "midi-open"
        cn = ClockAndNotes()

        stage = "clock-follow"
        beat_s = cn.wait_for_clock()
        if not (0.1 < beat_s < 4.0):
            raise Halt(f"measured beat length {beat_s:.4f}s is not a musical tempo")

        stage = "sl-configure"
        restore["sync_source"] = sl.get_global("sync_source")
        restore["quantize"] = sl.get_global("quantize")
        sl.set_global("sync_source", SYNC_SOURCE_MIDI_CLOCK)
        sl.set_global("quantize", QUANTIZE_CYCLE)
        sl.set_loop(args.loop, "sync", 1.0)
        time.sleep(0.5)
        got = sl.get_global("sync_source")
        if abs(got - SYNC_SOURCE_MIDI_CLOCK) > 0.01:
            raise Halt(
                f"sync_source did not take (asked {SYNC_SOURCE_MIDI_CLOCK}, got {got}) "
                "-- the loop would not start on a beat and every phase is meaningless"
            )

        _sentinel(
            "loop-align-ready",
            beat_s=round(beat_s, 6),
            bpm=round(60.0 / beat_s, 2),
            offset_ms=round(offset_ms, 3),
            inject_ms=args.inject_ms,
            midi_out=json.dumps(cn.out_port),
            clock_started=clock_started,
        )

        stage = "record"
        sl.hit(args.loop, "reset")
        time.sleep(0.5)
        sl.hit(args.loop, "record")

        # Place notes on successive beats. Emitted at exactly the instant
        # plan_fire_at would choose: the beat, shifted by the offset.
        placed = []
        first = cn.next_beat_after(time.monotonic() + 1.5, beat_s)
        for i in range(args.notes):
            target = first + i * beat_s
            fire_at = target + (offset_ms + args.inject_ms) / 1000.0
            while time.monotonic() < fire_at - 0.002:
                time.sleep(0.0005)
            while time.monotonic() < fire_at:
                pass
            cn.note(True)
            placed.append(target)
            time.sleep(NOTE_LEN_S)
            cn.note(False)

        # Let the last note ring, then close the loop on a cycle boundary.
        time.sleep(beat_s * 1.5)
        sl.hit(args.loop, "record")
        time.sleep(beat_s * 2.0)

        stage = "save"
        sl.save_loop(args.loop, args.wav)

        stage = "analyse"
        result = analyse(args.wav, beat_s)
        result["offset_ms_applied"] = round(offset_ms, 3)
        result["inject_ms"] = args.inject_ms
        result["notes_placed"] = len(placed)

        if abs(result["median_error_ms"]) > MAX_PLAUSIBLE_ERROR_MS:
            raise Halt(
                f"median error {result['median_error_ms']} ms exceeds "
                f"{MAX_PLAUSIBLE_ERROR_MS} ms -- reject the instrument rather than "
                "explain the number"
            )
        if result["onsets"] < max(2, args.notes // 2):
            raise Halt(
                f"only {result['onsets']} onsets for {args.notes} notes -- the "
                "detector missed notes and the median is not trustworthy"
            )

        args.out.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result, indent=2))
        _sentinel(
            "loop-align-complete",
            median_error_ms=result["median_error_ms"],
            onsets=result["onsets"],
            sd_ms=result["sd_ms"],
        )
        return 0

    except Halt as exc:
        _sentinel("loop-align-aborted", stage=stage, reason=json.dumps(str(exc)))
        print(f"HALT [{stage}]: {exc}", file=sys.stderr, flush=True)
        return 2
    except Exception as exc:  # noqa: BLE001 - must not exit silently
        _sentinel("loop-align-aborted", stage=stage, reason=json.dumps(repr(exc)))
        print(f"HALT [{stage}] unexpected: {exc!r}", file=sys.stderr, flush=True)
        return 3
    finally:
        # Restore is not optional: this changed the looper's sync source and may
        # have started a clock service that was deliberately disabled.
        try:
            if sl is not None:
                if "sync_source" in restore:
                    sl.set_global("sync_source", float(restore["sync_source"]))  # type: ignore[arg-type]
                if "quantize" in restore:
                    sl.set_global("quantize", float(restore["quantize"]))  # type: ignore[arg-type]
        except Exception:
            pass
        if cn is not None:
            cn.close()
        if clock_started:
            subprocess.run(["sudo", "systemctl", "stop", "midi-clock-out"], check=False)
        _sentinel("loop-align-restored", clock_stopped=clock_started)


if __name__ == "__main__":
    sys.exit(main())
