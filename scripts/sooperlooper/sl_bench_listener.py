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

# The engine-wide control the bench watches to notice a restart. Slow on
# purpose: it changes only when something has gone badly wrong, and the reply
# costs a datagram every interval for the life of the bench.
GLOBAL_SENTINEL = os.environ.get("MPE_SL_BENCH_SENTINEL", "sync_source")
GLOBAL_UPDATE_MS = int(os.environ.get("MPE_SL_BENCH_SENTINEL_MS", "1000"))


class SlBenchStateListener:
    def __init__(self, by_loop: dict[int, LoopFootswitch],
                 on_global=None) -> None:
        self._by_loop = by_loop
        self._on_global = on_global
        self._server: object | None = None
        self._thread: threading.Thread | None = None
        self._last_register = 0.0
        self._osc_client = None
        self._num_loops = 16

    def on_update(self, _addr: str, loop_index: int, control: str, value: float) -> None:
        fs = self._by_loop.get(loop_index)
        if fs is None:
            return
        if control == "state":
            fs.sync_from_sl(int(value))
        elif control == "loop_len":
            # Needed to capture the tempo from the first take, which is what
            # establishes the grid.
            fs.sync_loop_len(float(value))

    def on_global_update(self, _addr: str, _loop_index: int, control: str,
                         value: float) -> None:
        """Engine-wide settings. Loop index is -2 and means nothing here."""
        if self._on_global is not None:
            self._on_global(str(control), float(value))

    def register(self, client, *, num_loops: int) -> None:
        self._osc_client = client
        self._num_loops = num_loops
        returl = f"{LISTEN_HOST}:{LISTEN_PORT}"
        retpath = "/sl/bench/state"
        for loop in range(num_loops):
            for ctrl in ("state", "loop_len"):
                client.send_message(
                    f"/sl/{loop}/register_auto_update",
                    [ctrl, UPDATE_MS, returl, retpath],
                )
        # Global (no /sl/N prefix) — verified against control_osc.cpp:178 and
        # live on the engine: replies carry loop index -2. This is how the bench
        # notices the engine restarted underneath it, which otherwise leaves the
        # grid config silently reverted to SooperLooper's defaults.
        client.send_message(
            "/register_auto_update",
            [GLOBAL_SENTINEL, GLOBAL_UPDATE_MS, returl, "/sl/bench/global"],
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
        disp.map("/sl/bench/global", self.on_global_update)
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
