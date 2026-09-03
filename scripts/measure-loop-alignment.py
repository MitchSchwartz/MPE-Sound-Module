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

# SooperLooper loop states (its LoopState enum).
SL_STATE_WAIT_START = 1
SL_STATE_RECORDING = 2
SL_STATE_WAIT_STOP = 3
SL_STATE_PLAYING = 4
SL_STATE_OVERDUBBING = 5

# The loop input must see at least this (linear peak) for the record path to
# be provably live. Surge at normal level reads far above it; a disconnected
# input reads exactly 0.0.
INPUT_PEAK_MIN = 0.001

NOTE = 60
VELOCITY = 100
# Short, so the note-off release transient lands far from the overdub spacing.
# At 0.12 the release arrived ~100 ms after the note-on, a hair inside the
# 150 ms take->overdub gap, and every release was counted as an early overdub.
NOTE_LEN_S = 0.05

# A reading outside these is a broken instrument, not a slow result.
MAX_PLAUSIBLE_ERROR_MS = 60.0

# Minimum quiet span before a rising edge counts as a new onset, in beats.
# Must exceed NOTE_LEN_S (the release transient) and stay under the closest
# real spacing, which in overdub mode is half a beat.
ONSET_GAP_BEATS = 0.2

# The detector must find one onset per note played. More means it is counting
# something that is not a note; fewer means it is missing them. Either way the
# median describes a population that is not the notes.
ONSET_COUNT_SLACK = 1

# Where the overdub sits within the beat. Deliberately NOT 0.5: at half a beat
# the take->overdub and overdub->take intervals are (half + d) and (half - d),
# which are indistinguishable, so the two signs of the same displacement cancel
# and the median lands on whichever direction happens to be counted one more
# time. A 20 ms control read 18.3 ms with sd 21.7 for exactly that reason. At
# 0.3 the two gaps are 0.3 and 0.7 of a beat, so every interval declares which
# direction it is and both yield +d.
OVERDUB_SHIFT_BEATS = 0.3

