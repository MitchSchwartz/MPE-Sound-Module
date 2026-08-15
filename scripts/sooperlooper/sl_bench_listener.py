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
REREGISTER_S = float(os.environ.get("MPE_SL_BENCH_REREGISTER_S", "15"))


class SlBenchStateListener:
    def __init__(self, by_loop: dict[int, LoopFootswitch]) -> None:
        self._by_loop = by_loop
        self._server: object | None = None
        self._thread: threading.Thread | None = None
        self._last_register = 0.0
        self._osc_client = None
        self._num_loops = 16

    def on_update(self, _addr: str, loop_index: int, control: str, value: float) -> None:
        if control != "state":
            return
        fs = self._by_loop.get(loop_index)
        if fs is None:
            return
        fs.sync_from_sl(int(value))

    def register(self, client, *, num_loops: int) -> None:
        self._osc_client = client
        self._num_loops = num_loops
        returl = f"{LISTEN_HOST}:{LISTEN_PORT}"
        retpath = "/sl/bench/state"
        for loop in range(num_loops):
            client.send_message(
                f"/sl/{loop}/register_auto_update",
                ["state", UPDATE_MS, returl, retpath],
            )
        import time

        self._last_register = time.monotonic()
        print(
            f"sl-bench-listener: state updates for loops 0..{num_loops - 1} "
            f"on {LISTEN_HOST}:{LISTEN_PORT}",
            flush=True,
        )

    def maybe_reregister(self) -> None:
        import time

        if self._osc_client is None:
            return
        if (time.monotonic() - self._last_register) < REREGISTER_S:
            return
        self.register(self._osc_client, num_loops=self._num_loops)

    def start(self) -> None:
        from pythonosc import dispatcher as osc_dispatcher
        from pythonosc import osc_server

        disp = osc_dispatcher.Dispatcher()
        disp.map("/sl/bench/state", self.on_update)
        # Bind failure must be FATAL. A dead listener means sl_state never
        # updates: no blink, no state, no truth — the bench keeps running and
        # every symptom looks like a control-layer bug. On 2026-08-14 a stale
        # bench held this port, this raised, and the session was debugged blind.
        try:
            self._server = osc_server.ThreadingOSCUDPServer(
                (LISTEN_HOST, LISTEN_PORT), disp
            )
        except OSError as exc:
            raise SystemExit(
                f"sl-bench-listener: cannot bind {LISTEN_HOST}:{LISTEN_PORT} ({exc}).\n"
                f"  A previous bench is probably still running.\n"
                f"  Fix: mpe looper sl-bench stop, then start again.\n"
                f"  Refusing to run blind — without state updates every pad lies."
            ) from exc
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
