#!/usr/bin/env python3
"""Does `load_loop` halt playback GLOBALLY, or only the loop being loaded?

This decides how the seam weld can ever ship. Measured 2026-08-26 on Pi 5:
load_loop halts playback, so the SEAM_LOAD_LEAD_MS pre-load is a hole in the
audio before every wrap (600 ms lead = full dropout, 150 ms = audible pop).
There is no lead that is both longer than the load and inaudible.

  If the halt is PER-LOOP: the merged buffer can be loaded into a spare slot
  while the take keeps playing, and the wrap becomes a switch between two
  resident buffers — no I/O at the boundary, no hole, no tuning knob.

  If the halt is GLOBAL: file-based swapping cannot be made seamless at all,
  and the tail must be summed into the head some other way (e.g. an overdub
  pass) with no load_loop in the audio path.

Method: poll one playing loop's `loop_pos` continuously and look for stalls —
position freezing is the halt, and its duration is the hole. Three phases:

    baseline   nothing else happening       -> the polling noise floor
    other-loop load_loop into a spare slot  -> the question
    same-loop  load_loop into the poller    -> the known-positive control

The same-loop phase is what makes this readable: if it does NOT stall, the
instrument is blind and every other number is meaningless.

READ-ONLY on the take: it plays and loads only the loops named by --play-loop
and --spare-loop (default 1 and 13), never loop 0.

Usage: spike-load-halt.py --wav /tmp/mpe-seam-weld/merged-0-*.wav
Env: MPE_SL_OSC_HOST (127.0.0.1), MPE_SL_OSC_PORT (9951).
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import threading
import time

LISTEN_HOST = "127.0.0.1"
RETPATH = "/halt/reply"
POLL_S = 0.004


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--wav", required=True, help="WAV to load (any valid loop file)")
    ap.add_argument("--play-loop", type=int, default=1)
    ap.add_argument("--spare-loop", type=int, default=13)
    ap.add_argument("--record-s", type=float, default=2.0)
    ap.add_argument("--phase-s", type=float, default=2.5)
    args = ap.parse_args()

    if not os.path.exists(args.wav):
        print(f"spike-load-halt: FAIL — no such WAV: {args.wav}", file=sys.stderr)
        return 1

    from pythonosc import dispatcher, osc_server, udp_client

    host = os.environ.get("MPE_SL_OSC_HOST", "127.0.0.1")
    port = int(os.environ.get("MPE_SL_OSC_PORT", "9951"))

    samples: list[tuple[float, float]] = []
    lock = threading.Lock()

    def on_reply(_addr: str, _loop: int, _ctrl: str, value: float) -> None:
        with lock:
            samples.append((time.monotonic(), float(value)))

    disp = dispatcher.Dispatcher()
    disp.map(RETPATH, on_reply)
    server = osc_server.ThreadingOSCUDPServer((LISTEN_HOST, 0), disp)
    returl = f"{LISTEN_HOST}:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    client = udp_client.SimpleUDPClient(host, port)

    def poll_for(seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            client.send_message(
                f"/sl/{args.play_loop}/get", ["loop_pos", returl, RETPATH]
            )
            time.sleep(POLL_S)

    def stalls_since(mark: int) -> tuple[float, float]:
        """Longest and median gap between position CHANGES after index `mark`."""
        with lock:
            rows = samples[mark:]
        gaps, last_t, last_v = [], None, None
        for t, v in rows:
            if last_v is None or v != last_v:
                if last_t is not None:
                    gaps.append(t - last_t)
                last_t, last_v = t, v
        if not gaps:
            return (float("nan"), float("nan"))
        return (max(gaps) * 1000.0, statistics.median(gaps) * 1000.0)

    print(f"recording {args.record_s:.1f}s into loop {args.play_loop} …")
    client.send_message(f"/sl/{args.play_loop}/hit", ["record"])
    time.sleep(args.record_s)
    client.send_message(f"/sl/{args.play_loop}/hit", ["record"])
    time.sleep(0.4)

    results = {}
    for name, action in (
        ("baseline", None),
        ("other-loop", args.spare_loop),
        ("same-loop", args.play_loop),
    ):
        with lock:
            mark = len(samples)
        if action is None:
            poll_for(args.phase_s)
        else:
            t = threading.Thread(target=poll_for, args=(args.phase_s,))
            t.start()
            time.sleep(args.phase_s / 3.0)
            client.send_message(f"/sl/{action}/load_loop", [args.wav, "", ""])
            t.join()
        results[name] = stalls_since(mark)
        print(f"  {name:<11} worst stall {results[name][0]:7.1f} ms   "
              f"median {results[name][1]:5.1f} ms")

    server.shutdown()
    base = results["baseline"][0]
    other = results["other-loop"][0]
    same = results["same-loop"][0]

    print()
    if not (same > base * 2):
        print("INCONCLUSIVE — the control did not stall. The instrument cannot "
              "see the halt; do not trust the other rows.")
        return 2
    if other > base * 2:
        print(f"GLOBAL halt — loading loop {args.spare_loop} stalled loop "
              f"{args.play_loop} by {other:.1f} ms (baseline {base:.1f} ms).")
        print("=> File-based swapping cannot be seamless. Sum the tail without "
              "load_loop in the audio path.")
    else:
        print(f"PER-LOOP halt — loading loop {args.spare_loop} left loop "
              f"{args.play_loop} running ({other:.1f} ms vs baseline "
              f"{base:.1f} ms; control stalled {same:.1f} ms).")
        print("=> Double-buffer: pre-load the merged take into a spare slot, "
              "switch at the wrap. No I/O at the boundary.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
