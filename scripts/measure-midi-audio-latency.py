#!/usr/bin/env python3
"""Measure Surge's MIDI-in -> audio-out latency, in JACK frames.

WHY THIS EXISTS
---------------
The looper-grid MIDI offset uses a MODEL: period x periods. On 2026-09-01 that
model was checked against `jack_lsp -l`, which reported exactly 192 frames for
`system:playback_*` at period=96 periods=2 -- the identical arithmetic. jackd
runs with no `-I`/`-O`, which default to 0, so JACK's figure is the model echoed
back, not a reading of anything. `Surge XT:out_1` likewise declares [ 0 192 ]:
Surge never calls jack_port_set_latency_range, so JACK does not know whether
Surge adds latency of its own. Comparing the model to either number is a
tautology.

This measures the one leg JACK cannot see: from a MIDI byte entering Surge on
the real ALSA path to the resulting audio appearing at Surge's JACK output port.
Both timestamps come from the SAME clock -- JACK's frame counter -- so there is
no wall-clock/file-origin confound.

WHAT IT DOES NOT MEASURE
------------------------
Surge's output port to the physical converter. JACK declares that leg as 192
frames and the DAC's own transfer/conversion time is declared as zero. Measuring
it needs a physical loopback (jack_iodelay, KA1 out -> Scarlett in). That is a
separate cell. The number here ADDS to those.

INSTRUMENT HAZARD, STATED UP FRONT
----------------------------------
An onset detector fires when the signal crosses a threshold, so a slow attack
reads as latency. That bias is real and is why every trial reports TWO onsets:
one at a floor threshold (3x the measured noise floor) and one at a trigger
threshold (10x). Their difference IS the attack contribution. If they diverge by
more than ATTACK_DIVERGENCE_HALT_MS the loaded patch is unfit for this
measurement and the run HALTS rather than reporting a biased number.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# Physics bounds. A reading outside these is not a slow result, it is a broken
# instrument, and the harness must reject it rather than report it (Step 0.4).
MAX_PLAUSIBLE_MS = 60.0
MIN_PLAUSIBLE_MS = 0.0
ATTACK_DIVERGENCE_HALT_MS = 5.0

# Noise floor above this and the onset detector cannot discriminate an attack
# from the background, so no threshold choice is defensible.
MAX_NOISE_FLOOR = 0.01

SILENCE_PROBE_S = 0.5
ONSET_TIMEOUT_S = 2.0
# A fixed settle is a guess about the patch's release tail, and the pilot proved
# the guess wrong on trial 2 (peak 0.253 still ringing after 0.25 s). Wait for
# measured silence instead, and halt if it never arrives -- a note that never
# decays means every subsequent onset would be attributed to the wrong trial.
SILENCE_POLL_S = 0.05
SILENCE_TIMEOUT_S = 8.0

NOTE = 60
VELOCITY = 100


class Halt(RuntimeError):
    """Any condition under which a number must NOT be reported."""


def _sentinel(name: str, **fields: object) -> None:
    parts = " ".join(f"{k}={v}" for k, v in fields.items())
    print(f"SENTINEL {name} {parts}".rstrip(), flush=True)


class LatencyProbe:
    def __init__(self, source_port: str, connect: bool = True) -> None:
        import jack
        import numpy as np

        self._np = np
        self._jack = jack
        self.client = jack.Client("mpe-lat-probe", no_start_server=True)
        self.inport = self.client.inports.register("in")
        self.source_port = source_port
        self._connect = connect

        self.rate = self.client.samplerate
        self.blocksize = self.client.blocksize

        self.xruns = 0
        self._lock = threading.Lock()
        self._armed = False
        self._floor_thresh = 0.0
        self._trig_thresh = 0.0
        self._floor_frame: int | None = None
        self._trig_frame: int | None = None
        self._peak = 0.0
        self._hit = threading.Event()

        self.client.set_process_callback(self._process)
        self.client.set_xrun_callback(self._on_xrun)

    def _on_xrun(self, delay_usecs: float) -> None:
        self.xruns += 1

    def _process(self, nframes: int) -> None:
        np = self._np
        buf = self.inport.get_array()
        mag = np.abs(buf)
        peak = float(mag.max()) if mag.size else 0.0
        with self._lock:
            if peak > self._peak:
                self._peak = peak
            if not self._armed:
                return
            base = self.client.last_frame_time
            if self._floor_frame is None:
                idx = np.flatnonzero(mag > self._floor_thresh)
                if idx.size:
                    self._floor_frame = base + int(idx[0])
            if self._trig_frame is None:
                idx = np.flatnonzero(mag > self._trig_thresh)
                if idx.size:
                    self._trig_frame = base + int(idx[0])
                    self._hit.set()

    def activate(self) -> None:
        self.client.activate()
        if not self._connect:
            # Negative control: leave the input unconnected on purpose. The run
            # MUST halt on the first trial. If it instead reports a number, the
            # instrument reports blindness as data and every result is void.
            return
        matches = self.client.get_ports(self.source_port, is_output=True, is_audio=True)
        if not matches:
            raise Halt(f"source port {self.source_port!r} not found in the graph")
        self.client.connect(matches[0], self.inport)

    def measure_noise_floor(self) -> float:
        with self._lock:
            self._peak = 0.0
        time.sleep(SILENCE_PROBE_S)
        with self._lock:
            floor = self._peak
        if floor > MAX_NOISE_FLOOR:
            raise Halt(
                f"noise floor {floor:.5f} exceeds {MAX_NOISE_FLOOR} -- an onset "
                "detector cannot discriminate an attack from this background"
            )
        return floor

    def set_thresholds(self, floor: float) -> tuple[float, float]:
        trig = max(floor * 10.0, 0.005)
        flr = max(floor * 3.0, 0.0015)
        with self._lock:
            self._floor_thresh = flr
            self._trig_thresh = trig
        return flr, trig

    def arm(self) -> None:
        with self._lock:
            if self._peak > self._trig_thresh:
                raise Halt(
                    f"signal present before arming (peak {self._peak:.5f}) -- a "
                    "previous note is still ringing; onset would be attributed "
                    "to the wrong trial"
                )
            self._floor_frame = None
            self._trig_frame = None
            self._peak = 0.0
            self._armed = True
        self._hit.clear()

    def disarm(self) -> None:
        with self._lock:
            self._armed = False

    def wait_for_onset(self) -> tuple[int, int]:
        if not self._hit.wait(ONSET_TIMEOUT_S):
            raise Halt(
                f"no onset within {ONSET_TIMEOUT_S}s -- the probe heard nothing. "
                "This is a blind instrument, not a zero-latency result."
            )
        with self._lock:
            trig = self._trig_frame
            flr = self._floor_frame
        if trig is None or flr is None:
            raise Halt("onset event set but frame not recorded -- instrument fault")
        return flr, trig

    def wait_for_silence(self) -> float:
        """Block until the release tail is provably below the floor threshold."""
        deadline = time.monotonic() + SILENCE_TIMEOUT_S
        while True:
            with self._lock:
                self._peak = 0.0
            time.sleep(SILENCE_POLL_S)
            with self._lock:
                peak = self._peak
            if peak < self._floor_thresh:
                return peak
            if time.monotonic() > deadline:
                raise Halt(
                    f"signal still at {peak:.5f} after {SILENCE_TIMEOUT_S}s of "
                    "note-off -- the patch does not decay, so trials cannot be "
                    "separated and no onset can be attributed to its own note"
                )

    def close(self) -> None:
        try:
            self.client.deactivate()
            self.client.close()
        except Exception:
            pass


def open_midi_out():
    import rtmidi

    from patch_browser.pressure_midi import (
        REMAP_OUTPUT_PORT_NAME,
        find_remap_output_port_index,
    )

    out = rtmidi.MidiOut()
    ports = list(out.get_ports())
    index = find_remap_output_port_index(ports)
    if index is None:
        raise Halt(
            f"{REMAP_OUTPUT_PORT_NAME!r} not among RtMidi outputs {ports!r} -- "
            "this is the port the router uses and the port Surge listens on"
        )
    out.open_port(index)
    return out, ports[index]


def run_trial(probe: LatencyProbe, midi_out, inject_s: float) -> dict:
    probe.arm()
    try:
        send_frame = probe.client.frame_time
        if inject_s > 0:
            # Positive control: a delay of known size inserted between the
            # timestamp and the send. It must appear in the reading at full
            # size, which is what proves the frame<->send mapping is calibrated
            # and not merely self-consistent.
            time.sleep(inject_s)
        midi_out.send_message([0x90, NOTE, VELOCITY])
        floor_frame, trig_frame = probe.wait_for_onset()
    finally:
        probe.disarm()
        midi_out.send_message([0x80, NOTE, 0])
    probe.wait_for_silence()

    rate = probe.rate
    trig_ms = (trig_frame - send_frame) * 1000.0 / rate
    floor_ms = (floor_frame - send_frame) * 1000.0 / rate
    attack_ms = trig_ms - floor_ms

    inject_ms = inject_s * 1000.0
    if not (MIN_PLAUSIBLE_MS <= floor_ms <= MAX_PLAUSIBLE_MS + inject_ms):
        raise Halt(
            f"latency {floor_ms:.3f} ms is outside the physically possible range "
            f"[{MIN_PLAUSIBLE_MS}, {MAX_PLAUSIBLE_MS + inject_ms}] -- reject the "
            "instrument, do not explain the number"
        )
    if attack_ms > ATTACK_DIVERGENCE_HALT_MS:
        raise Halt(
            f"attack ramp spans {attack_ms:.3f} ms between the floor and trigger "
            "thresholds -- the loaded patch is too slow for onset detection and "
            "the reading would be attack time, not latency"
        )

    return {
        "send_frame": send_frame,
        "floor_frame": floor_frame,
        "trig_frame": trig_frame,
        "floor_ms": round(floor_ms, 4),
        "trig_ms": round(trig_ms, 4),
        "attack_ms": round(attack_ms, 4),
    }


def summarise(trials: list[dict], key: str) -> dict:
    vals = [t[key] for t in trials]
    return {
        "n": len(vals),
        "min": round(min(vals), 4),
        "median": round(statistics.median(vals), 4),
        "max": round(max(vals), 4),
        "sd": round(statistics.stdev(vals), 4) if len(vals) > 1 else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=30)
    ap.add_argument("--source-port", default="Surge XT:out_1")
    ap.add_argument("--inject-ms", type=float, default=0.0)
    ap.add_argument("--label", default="cell")
    ap.add_argument("--negative-control", action="store_true")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    stage = "startup"
    probe = None
    midi_out = None
    try:
        probe = LatencyProbe(args.source_port, connect=not args.negative_control)

        stage = "activate"
        probe.activate()

        stage = "midi-open"
        midi_out, midi_port = open_midi_out()

        stage = "noise-floor"
        floor = probe.measure_noise_floor()
        flr_t, trig_t = probe.set_thresholds(floor)

        _sentinel(
            "probe-ready",
            label=args.label,
            rate=probe.rate,
            blocksize=probe.blocksize,
            noise_floor=f"{floor:.6f}",
            floor_thresh=f"{flr_t:.6f}",
            trig_thresh=f"{trig_t:.6f}",
            midi_port=json.dumps(midi_port),
        )

        stage = "trials"
        inject_s = args.inject_ms / 1000.0
        trials = []
        for i in range(args.trials):
            t = run_trial(probe, midi_out, inject_s)
            trials.append(t)
            print(
                f"  trial {i + 1:>3}/{args.trials}  "
                f"floor={t['floor_ms']:>8.3f} ms  trig={t['trig_ms']:>8.3f} ms  "
                f"attack={t['attack_ms']:>6.3f} ms",
                flush=True,
            )

        result = {
            "label": args.label,
            "rate": probe.rate,
            "blocksize": probe.blocksize,
            "inject_ms": args.inject_ms,
            "noise_floor": floor,
            "xruns_during_window": probe.xruns,
            "floor": summarise(trials, "floor_ms"),
            "trig": summarise(trials, "trig_ms"),
            "attack": summarise(trials, "attack_ms"),
            "trials": trials,
        }
        if args.out:
            args.out.write_text(json.dumps(result, indent=2), encoding="utf-8")

        print(json.dumps({k: v for k, v in result.items() if k != "trials"}, indent=2))
        _sentinel(
            "run-complete",
            label=args.label,
            n=len(trials),
            floor_median=result["floor"]["median"],
            xruns=probe.xruns,
        )
        return 0

    except Halt as exc:
        _sentinel("run-aborted", label=args.label, stage=stage, reason=json.dumps(str(exc)))
        print(f"HALT [{stage}]: {exc}", file=sys.stderr, flush=True)
        return 2
    except Exception as exc:  # noqa: BLE001 - must not exit silently
        _sentinel(
            "run-aborted", label=args.label, stage=stage, reason=json.dumps(repr(exc))
        )
        print(f"HALT [{stage}] unexpected: {exc!r}", file=sys.stderr, flush=True)
        return 3
    finally:
        if midi_out is not None:
            try:
                midi_out.send_message([0x80, NOTE, 0])
                midi_out.close_port()
            except Exception:
                pass
        if probe is not None:
            probe.close()


if __name__ == "__main__":
    sys.exit(main())
