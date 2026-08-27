#!/usr/bin/env python3
"""Read every loop's wet/dry level and state out of the engine. READ ONLY.

The bank test asks "did the fader binding travel with the clip?" — a question
you cannot answer by ear. Turning fader 1 down and hearing something get quieter
does not say *which* track moved, and the failure mode that matters (fader 1
still writing track 0 after banking to 8-15) sounds exactly like success if
track 0 happens to be the loud one.

So: ask the engine. This sends `/sl/N/get` for `wet` and `state` on every loop
and prints one line per loop. Run it before and after a bank change and diff.

`get` is a pure read — unlike sl_probe.py, which writes a probe value, this
touches nothing. Safe to run against a live take.

It also reports `dry`, which must be 0 on every loop, and says so loudly when
it is not — that is the passthrough fault that sounds like a level problem in
the amp rather than a control-layer bug.

Usage:
    dump-loop-levels.py [--loops 16] [--json] [--detail]

    --detail   Also read input_latency, fade_samples, loop_len per loop (P0 seam
               calibration — looper-p0-latency-calibration.md).

Env: MPE_SL_OSC_HOST (127.0.0.1), MPE_SL_OSC_PORT (9951), MPE_SL_LOOPS (16).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time

LISTEN_HOST = "127.0.0.1"
RETPATH = "/dump/reply"
# The engine answers on the JACK process callback's schedule, not ours. 1.5 s is
# far past the observed round trip and still short enough to fail fast when the
# command path is wedged (the case sl_probe.py exists to diagnose).
COLLECT_S = 1.5

# `dry` must be 0 on every loop. Above this it is audible: the JACK graph is
# parallel (Surge -> playback AND Surge -> every loop input, fail-open), so any
# loop with dry > 0 adds a second copy of the live signal one input-latency
# late. Left at ~0.4 by a leaking health probe on 2026-08-27, heard as random
# volume swells while nothing was looping. Repair: wire-jack-graph.sh connect
DRY_MAX = float(os.environ.get("MPE_SL_DRY_MAX", "0.001"))

# The codes the rest of the codebase reasons about come from the canonical
# module — a second hand-written copy is how a tester reads "playing" off a
# wrong number and records a false pass. The remaining labels are engine codes
# nothing here branches on; they exist so the dump is readable, not asserted on.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sl_limits import resolve_num_loops  # noqa: E402
from sl_loop_states import (  # noqa: E402
    SL_STATE_MUTE,
    SL_STATE_OFF,
    SL_STATE_PAUSED,
    SL_STATE_PLAYING,
    SL_STATE_RECORDING,
    SL_STATE_WAIT_START,
    SL_STATE_WAIT_STOP,
)

STATE_NAMES = {
    SL_STATE_OFF: "off",
    SL_STATE_WAIT_START: "wait_start",
    SL_STATE_RECORDING: "recording",
    SL_STATE_WAIT_STOP: "wait_stop",
    SL_STATE_PLAYING: "playing",
    SL_STATE_MUTE: "muted",
    SL_STATE_PAUSED: "paused",
    -1: "unknown", 5: "overdubbing", 6: "multiplying", 7: "inserting",
    8: "replacing", 9: "delay", 11: "scratching", 12: "one_shot",
    13: "substitute",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--loops", type=int, default=resolve_num_loops())
    parser.add_argument("--json", action="store_true", help="machine-readable, for diffing")
    parser.add_argument(
        "--detail",
        action="store_true",
        help="also read input_latency, fade_samples, loop_len (P0 calibration)",
    )
    args = parser.parse_args()

    try:
        from pythonosc import dispatcher, osc_server, udp_client
    except ImportError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    host = os.environ.get("MPE_SL_OSC_HOST", "127.0.0.1")
    port = int(os.environ.get("MPE_SL_OSC_PORT", "9951"))

    replies: dict[tuple[int, str], float] = {}
    lock = threading.Lock()

    def on_reply(_addr: str, loop_index: int, control: str, value: float) -> None:
        with lock:
            replies[(int(loop_index), str(control))] = float(value)

    disp = dispatcher.Dispatcher()
    disp.map(RETPATH, on_reply)
    # Port 0 = let the OS pick, so this never collides with a running bench
    # listener. The engine is told where to answer, so the port need not be
    # fixed or known in advance.
    server = osc_server.ThreadingOSCUDPServer((LISTEN_HOST, 0), disp)
    listen_port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    client = udp_client.SimpleUDPClient(host, port)
    # Bare host:port, not an osc.udp:// URL. This is the one line here that
    # cannot be tested without an engine, so it copies the form that is known
    # to work live — sl_bench_listener.register(). Guessing the URL form and
    # being wrong prints "engine may be wedged" at a perfectly healthy engine.
    returl = f"{LISTEN_HOST}:{listen_port}"
    # `dry` is read every time, not behind --detail. It has exactly one correct
    # value here (0.0 — wire-jack-graph.sh pins it, because the graph is
    # parallel and the looper must not pass its input through), and when it
    # drifts off zero the symptom is a level fault at the speakers that reads
    # like a hardware problem. A number nobody prints is a number nobody checks.
    controls = ("wet", "dry", "state")
    if args.detail:
        controls = ("wet", "dry", "state", "input_latency", "autoset_latency",
                    "fade_samples", "loop_len")
    for loop in range(args.loops):
        for ctrl in controls:
            client.send_message(f"/sl/{loop}/get", [ctrl, returl, RETPATH])

    time.sleep(COLLECT_S)
    server.shutdown()

    with lock:
        got = dict(replies)

    missing = [n for n in range(args.loops) if (n, "wet") not in got]
    if args.json:
        payload = {}
        for n in range(args.loops):
            row = {"wet": got.get((n, "wet")), "dry": got.get((n, "dry")),
                   "state": got.get((n, "state"))}
            if args.detail:
                row["input_latency"] = got.get((n, "input_latency"))
                row["autoset_latency"] = got.get((n, "autoset_latency"))
                row["fade_samples"] = got.get((n, "fade_samples"))
                row["loop_len"] = got.get((n, "loop_len"))
            payload[str(n)] = row
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for n in range(args.loops):
            wet = got.get((n, "wet"))
            state = got.get((n, "state"))
            dry = got.get((n, "dry"))
            wet_s = "  --  " if wet is None else f"{wet:6.3f}"
            dry_s = "  --  " if dry is None else f"{dry:6.3f}"
            if state is None:
                state_s = "no reply"
            else:
                state_s = STATE_NAMES.get(int(state), f"unknown({int(state)})")
            line = f"loop {n:2d}  wet={wet_s}  dry={dry_s}  state={state_s}"
            if dry is not None and abs(dry) > DRY_MAX:
                line += "   <-- PASSTHROUGH ON"
            if args.detail:
                il = got.get((n, "input_latency"))
                au = got.get((n, "autoset_latency"))
                fs = got.get((n, "fade_samples"))
                ll = got.get((n, "loop_len"))
                il_s = "  --" if il is None else f"{il:.0f}"
                au_s = " -" if au is None else f"{int(au)}"
                fs_s = " --" if fs is None else f"{fs:.0f}"
                ll_s = "  --" if ll is None else f"{ll:.3f}s"
                line += f"  in_lat={il_s}  autoset={au_s}  fade={fs_s}  len={ll_s}"
            print(line)

    passthrough = [n for n in range(args.loops)
                   if (got.get((n, "dry")) or 0.0) > DRY_MAX]
    if passthrough:
        print(
            f"\nPASSTHROUGH: loops {passthrough} have dry > {DRY_MAX} — the "
            f"looper is mixing the live input back to the speakers on top of "
            f"the direct Surge path. Expect level swells / doubling.\n"
            f"Repair (non-destructive, no loops lost): "
            f"scripts/sooperlooper/wire-jack-graph.sh connect",
            file=sys.stderr,
        )

    if missing:
        # Silence is the wedge signature, not an empty engine: a loop that
        # exists always answers `get`. Say so rather than printing dashes and
        # exiting 0, which reads as "levels are zero".
        print(
            f"\nNO REPLY for loops {missing} — engine may be wedged or have "
            f"fewer than {args.loops} loops. See sl-health.py.",
            file=sys.stderr,
        )
        return 1
    return 1 if passthrough else 0


if __name__ == "__main__":
    raise SystemExit(main())
