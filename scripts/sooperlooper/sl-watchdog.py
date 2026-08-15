#!/usr/bin/env python3
"""SooperLooper stack watchdog — repair what is safe, alarm on the rest.

Design rule, learned the hard way on 2026-08-14:

    Fail OPEN on the audio path. Fail LOUD on the control path.
    Never auto-repair anything that can destroy a take.

Those are different obligations. Audio must keep flowing; control must never
lie. A component that keeps running while reporting false state is worse than
one that stops, because every downstream symptom then points at the wrong layer.

What it repairs automatically (non-destructive, restores a known-good graph):
  * `common_out` disconnected from `system:playback` — loops audible again.
    JACK connections do not survive a SooperLooper restart, so this happens
    every time the engine is restarted without a rewire.

What it will NOT repair (destructive — alarms instead):
  * A wedged engine. Restarting SooperLooper destroys every recorded loop. In
    the middle of a set that is far worse than the wedge. It alarms, captures
    diagnostics, and waits for a human to run `mpe looper sl-restart`.

Root cause of the wedge is UNKNOWN as of 2026-08-14. It has recurred at least
twice: OSC `/get` keeps answering while `/set` and `/hit` are silently ignored
(they go through push_nonrt_event; `get` reads state directly). On detection
this dumps per-thread kernel state to the alarm file so the next occurrence
produces evidence instead of another guess.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

SL_HOST = os.environ.get("MPE_SL_OSC_HOST", "127.0.0.1")
SL_PORT = int(os.environ.get("MPE_SL_OSC_PORT", "9951"))
LISTEN_PORT = int(os.environ.get("MPE_SL_WATCHDOG_PORT", "9961"))
JACK_CLIENT = os.environ.get("MPE_SL_JACK_CLIENT", "mpe-looper")
INTERVAL_S = float(os.environ.get("MPE_SL_WATCHDOG_INTERVAL_S", "10"))
ALARM_FILE = Path(os.environ.get(
    "MPE_SL_WATCHDOG_ALARM_FILE", str(Path.home() / ".mpe_sl_watchdog.json")))
REPO_ROOT = Path(__file__).resolve().parents[2]


def now() -> str:
    return time.strftime("%H:%M:%S")


def log(msg: str) -> None:
    print(f"[{now()}] sl-watchdog: {msg}", flush=True)


class Osc:
    def __init__(self) -> None:
        self.last: dict[str, float] = {}

    def start(self):
        from pythonosc import dispatcher as dsp
        from pythonosc import osc_server, udp_client

        d = dsp.Dispatcher()
        d.set_default_handler(
            lambda _a, *x: self.last.__setitem__(str(x[1]), x[2]) if len(x) >= 3 else None
        )
        self._srv = osc_server.ThreadingOSCUDPServer((SL_HOST, LISTEN_PORT), d)
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()
        self.client = udp_client.SimpleUDPClient(SL_HOST, SL_PORT)
        return self

    def get(self, ctrl: str, loop: int = 0, timeout: float = 1.5):
        self.last.pop(ctrl, None)
        path = "/get" if loop < 0 else f"/sl/{loop}/get"
        self.client.send_message(path, [ctrl, f"{SL_HOST}:{LISTEN_PORT}", "/r"])
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if ctrl in self.last:
                return self.last[ctrl]
            time.sleep(0.03)
        return None


def playback_sources() -> set[str]:
    try:
        out = subprocess.run(["jack_lsp", "-c"], capture_output=True,
                             text=True, timeout=10).stdout
    except Exception:
        return set()
    found, cur = set(), None
    for line in out.splitlines():
        if not line.startswith((" ", "\t")):
            cur = line.strip()
        elif cur and cur.startswith("system:playback"):
            found.add(line.strip())
    return found


def capture_wedge_diagnostics() -> dict:
    """Evidence for the unknown wedge: what is each engine thread doing?"""
    info: dict = {"threads": []}
    try:
        pid = subprocess.run(["pgrep", "-f", "src/sooperlooper"],
                             capture_output=True, text=True, timeout=5).stdout.split()
        if not pid:
            return {"error": "no sooperlooper process"}
        pid = pid[0]
        info["pid"] = pid
        for t in Path(f"/proc/{pid}/task").iterdir():
            entry = {"tid": t.name}
            for f in ("comm", "wchan", "stat"):
                try:
                    entry[f] = (t / f).read_text(errors="replace").strip()[:200]
                except OSError:
                    pass
            info["threads"].append(entry)
    except Exception as exc:
        info["error"] = str(exc)
    return info


def write_alarm(state: str, detail: dict) -> None:
    payload = {"updated_at": time.time(), "state": state, **detail}
    tmp = ALARM_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(ALARM_FILE)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--once", action="store_true", help="single pass, then exit")
    ap.add_argument("--no-repair", action="store_true",
                    help="detect and alarm only; never touch the JACK graph")
    args = ap.parse_args(argv)

    osc = Osc().start()
    log(f"watching every {INTERVAL_S:.0f}s — repairs JACK graph, alarms on wedge")
    wedged_since: float | None = None

    while True:
        problems, repaired = [], []

        # --- audio path: safe to repair -------------------------------------
        srcs = playback_sources()
        if srcs and not any(s.startswith(f"{JACK_CLIENT}:common_out") for s in srcs):
            problems.append("common_out not connected to system:playback")
            if not args.no_repair:
                script = REPO_ROOT / "scripts/sooperlooper/wire-jack-graph.sh"
                try:
                    subprocess.run(["bash", str(script), "connect"],
                                   capture_output=True, timeout=60)
                    if any(s.startswith(f"{JACK_CLIENT}:common_out")
                           for s in playback_sources()):
                        repaired.append("reconnected common_out -> playback")
                        problems.pop()
                except Exception as exc:
                    log(f"repair failed: {exc}")

        # --- control path: NEVER auto-repair (restart destroys takes) --------
        state = osc.get("state")
        if state is None:
            problems.append("engine not answering OSC")
        else:
            before = osc.get("dry")
            target = 0.5 if (before is None or abs(float(before) - 0.5) > 0.01) else 0.75
            osc.client.send_message("/sl/0/set", ["dry", target])
            time.sleep(0.4)
            after = osc.get("dry")
            if after is None or abs(float(after) - target) > 0.01:
                problems.append("WEDGED: reads answer, commands ignored")
                if wedged_since is None:
                    wedged_since = time.time()
                    diag = capture_wedge_diagnostics()
                    write_alarm("wedged", {
                        "detail": "OSC /set ignored; /get still answers",
                        "action": "mpe looper sl-restart (DESTROYS loops — human call)",
                        "diagnostics": diag,
                    })
                    log("!! ENGINE WEDGED — not auto-restarting (that would destroy "
                        "your loops). Diagnostics captured. Fix: mpe looper sl-restart")
            elif before is not None:
                osc.client.send_message("/sl/0/set", ["dry", float(before)])
                wedged_since = None

        for r in repaired:
            log(f"repaired: {r}")
        if problems:
            log("PROBLEM: " + "; ".join(problems))
        else:
            write_alarm("ok", {})

        if args.once:
            return 1 if problems else 0
        time.sleep(INTERVAL_S)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        sys.exit(0)
