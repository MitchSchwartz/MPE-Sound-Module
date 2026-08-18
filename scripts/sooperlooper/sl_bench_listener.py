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

from sl_grid_sync import ENGINE_CONFIG_PROBE, expected_engine_config  # noqa: E402

# Global config probe — detects engine restart via smart_eighths drift.
GLOBAL_CONFIG_PROBE = os.environ.get("MPE_SL_BENCH_CONFIG_PROBE", ENGINE_CONFIG_PROBE)
GLOBAL_UPDATE_MS = int(os.environ.get("MPE_SL_BENCH_CONFIG_PROBE_MS", "1000"))


class SlBenchStateListener:
    def __init__(self, by_loop: dict[int, LoopFootswitch],
                 on_global=None, on_wet=None) -> None:
        self._by_loop = by_loop
        self._on_global = on_global
        # Seeds the fader layer from engine truth. Without it the faders have
        # no idea where the levels actually are, and their first movement is a
        # jump rather than a pickup.
        self._on_wet = on_wet
        self._server: object | None = None
        self._thread: threading.Thread | None = None
        self._last_register = 0.0
        self._osc_client = None
        self._num_loops = 16

    def on_update(self, _addr: str, loop_index: int, control: str, value: float) -> None:
        if control == "wet":
            # Handled before the footswitch lookup: the fader layer wants this
            # even for loops with no pad bound to them.
            if self._on_wet is not None:
                self._on_wet(int(loop_index), float(value))
            return
        fs = self._by_loop.get(loop_index)
        if fs is None:
            return
        if control == "state":
            fs.sync_from_sl(int(value))
        elif control == "loop_len":
            # Needed to capture the tempo from the first take, which is what
            # establishes the grid.
            fs.sync_loop_len(float(value))
        elif control == "loop_pos":
            fs.sync_loop_pos(float(value))

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
            # Slower than state on purpose. This only has to notice a level
            # changed by something other than us; polling it at pad-blink rate
            # would cost a datagram per loop per 100 ms for no benefit.
            client.send_message(
                f"/sl/{loop}/register_auto_update",
                ["loop_pos", LOOP_POS_UPDATE_MS, returl, retpath],
            )
            client.send_message(
                f"/sl/{loop}/register_auto_update",
                ["wet", WET_UPDATE_MS, returl, retpath],
            )
        # Global (no /sl/N prefix) — verified against control_osc.cpp:178 and
        # live on the engine: replies carry loop index -2. This is how the bench
        # notices the engine restarted underneath it, which otherwise leaves the
        # grid config silently reverted to SooperLooper's defaults.
        client.send_message(
            "/register_auto_update",
            [GLOBAL_CONFIG_PROBE, GLOBAL_UPDATE_MS, returl, "/sl/bench/global"],
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
