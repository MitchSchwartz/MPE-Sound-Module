#!/usr/bin/env python3
"""SooperLooper master-loop position → ~/.mpe_sl_hud_state.json for touch HUD."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from patch_browser.sl_hud_state import SL_HUD_STATE_FILE  # noqa: E402

MASTER_LOOP = int(os.environ.get("MPE_SL_HUD_MASTER_LOOP", "0"))
SL_HOST = os.environ.get("MPE_SL_OSC_HOST", "127.0.0.1")
SL_PORT = int(os.environ.get("MPE_SL_OSC_PORT", "9951"))
LISTEN_HOST = os.environ.get("MPE_SL_HUD_LISTEN_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("MPE_SL_HUD_LISTEN_PORT", "9952"))
WRITE_INTERVAL_S = float(os.environ.get("MPE_SL_HUD_WRITE_INTERVAL_S", "0.1"))

PLAYING_STATES = frozenset({4, 5})


def beat_and_bar(loop_pos: float, cycle_len: float) -> tuple[int | None, int | None]:
    if cycle_len <= 0.0:
        return None, None
    pos = loop_pos % cycle_len
    beat = int((pos / cycle_len) * 4.0) % 4 + 1
    bar = int(loop_pos / cycle_len) + 1
    return beat, bar


class SlHudMonitor:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: dict[str, float | int] = {
            "loop_pos": 0.0,
            "cycle_len": 0.0,
            "loop_len": 0.0,
            "state": -1,
        }
        self._last_write = 0.0

    def on_update(self, _addr: str, loop_index: int, control: str, value: float) -> None:
        if loop_index != MASTER_LOOP:
            return
        if control not in self._values:
            return
        with self._lock:
            if control == "state":
                self._values[control] = int(value)
            else:
                self._values[control] = float(value)

    def maybe_write(self, force: bool = False) -> None:
        now = time.time()
        if not force and (now - self._last_write) < WRITE_INTERVAL_S:
            return
        self._last_write = now
        with self._lock:
            loop_pos = float(self._values["loop_pos"])
            cycle_len = float(self._values["cycle_len"])
            loop_len = float(self._values["loop_len"])
            state = int(self._values["state"])
        beat, bar = beat_and_bar(loop_pos, cycle_len)
        payload = {
            "updated_at": now,
            "master_loop": MASTER_LOOP,
            "loop_pos": loop_pos,
            "cycle_len": cycle_len,
            "loop_len": loop_len,
            "state": state,
            "beat": beat,
            "bar": bar,
            "playing": state in PLAYING_STATES,
        }
        tmp = SL_HUD_STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(SL_HUD_STATE_FILE)

    def register(self, client) -> None:
        returl = f"{LISTEN_HOST}:{LISTEN_PORT}"
        retpath = "/sl/hud"
        for ctrl in ("loop_pos", "cycle_len", "loop_len", "state"):
            client.send_message(
                f"/sl/{MASTER_LOOP}/register_auto_update",
                [ctrl, 100, returl, retpath],
            )


def main() -> int:
    try:
        from pythonosc import dispatcher as osc_dispatcher
        from pythonosc import osc_server, udp_client
    except ImportError as exc:
        print(f"sl-hud-monitor: {exc}", file=sys.stderr)
        return 1

    monitor = SlHudMonitor()
    disp = osc_dispatcher.Dispatcher()
    disp.map("/sl/hud", monitor.on_update)

    server = osc_server.ThreadingOSCUDPServer((LISTEN_HOST, LISTEN_PORT), disp)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    client = udp_client.SimpleUDPClient(SL_HOST, SL_PORT)
    monitor.register(client)
    print(
        f"sl-hud-monitor: loop {MASTER_LOOP} → {SL_HUD_STATE_FILE} "
        f"(listen {LISTEN_HOST}:{LISTEN_PORT}, SL {SL_HOST}:{SL_PORT})",
        flush=True,
    )

    try:
        while True:
            monitor.maybe_write()
            time.sleep(0.05)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
