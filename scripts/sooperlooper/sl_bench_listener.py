"""OSC state auto-update listener for APC footswitch bench."""

from __future__ import annotations

import os
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apc_footswitch import LoopFootswitch

LISTEN_HOST = os.environ.get("MPE_SL_BENCH_LISTEN_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("MPE_SL_BENCH_LISTEN_PORT", "9953"))
UPDATE_MS = int(os.environ.get("MPE_SL_BENCH_STATE_MS", "100"))


class SlBenchStateListener:
    def __init__(self, by_loop: dict[int, LoopFootswitch]) -> None:
        self._by_loop = by_loop
        self._server: object | None = None
        self._thread: threading.Thread | None = None

    def on_update(self, _addr: str, loop_index: int, control: str, value: float) -> None:
        if control != "state":
            return
        fs = self._by_loop.get(loop_index)
        if fs is None:
            return
        fs.sync_from_sl(int(value))

    def register(self, client, *, num_loops: int) -> None:
        returl = f"{LISTEN_HOST}:{LISTEN_PORT}"
        retpath = "/sl/bench/state"
        for loop in range(1, num_loops):
            client.send_message(
                f"/sl/{loop}/register_auto_update",
                ["state", UPDATE_MS, returl, retpath],
            )

    def start(self) -> None:
        from pythonosc import dispatcher as osc_dispatcher
        from pythonosc import osc_server

        disp = osc_dispatcher.Dispatcher()
        disp.map("/sl/bench/state", self.on_update)
        self._server = osc_server.ThreadingOSCUDPServer((LISTEN_HOST, LISTEN_PORT), disp)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        print(
            f"sl-bench-listener: state updates on {LISTEN_HOST}:{LISTEN_PORT}",
            flush=True,
        )
