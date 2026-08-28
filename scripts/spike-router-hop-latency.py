#!/usr/bin/env python3
"""Phase 0: what does the classic-MIDI router hop cost?

docs/CLASSIC-MIDI-PLAN.md phase 0. The router adds a Python process between
controller and Surge for devices that currently reach Surge directly. That cost
must be measured, not assumed — there is a production precedent (the ROLI path
already traverses a Python daemon) but no number for it.

Method: the same message travels two routes, alternating, on dedicated virtual
ports that touch neither Surge nor the APC.

    direct   src-direct -----------------------> dst
    hopped   src-hop --> forwarder(translate) --> dst

The delta between them IS the hop, and both routes share the receive callback
and the clock, so anything constant cancels.

TWO source ports, both connected once and left alone. An earlier version used
one source and ran aconnect immediately before each direct sample; that put
ALSA connection setup inside the direct arm, inflating it until the hop
measured NEGATIVE at p99. Connection churn is not part of the thing being
measured.

Alternating rather than one block each: a burst of CPU or a governor step that
lands on one whole arm would otherwise be read as the hop.

READ-ONLY with respect to the instrument: it opens its own virtual ports and
connects only those. It never writes to Midi Through, so Surge hears nothing.
"""

from __future__ import annotations

import argparse
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.midi_translate import ClassicToMpe  # noqa: E402

SRC_DIRECT = "mpe-lat-src-direct"
SRC_HOP = "mpe-lat-src-hop"
FWD_IN = "mpe-lat-fwd-in"
FWD_OUT = "mpe-lat-fwd-out"
DST = "mpe-lat-dst"


def pct(vals: list[float], p: float) -> float:
    if not vals:
        return float("nan")
    o = sorted(vals)
    return o[max(0, min(int(round(p / 100.0 * (len(o) - 1))), len(o) - 1))]


def client_port(name: str) -> str | None:
    """ALSA 'client:port' for a virtual port, via aconnect -l."""
    try:
        out = subprocess.run(["aconnect", "-l"], capture_output=True, text=True,
                             timeout=10).stdout
    except Exception:
        return None
    client = None
    for line in out.splitlines():
        if line.startswith("client "):
            client = line.split()[1].rstrip(":")
        elif name in line and client and "'" in line:
            return f"{client}:0"
    return None


def connect(a: str, b: str) -> bool:
    return subprocess.run(["aconnect", a, b], capture_output=True).returncode == 0


def disconnect(a: str, b: str) -> None:
    subprocess.run(["aconnect", "-d", a, b], capture_output=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--samples", type=int, default=200)
    ap.add_argument("--gap-ms", type=float, default=8.0)
    args = ap.parse_args()

    import rtmidi

    src_direct = rtmidi.MidiOut(); src_direct.open_virtual_port(SRC_DIRECT)
    src_hop = rtmidi.MidiOut(); src_hop.open_virtual_port(SRC_HOP)
    fwd_in = rtmidi.MidiIn(); fwd_in.open_virtual_port(FWD_IN)
    fwd_out = rtmidi.MidiOut(); fwd_out.open_virtual_port(FWD_OUT)
    dst = rtmidi.MidiIn(); dst.open_virtual_port(DST)
    time.sleep(0.5)

    ports = {n: client_port(n) for n in (SRC_DIRECT, SRC_HOP, FWD_IN, FWD_OUT, DST)}
    if not all(ports.values()):
        print(f"spike: FAIL — could not resolve ports: {ports}", file=sys.stderr)
        return 1

    translator = ClassicToMpe()

    def on_fwd(event, _d=None):
        msg, _ = event
        for out in translator.translate(list(msg)):
            fwd_out.send_message(out)

    fwd_in.set_callback(on_fwd)

    recv: list[float] = []

    def on_dst(event, _d=None):
        recv.append(time.perf_counter())

    dst.set_callback(on_dst)

    links = (
        (ports[SRC_HOP], ports[FWD_IN]),
        (ports[FWD_OUT], ports[DST]),
        (ports[SRC_DIRECT], ports[DST]),
    )
    for a, b in links:
        if not connect(a, b):
            print(f"spike: FAIL — aconnect {a} {b}", file=sys.stderr)
            return 1
    time.sleep(0.3)  # settle before the first sample

    direct, hopped = [], []
    note = 60
    for i in range(args.samples):
        use_direct = i % 2 == 0
        port = src_direct if use_direct else src_hop
        recv.clear()
        translator.all_notes_off()
        t0 = time.perf_counter()
        port.send_message([0x90, note, 100])
        deadline = t0 + 0.5
        while not recv and time.perf_counter() < deadline:
            time.sleep(0.0002)
        if recv:
            (direct if use_direct else hopped).append((recv[0] - t0) * 1000.0)
        port.send_message([0x80, note, 0])
        time.sleep(args.gap_ms / 1000.0)

    for a, b in links:
        disconnect(a, b)

    # Pure translate() cost, separate from any transport.
    t = ClassicToMpe()
    n = 20000
    t1 = time.perf_counter()
    for i in range(n):
        t.translate([0x90, 60 + (i % 12), 100])
        t.translate([0x80, 60 + (i % 12), 0])
    per_us = (time.perf_counter() - t1) / (2 * n) * 1e6

    print(f"samples: direct={len(direct)} hopped={len(hopped)}")
    if not direct or not hopped:
        print("spike: INCONCLUSIVE — one arm collected nothing", file=sys.stderr)
        return 2
    for name, vals in (("direct", direct), ("hopped", hopped)):
        print(f"  {name:<7} p50 {statistics.median(vals):6.3f} ms   "
              f"p99 {pct(vals, 99):6.3f} ms   max {max(vals):6.3f} ms")
    d50 = statistics.median(hopped) - statistics.median(direct)
    d99 = pct(hopped, 99) - pct(direct, 99)
    print(f"\n  HOP COST  p50 {d50:+.3f} ms   p99 {d99:+.3f} ms")
    print(f"  translate() alone: {per_us:.2f} us/message "
          f"({per_us / 1000:.4f} ms) — {'negligible' if per_us < 50 else 'NOT negligible'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
