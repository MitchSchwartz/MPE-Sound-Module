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
LOOP_POS_UPDATE_MS = int(os.environ.get("MPE_SL_BENCH_LOOP_POS_MS", "20"))
WET_UPDATE_MS = int(os.environ.get("MPE_SL_BENCH_WET_MS", "500"))
REREGISTER_S = float(os.environ.get("MPE_SL_BENCH_REREGISTER_S", "15"))


class SlBenchStateListener:
    def __init__(self, by_loop: dict[int, LoopFootswitch],
                 on_wet=None) -> None:
        self._by_loop = by_loop
        self._on_wet = on_wet
        self._server: object | None = None
        self._thread: threading.Thread | None = None
        self._last_register = 0.0
        self._osc_client = None
        self._num_loops = 16

    def on_update(self, _addr: str, loop_index: int, control: str, value: float) -> None:
        if control == "wet":
            if self._on_wet is not None:
                self._on_wet(int(loop_index), float(value))
            return
        fs = self._by_loop.get(loop_index)
        if fs is None:
            return
        if control == "state":
            fs.sync_from_sl(int(value))
        elif control == "loop_len":
            fs.sync_loop_len(float(value))
        elif control == "loop_pos":
            fs.sync_loop_pos(float(value))

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
            client.send_message(
                f"/sl/{loop}/register_auto_update",
                ["loop_pos", LOOP_POS_UPDATE_MS, returl, retpath],
            )
            client.send_message(
                f"/sl/{loop}/register_auto_update",
                ["wet", WET_UPDATE_MS, returl, retpath],
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