# The loop must close on a whole number of beats for pass 2 to land in phase
# with pass 1. Asserted, never assumed.
LOOP_LEN_TOLERANCE_MS = 8.0


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

    def wait_for_state(self, loop: int, wanted: set[int], timeout: float, what: str) -> int:
        """Block until the loop reports one of *wanted*, or halt.

        With quantize=cycle a `record` hit does not start recording -- it queues
        it until the next CYCLE boundary, up to two seconds away at 120 BPM.
        Playing notes before that produced a loop of digital silence on the first
        live run. Never assume a transport command took effect; ask.
        """
        deadline = time.monotonic() + timeout
        last = -1.0
        while time.monotonic() < deadline:
            last = self.get_loop(loop, "state")
            if int(round(last)) in wanted:
                return int(round(last))
            time.sleep(0.05)
        raise Halt(
            f"loop {loop} never reached {what} (last state {last}) within {timeout}s"
        )

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
class ClockMaster:
    """Generates MIDI clock on Midi Through AND places the notes.

    midi-clock-out.service cannot be used here: it deliberately SKIPS any port
    matching "midi through" (see SKIP_CLOCK_PORT_SUBSTRINGS) because it exists to
    drive external gear with the Pi as master. Starting it would put no clock
    anywhere Surge, the router or SooperLooper can see it.

    Generating the clock here is also better for the measurement: the beat
    instants are known BY CONSTRUCTION rather than recovered by following, so the
    note placement carries no follower error at all. Delivery jitter still
    reaches SooperLooper, so it is measured and reported, and a jittery clock
    halts rather than quietly widening the result.
    """

    def __init__(self, bpm: float) -> None:
        import rtmidi

        from patch_browser.pressure_midi import (
            REMAP_OUTPUT_PORT_NAME,
            find_remap_output_port_index,
        )

        self._out = rtmidi.MidiOut()
        ports = list(self._out.get_ports())
        index = find_remap_output_port_index(ports)
        if index is None:
            raise Halt(f"{REMAP_OUTPUT_PORT_NAME!r} not among RtMidi outputs {ports!r}")
        self._out.open_port(index)
        self.out_port = ports[index]

        self.bpm = bpm
        self.beat_s = 60.0 / bpm
        self._tick_s = self.beat_s / PPQN
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.start_monotonic: float | None = None
        self._lateness: list[float] = []

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        # Wait for the first tick so start_monotonic is set before anyone asks.
        deadline = time.monotonic() + 5.0
        while self.start_monotonic is None and time.monotonic() < deadline:
            time.sleep(0.01)
        if self.start_monotonic is None:
            raise Halt("clock thread never emitted its first tick")

    def _run(self) -> None:
        self._out.send_message([MIDI_START_BYTE])
        t0 = time.monotonic()
        self.start_monotonic = t0
        n = 0
        while not self._stop.is_set():
            n += 1
            due = t0 + n * self._tick_s
            delay = due - time.monotonic()
            if delay > 0.001:
                time.sleep(delay - 0.0005)
            while time.monotonic() < due:
                pass
            self._out.send_message([MIDI_CLOCK_BYTE])
            self._lateness.append(time.monotonic() - due)
        try:
            self._out.send_message([MIDI_STOP_BYTE])
        except Exception:
            pass

    def jitter_ms(self) -> float:
        """Worst tick lateness so far, in ms. A jittery clock moves the loop
        boundary SooperLooper syncs to, which lands directly in the result."""
        if not self._lateness:
            return 0.0
        return max(self._lateness) * 1000.0

    def beat_at_or_after(self, when: float) -> float:
        if self.start_monotonic is None:
            raise Halt("clock has no start reference")
        n = math.ceil((when - self.start_monotonic) / self.beat_s)
        return self.start_monotonic + n * self.beat_s

    def note(self, on: bool) -> None:
        self._out.send_message([0x90 if on else 0x80, NOTE, VELOCITY if on else 0])

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        try:
            self._out.close_port()
        except Exception:
            pass


