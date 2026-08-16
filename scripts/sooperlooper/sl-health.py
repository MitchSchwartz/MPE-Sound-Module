#!/usr/bin/env python3
"""SooperLooper engine health — is the COMMAND path alive, not just the read path?

Why this exists (2026-08-14): the engine can wedge in a state where OSC `/get`
still answers correctly but `/set` and `/hit` are silently ignored. `get` reads
state directly; `set`, `hit` and `save_loop` all go through
`push_nonrt_event()`. When that non-realtime event thread stops draining, the
engine looks healthy to every read-only check and ignores every command.

Cost of not having this check: an evening of debugging the Python control layer
against a dead engine, plus a bogus "B8 persistence fail" in the eval log — the
missing .wav was this wedge, not broken disk persistence.

Exit 0 = engine accepts commands. Exit 1 = wedged or unreachable; restart it
(`mpe looper sl-restart`) before trusting anything else.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sl_probe import (  # noqa: E402
    ALIVE,
    PROBE_LOOP,
    WEDGED,
    check_command_path,
)

SL_HOST = os.environ.get("MPE_SL_OSC_HOST", "127.0.0.1")
SL_PORT = int(os.environ.get("MPE_SL_OSC_PORT", "9951"))
LISTEN_PORT = int(os.environ.get("MPE_SL_HEALTH_PORT", "9954"))

STATE_NAMES = {
    0: "Off", 1: "WaitStart", 2: "Recording", 3: "WaitStop",
    4: "Playing", 5: "Overdubbing", 14: "Paused",
}


class Probe:
    def __init__(self) -> None:
        self.last: dict[str, float] = {}
        self._server = None

    def _on(self, _addr, *args) -> None:
        if len(args) >= 3:
            self.last[str(args[1])] = args[2]

    def start(self):
        from pythonosc import dispatcher as osc_dispatcher
        from pythonosc import osc_server, udp_client

        disp = osc_dispatcher.Dispatcher()
        disp.set_default_handler(self._on)
        self._server = osc_server.ThreadingOSCUDPServer((SL_HOST, LISTEN_PORT), disp)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        self.client = udp_client.SimpleUDPClient(SL_HOST, SL_PORT)
        return self

    def get(self, ctrl: str, loop: int = 0, timeout: float = 1.5):
        self.last.pop(ctrl, None)
        path = "/get" if loop < 0 else f"/sl/{loop}/get"
        self.client.send_message(path, [ctrl, f"{SL_HOST}:{LISTEN_PORT}", "/r"])
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if ctrl in self.last:
                return self.last[ctrl]
            time.sleep(0.05)
        return None

    def close(self) -> None:
        if self._server is not None:
            self._server.shutdown()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--record-test", action="store_true",
                    help="Also drive a full record->play cycle (audible; clears loop 0)")
    args = ap.parse_args(argv)

    try:
        import pythonosc  # noqa: F401
    except ImportError as exc:
        print(f"sl-health: {exc}", file=sys.stderr)
        return 1

    p = Probe().start()
    failures = 0

    try:
        # 1) Read path.
        state = p.get("state")
        if state is None:
            print("FAIL  read path    no reply to /sl/0/get — engine down or wrong port")
            return 1
        print(f"PASS  read path    loop 0 state = {int(state)} ({STATE_NAMES.get(int(state), '?')})")

        # 2) Command path — the wedge detector. `set` goes through the nonrt
        #    event queue; if that thread is stuck this never round-trips.
        #
        #    Shared with sl-watchdog so the two cannot fight over one control
        #    and manufacture a false WEDGED — whose remedy destroys every loop.
        verdict, detail = check_command_path(
            lambda ctrl: p.get(ctrl, loop=PROBE_LOOP),
            lambda ctrl, val: p.client.send_message(f"/sl/{PROBE_LOOP}/set", [ctrl, val]),
            seed="sl-health",
        )
        if verdict == ALIVE:
            print(f"PASS  command path {detail}")
        else:
            print(f"FAIL  command path {detail}")
            if verdict == WEDGED:
                print("      Engine is WEDGED: reads answer, commands do nothing.")
                print("      FIRST check it is still on JACK — an orphaned client")
                print("      looks identical from here (spec §M):  jack_lsp | grep mpe-looper")
                print("      Then, only if it is on JACK: mpe looper sl-restart")
                print("      (sl-restart DESTROYS every recorded loop.)")
            failures += 1

        # 3) Global config readback.
        src = p.get("sync_source", loop=-1)
        tempo = p.get("tempo", loop=-1)
        print(f"PASS  sync config  sync_source={src} tempo={tempo}")

        # 4) Audio path out. A loop can be Playing with its output connected to
        #    nothing — the pad goes green and you hear silence. JACK connections
        #    do NOT survive a SooperLooper restart, so this breaks every time
        #    the engine is restarted without a rewire.
        import subprocess

        client = os.environ.get("MPE_SL_JACK_CLIENT", "mpe-looper")
        try:
            graph = subprocess.run(["jack_lsp", "-c"], capture_output=True,
                                   text=True, timeout=10).stdout
        except Exception as exc:
            print(f"FAIL  audio path   jack_lsp unavailable ({exc})")
            failures += 1
            graph = ""
        if graph:
            connected, current = set(), None
            for line in graph.splitlines():
                if not line.startswith((" ", "\t")):
                    current = line.strip()
                elif current and current.startswith("system:playback"):
                    connected.add(line.strip())
            outs = [c for c in connected if c.startswith(f"{client}:common_out")]
            if outs:
                print(f"PASS  audio path   {', '.join(sorted(outs))} -> system:playback")
            else:
                print(f"FAIL  audio path   {client}:common_out is NOT connected to "
                      f"system:playback — loops will play SILENTLY")
                print("      Fix: mpe looper sl-rewire")
                failures += 1

        if args.record_test and failures == 0:
            print("      --record-test: driving record -> play on loop 0")
            p.client.send_message("/sl/0/hit", ["undo_all"])
            time.sleep(0.6)
            p.client.send_message("/sl/0/hit", ["record"])
            got_recording = False
            for _ in range(12):
                time.sleep(0.5)
                if int(p.get("state") or -1) == 2:
                    got_recording = True
                    break
            p.client.send_message("/sl/0/hit", ["record"])
            got_playing = False
            for _ in range(12):
                time.sleep(0.5)
                if int(p.get("state") or -1) == 4:
                    got_playing = True
                    break
            length = p.get("loop_len")
            if got_recording and got_playing:
                print(f"PASS  record cycle Off->Recording->Playing, loop_len={length}")
            else:
                print(f"FAIL  record cycle recording={got_recording} playing={got_playing} "
                      f"loop_len={length} — sync boundaries may not be arriving")
                failures += 1
    finally:
        p.close()

    print("")
    if failures:
        print("sl-health: FAIL — do not debug the control layer against this engine",
              file=sys.stderr)
        return 1
    print("sl-health: PASS — engine accepts commands")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