def _alsa_client_id(name_fragment: str) -> str | None:
    """ALSA sequencer client id whose name contains *name_fragment*."""
    out = subprocess.run(["aconnect", "-l"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if line.startswith("client ") and name_fragment.lower() in line.lower():
            return line.split()[1].rstrip(":")
    return None


# --------------------------------------------------------------------------
# Analysis
# --------------------------------------------------------------------------
def read_wav_mono(path: Path):
    """Mono float samples + rate from a RIFF file, PCM or IEEE float.

    Python's `wave` module refuses SooperLooper's output with
    "unknown format: 3" -- SooperLooper saves 32-bit IEEE FLOAT (format 3), which
    `wave` cannot read at all. The first version of this analyser used `wave` and
    died after a successful recording, which is the cheap kind of failure: it
    halted loudly instead of returning a number from a misread buffer.
    """
    import numpy as np

    raw = path.read_bytes()
    if raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        raise Halt(f"{path} is not a RIFF/WAVE file")

    fmt = None
    data = None
    pos = 12
    while pos + 8 <= len(raw):
        cid = raw[pos:pos + 4]
        size = int.from_bytes(raw[pos + 4:pos + 8], "little")
        body = raw[pos + 8:pos + 8 + size]
        if cid == b"fmt ":
            fmt = body
        elif cid == b"data":
            data = body
        pos += 8 + size + (size & 1)

    if fmt is None or data is None:
        raise Halt(f"{path} has no fmt/data chunk -- cannot be decoded")

    audio_format = int.from_bytes(fmt[0:2], "little")
    channels = int.from_bytes(fmt[2:4], "little")
    rate = int.from_bytes(fmt[4:8], "little")
    bits = int.from_bytes(fmt[14:16], "little")
    if audio_format == 0xFFFE and len(fmt) >= 26:      # WAVE_FORMAT_EXTENSIBLE
        audio_format = int.from_bytes(fmt[24:26], "little")

    if audio_format == 3 and bits == 32:
        samples = np.frombuffer(data, dtype="<f4").astype(np.float64)
    elif audio_format == 1 and bits == 16:
        samples = np.frombuffer(data, dtype="<i2").astype(np.float64) / 32768.0
    elif audio_format == 1 and bits == 32:
        samples = np.frombuffer(data, dtype="<i4").astype(np.float64) / 2147483648.0
    elif audio_format == 1 and bits == 24:
        b = np.frombuffer(data, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        ints = (b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16))
        ints[ints >= 1 << 23] -= 1 << 24
        samples = ints.astype(np.float64) / 8388608.0
    else:
        raise Halt(
            f"unsupported WAV encoding in {path}: format={audio_format} bits={bits}"
        )

    if channels > 1:
        usable = (samples.size // channels) * channels
        samples = samples[:usable].reshape(-1, channels).mean(axis=1)
    return samples, rate


def analyse(path: Path, beat_s: float, threshold_ratio: float = 0.25) -> dict:
    import numpy as np

    data, rate = read_wav_mono(path)
    if data.size == 0:
        raise Halt(f"{path} contains no audio -- the loop recorded nothing")

    peak = float(np.abs(data).max())
    if peak <= 0:
        raise Halt(f"{path} is digital silence -- nothing was recorded")
    norm = np.abs(data) / peak

    thresh = threshold_ratio
    above = norm > thresh
    beat_frames_i = beat_s * rate
    # Onset = a rising edge preceded by a quiet gap. The gap is BEAT-RELATIVE,
    # not a fixed 50 ms: at NOTE_LEN_S=0.12 every note-off released a transient
    # 100 ms after its note-on, and a 50 ms gap counted each note twice, which
    # dragged the median off every clean sample in the run.
    gap = max(int(0.01 * rate), int(ONSET_GAP_BEATS * beat_frames_i))
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
def analyse_overdub(path: Path, beat_s: float, threshold_ratio: float = 0.25) -> dict:
    """Measure pass 2 against pass 1, which is the question actually asked.

    "Onset position mod beat" measures SooperLooper's loop-start phase, and the
    first live run proved it: five onsets agreeing to sub-millisecond, all
    145.5 ms from the beat grid, because the loop simply did not begin on one of
    my beats. That number is real and tells you nothing about whether an overdub
    lands on the take under it.

    So: pass 1 on the beats, pass 2 on the offbeats, both in one loop. Every
    consecutive pair of onsets should be exactly half a beat apart. Whatever the
    loop's own start phase is, it is common to both passes and cancels. What is
    left is the misalignment a player would hear -- flam between the take and
    the overdub.
    """
    base = analyse(path, beat_s, threshold_ratio)
    positions = base["onset_positions_s"]
    if len(positions) < 4:
        raise Halt(
            f"only {len(positions)} onsets -- an overdub comparison needs both "
            "passes present, and fewer than four cannot show alternation"
        )

    short = OVERDUB_SHIFT_BEATS * beat_s              # take -> overdub
    long_ = (1.0 - OVERDUB_SHIFT_BEATS) * beat_s      # overdub -> next take
    tol = min(short, long_) * 0.45

    intervals = [b - a for a, b in zip(positions, positions[1:])]
    errors_ms: list[float] = []
    classified: list[str] = []
    for d in intervals:
        if abs(d - short) <= tol:
            errors_ms.append((d - short) * 1000.0)    # overdub late -> longer
            classified.append("short")
        elif abs(d - long_) <= tol:
            errors_ms.append((long_ - d) * 1000.0)    # overdub late -> shorter
            classified.append("long")
        else:
            classified.append("skip")                 # missed note, or a gap

    if len(errors_ms) < 3:
        raise Halt(
            f"only {len(errors_ms)} of {len(intervals)} onset intervals match "
            f"the {short * 1000:.0f}/{long_ * 1000:.0f} ms alternation -- the "
            "two passes are not interleaving, so there is nothing to compare"
        )

    base.update(
        {
            "mode": "overdub",
            "shift_beats": OVERDUB_SHIFT_BEATS,
            "expected_short_ms": round(short * 1000.0, 3),
            "expected_long_ms": round(long_ * 1000.0, 3),
            "intervals_ms": [round(d * 1000.0, 3) for d in intervals],
            "interval_kinds": classified,
            "errors_ms": [round(e, 3) for e in errors_ms],
            "median_error_ms": round(statistics.median(errors_ms), 3),
            "mean_error_ms": round(statistics.fmean(errors_ms), 3),
            "sd_ms": round(statistics.stdev(errors_ms), 3) if len(errors_ms) > 1 else None,
            "intervals_used": len(errors_ms),
        }
    )
    return base


def assert_onsets_match_notes(result: dict, notes_played: int) -> None:
    """PHYSICS ASSERTION: one onset per note, or the population is not the notes.

    This is the assertion whose absence cost two live runs. The detector was
    counting note-off release transients as onsets -- 16 and 17 events for 12
    notes -- and because a release sits a fixed distance after its note-on, the
    spurious intervals were CONSISTENT. The harness reported -39.6 ms with a
    sub-millisecond spread, and reported the identical -39.6 ms with a 20 ms
    control injected, because the median was dominated by events that no
    injection could move. Precision is not evidence; a blind instrument can be
    very precise about what it is not measuring.
    """
    found = result["onsets"]
    if abs(found - notes_played) > ONSET_COUNT_SLACK:
        raise Halt(
            f"{found} onsets for {notes_played} notes played (slack "
            f"{ONSET_COUNT_SLACK}) -- the detector is not counting notes, so "
            f"whatever the median describes, it is not their timing"
        )


def assert_loop_is_whole_beats(loop_seconds: float, beat_s: float) -> float:
    """PHYSICS ASSERTION. Pass 2 can only land in phase with pass 1 if the loop
    repeats in phase with the beat grid. If it does not, the overdub drifts by a
    different amount on every repetition and the median is meaningless."""
    beats = loop_seconds / beat_s
    err_ms = abs(beats - round(beats)) * beat_s * 1000.0
    if err_ms > LOOP_LEN_TOLERANCE_MS:
        raise Halt(
            f"loop is {loop_seconds:.4f}s = {beats:.4f} beats, {err_ms:.1f} ms "
            f"from a whole beat (max {LOOP_LEN_TOLERANCE_MS}) -- it does not "
            "repeat in phase with the grid, so an overdub cannot be in phase "
            "with the take either"
        )
    return err_ms


def assert_looper_hears_surge(sl: "SL", cn: "ClockMaster", loop: int) -> float:
    """POSITIVE CONTROL on the record path, before anything is recorded.

    A loop whose audio input is disconnected records digital silence, and so
    does a loop whose record never armed. Those two failures reached the
    analyser through the SAME channel -- an empty WAV -- so the harness could
    say "silence" without being able to say why. It cost a full live run.

    SooperLooper's in_peak_meter reads the signal AT the loop's input, which is
    exactly the junction in question. Fire notes, require the meter to move.
    If it does not, ask Surge's own peak meter which half is broken, so the
    halt names the fault instead of describing it.
    """
    sl.get_loop(loop, "in_peak_meter")          # reset the peak-hold
    peak = 0.0
    for _ in range(4):
        cn.note(True)
        time.sleep(0.12)
        cn.note(False)
        time.sleep(0.18)
        peak = max(peak, sl.get_loop(loop, "in_peak_meter"))
        if peak >= INPUT_PEAK_MIN:
            return peak

    surge = _surge_peak_linear()
    if surge is not None and surge >= INPUT_PEAK_MIN:
        raise Halt(
            f"loop {loop} input peak {peak:.5f} but Surge output peak {surge:.5f} "
            f"-- Surge is making sound and the looper cannot hear it: the record "
            f"path is disconnected. Repair: "
            f"bash scripts/sooperlooper/wire-jack-graph.sh connect"
        )
    raise Halt(
        f"loop {loop} input peak {peak:.5f} (< {INPUT_PEAK_MIN}) and Surge output "
        f"peak {'unreadable' if surge is None else format(surge, '.5f')} -- no audio "
        f"is being produced at all; check that a patch is loaded and Surge is voiced"
    )


def _surge_peak_linear() -> "float | None":
    """Surge's output level from the peak meter's state file, or None if the
    meter cannot be read. None means UNKNOWN and is never treated as zero."""
    for path in (Path("/run/mpe/meter.state"), Path("/tmp/mpe-peak-meter.state")):
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                key, _, value = line.partition("=")
                if key.strip() == "peak_linear":
                    return float(value.strip())
        except Exception:
            continue
    return None


def play_pass(cn: "ClockMaster", args, beat_s: float, offset_ms: float,
              shift_s: float, inject_ms: float = 0.0) -> list:
    """Place `args.notes` notes on successive beats, shifted by `shift_s`.

    Fired at exactly the instant plan_fire_at would choose: the beat, plus the
    configured output offset. `shift_s` is what separates the two passes -- 0
    for the take, half a beat for the overdub.
    """
    placed = []
    first = cn.beat_at_or_after(time.monotonic() + 1.5)
    for i in range(args.notes):
        target = first + i * beat_s + shift_s
        fire_at = target + (offset_ms + inject_ms) / 1000.0
        while time.monotonic() < fire_at - 0.002:
            time.sleep(0.0005)
        while time.monotonic() < fire_at:
            pass
        cn.note(True)
        placed.append(target)
        time.sleep(NOTE_LEN_S)
        cn.note(False)
    return placed


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
    ap.add_argument(
        "--mode",
        choices=("overdub", "phase"),
        default="overdub",
        help="overdub: measure pass 2 against pass 1 (the answerable question). "
             "phase: raw onset position mod beat, which also measures the loop's "
             "own start phase and cannot separate the two.",
    )
    ap.add_argument("--bpm", type=float, default=120.0)
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

        stage = "wire-looper"
        # SooperLooper's MIDI input is not connected to anything on this
        # appliance, so it can never see a clock as shipped. Wire it for the
        # duration and take the wire out again afterwards.
        through = _alsa_client_id("Midi Through")
        sl_client = _alsa_client_id("sooperlooper") or _alsa_client_id("mpe-looper")
        if through is None or sl_client is None:
            raise Halt(
                f"cannot find ALSA clients to wire (through={through}, sl={sl_client}) "
                "-- SooperLooper cannot sync to a clock it never receives"
            )
        rc = subprocess.run(
            ["aconnect", f"{through}:0", f"{sl_client}:0"],
            capture_output=True, text=True,
        )
        wired = rc.returncode == 0
        restore["wired"] = (through, sl_client, wired)

        stage = "clock-start"
        cn = ClockMaster(args.bpm)
        cn.start()
        clock_started = True
        beat_s = cn.beat_s
        time.sleep(1.0)
        if cn.jitter_ms() > 3.0:
            raise Halt(
                f"clock delivery jitter {cn.jitter_ms():.2f} ms -- the loop boundary "
                "SooperLooper syncs to would move by more than the effect being measured"
            )

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
            clock_jitter_ms=round(cn.jitter_ms(), 3),
            midi_wired=restore.get("wired", ("?", "?", False))[2],
        )

        stage = "input-check"
        in_peak = assert_looper_hears_surge(sl, cn, args.loop)
        _sentinel("loop-align-input-live", loop_in_peak=round(in_peak, 5))

        stage = "record"
        sl.hit(args.loop, "reset")
        time.sleep(0.5)
        sl.hit(args.loop, "record")
        state = sl.wait_for_state(
            args.loop, {SL_STATE_RECORDING}, timeout=8.0, what="Recording"
        )
        _sentinel("loop-align-recording", state=state)

        # Place notes on successive beats. Emitted at exactly the instant
        # plan_fire_at would choose: the beat, shifted by the offset. The first
        # is a full beat after recording is CONFIRMED live, not after the hit.
        # The take carries no injection: in overdub mode the injection is the
        # control, and it must appear as a DIFFERENCE between the passes.
        placed = play_pass(
            cn, args, beat_s, offset_ms, shift_s=0.0,
            inject_ms=0.0 if args.mode == "overdub" else args.inject_ms,
        )

        # Let the last note ring, then close the loop on a cycle boundary and
        # wait for the transport to actually leave the recording states -- saving
        # mid-record is another way to get a file that is not what it claims.
        time.sleep(beat_s * 1.5)
        sl.hit(args.loop, "record")
        sl.wait_for_state(
            args.loop,
            {SL_STATE_PLAYING, 0, 10},
            timeout=10.0,
            what="a settled (non-recording) state",
        )
        time.sleep(0.5)

        overdub_placed = []
        if args.mode == "overdub":
            stage = "overdub"
            sl.hit(args.loop, "overdub")
            od_state = sl.wait_for_state(
                args.loop, {SL_STATE_OVERDUBBING}, timeout=8.0, what="Overdubbing"
            )
            _sentinel("loop-align-overdubbing", state=od_state)
            overdub_placed = play_pass(
                cn, args, beat_s, offset_ms, shift_s=OVERDUB_SHIFT_BEATS * beat_s,
                inject_ms=args.inject_ms,
            )
            time.sleep(beat_s * 1.5)
            sl.hit(args.loop, "overdub")
            sl.wait_for_state(
                args.loop,
                {SL_STATE_PLAYING, 0, 10},
                timeout=10.0,
                what="a settled (non-overdubbing) state",
            )
            time.sleep(0.5)

        stage = "save"
        sl.save_loop(args.loop, args.wav)

        stage = "analyse"
        if args.mode == "overdub":
            result = analyse_overdub(args.wav, beat_s)
        else:
            result = analyse(args.wav, beat_s)
            result["mode"] = "phase"
        assert_onsets_match_notes(result, len(placed) + len(overdub_placed))
        result["loop_phase_error_ms"] = round(
            assert_loop_is_whole_beats(result["loop_seconds"], beat_s), 3
        )
        result["overdub_notes_placed"] = len(overdub_placed)
        result["clock_jitter_ms_final"] = round(cn.jitter_ms(), 3)
        result["offset_ms_applied"] = round(offset_ms, 3)
        result["inject_ms"] = args.inject_ms
        result["notes_placed"] = len(placed)
        result["clock_jitter_ms"] = round(cn.jitter_ms(), 3)
        result["bpm"] = args.bpm

        # Write the evidence BEFORE the assertions can reject it. A rejected
        # run is the one whose raw numbers are most worth having, and the first
        # version of this threw them away with the halt -- leaving a verdict of
        # "implausible" and nothing to diagnose it with.
        args.out.write_text(json.dumps(result, indent=2), encoding="utf-8")
        _sentinel(
            "loop-align-raw",
            median_error_ms=result["median_error_ms"],
            sd_ms=result["sd_ms"],
            onsets=result["onsets"],
            loop_seconds=result["loop_seconds"],
            first_onset_s=result["onset_positions_s"][0],
            out=str(args.out),
        )

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
        wire = restore.get("wired")
        if isinstance(wire, tuple) and wire[2]:
            subprocess.run(
                ["aconnect", "-d", f"{wire[0]}:0", f"{wire[1]}:0"],
                capture_output=True, text=True,
            )
        _sentinel(
            "loop-align-restored",
            clock_stopped=clock_started,
            unwired=bool(isinstance(wire, tuple) and wire[2]),
        )


if __name__ == "__main__":
    sys.exit(main())
