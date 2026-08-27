"""Single OSC listen port + engine client for the merged looper session (criterion 41).

Bench and HUD previously each bound their own UDP port (9953 / 9952) and kept
separate caches of the same engine state. One session object owns the lifecycle,
the cache, and registration — both consumers read derived state, not copies.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from sl_bench_listener import SlBenchStateListener

SL_HOST = os.environ.get("MPE_SL_OSC_HOST", "127.0.0.1")
SL_PORT = int(os.environ.get("MPE_SL_OSC_PORT", "9951"))
LISTEN_HOST = os.environ.get("MPE_SL_SESSION_LISTEN_HOST", "127.0.0.1")
# Canonical listen port — bench port (9953). HUD port (9952) is retired; alias only.
LISTEN_PORT = int(
    os.environ.get(
        "MPE_SL_SESSION_LISTEN_PORT",
        os.environ.get("MPE_SL_BENCH_LISTEN_PORT", "9953"),
    )
)

HUD_UPDATE_MS = 100
BENCH_STATE_MS = int(os.environ.get("MPE_SL_BENCH_STATE_MS", "100"))
BENCH_LOOP_POS_MS = int(os.environ.get("MPE_SL_BENCH_LOOP_POS_MS", "20"))
BENCH_WET_MS = int(os.environ.get("MPE_SL_BENCH_WET_MS", "500"))
REREGISTER_S = float(os.environ.get("MPE_SL_BENCH_REREGISTER_S", "15"))
NUM_LOOPS = int(os.environ.get("MPE_SL_LOOPS", "16"))

BenchWetCallback = Callable[[int, float], None]


def _cache_key(loop: int, ctrl: str) -> str:
    return f"{loop if loop >= 0 else -2}:{ctrl}"


class SlOscSession:
    """One engine client, one listen port, one auto-update cache."""

    def __init__(self) -> None:
        self.last: dict[str, float] = {}
        self._client = None
        self._server = None
        self._thread: threading.Thread | None = None
        self._last_register = 0.0
        self._bench_listener: SlBenchStateListener | None = None
        self._bench_num_loops = NUM_LOOPS
        self._hud_registered = False
        self._hud_loops_registered = False
        self._bench_registered = False

    @property
    def client(self):
        if self._client is None:
            raise RuntimeError("SlOscSession.start() was not called")
        return self._client

    @property
    def listen_port(self) -> int:
        return LISTEN_PORT

    def returl(self) -> str:
        return f"{LISTEN_HOST}:{LISTEN_PORT}"

    def start(self) -> SlOscSession:
        from pythonosc import dispatcher as osc_dispatcher
        from pythonosc import osc_server, udp_client

        disp = osc_dispatcher.Dispatcher()
        disp.map("/sl/bench/state", self._on_bench_state)
        disp.set_default_handler(self._on_hud_reply)
        try:
            self._server = osc_server.ThreadingOSCUDPServer(
                (LISTEN_HOST, LISTEN_PORT), disp
            )
        except OSError as exc:
            raise SystemExit(
                f"sl-osc-session: cannot bind {LISTEN_HOST}:{LISTEN_PORT} ({exc}).\n"
                f"  A previous looper session is probably still running.\n"
                f"  Fix: sudo systemctl stop mpe-looper-session.service, "
                f"then start again.\n"
                f"  Refusing to run blind — without state updates every pad lies."
            ) from exc
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True
        )
        self._thread.start()
        self._client = udp_client.SimpleUDPClient(SL_HOST, SL_PORT)
        print(
            f"sl-osc-session: listening on {LISTEN_HOST}:{LISTEN_PORT} "
            f"-> engine {SL_HOST}:{SL_PORT}",
            flush=True,
        )
        return self

    def attach_bench_listener(self, listener: SlBenchStateListener) -> None:
        self._bench_listener = listener

    def _store(self, loop_index: int, control: str, value: float) -> None:
        self.last[_cache_key(int(loop_index), control)] = float(value)

    def _on_hud_reply(self, _addr: str, *args) -> None:
        if len(args) >= 3:
            self._store(int(args[0]), str(args[1]), float(args[2]))

    def _on_bench_state(
        self, _addr: str, loop_index: int, control: str, value: float
    ) -> None:
        self._store(int(loop_index), control, float(value))
        if self._bench_listener is not None:
            self._bench_listener.on_update(_addr, int(loop_index), control, float(value))

    def cached(self, ctrl: str, loop: int = 0):
        """Last value delivered by auto-update. Never blocks."""
        return self.last.get(_cache_key(loop, ctrl))

    def get(self, ctrl: str, loop: int = 0, timeout: float = 0.4):
        key = _cache_key(loop, ctrl)
        self.last.pop(key, None)
        path = "/get" if loop < 0 else f"/sl/{loop}/get"
        self.client.send_message(path, [ctrl, self.returl(), "/r"])
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if key in self.last:
                return self.last[key]
            time.sleep(0.02)
        return None

    def register_hud(self) -> None:
        """Tempo only in merged mode — loop state comes from bench subscriptions."""
        returl = self.returl()
        self.client.send_message(
            "/register_auto_update", ["tempo", 200, returl, "/r"]
        )
        self._hud_registered = True

    def register_hud_loops(self) -> None:
        """Loop state/len/pos for --hud-only when bench is not running."""
        returl = self.returl()
        for loop in range(NUM_LOOPS):
            for ctrl in ("state", "loop_len", "loop_pos"):
                self.client.send_message(
                    f"/sl/{loop}/register_auto_update",
                    [ctrl, HUD_UPDATE_MS, returl, "/r"],
                )
        self._hud_loops_registered = True

    def register_bench(self, *, num_loops: int) -> None:
        self._bench_num_loops = num_loops
        returl = self.returl()
        retpath = "/sl/bench/state"
        for loop in range(num_loops):
            for ctrl in ("state", "loop_len"):
                self.client.send_message(
                    f"/sl/{loop}/register_auto_update",
                    [ctrl, BENCH_STATE_MS, returl, retpath],
                )
            self.client.send_message(
                f"/sl/{loop}/register_auto_update",
                ["loop_pos", BENCH_LOOP_POS_MS, returl, retpath],
            )
            self.client.send_message(
                f"/sl/{loop}/register_auto_update",
                ["wet", BENCH_WET_MS, returl, retpath],
            )
        self._bench_registered = True
        print(
            f"sl-osc-session: bench state updates for loops 0..{num_loops - 1}",
            flush=True,
        )

    def register_all(self, *, num_loops: int) -> None:
        """Subscribe bench + HUD, then seed tempo if still unknown."""
        self.register_hud()
        self.register_bench(num_loops=num_loops)
        self.seed_tempo()
        self._last_register = time.monotonic()

    def seed_tempo(self) -> None:
        if self.cached("tempo", -1) is None:
            self.get("tempo", -1)

    def maybe_reregister(self) -> None:
        if (time.monotonic() - self._last_register) < REREGISTER_S:
            return
        if self._hud_registered:
            self.register_hud()
        if self._hud_loops_registered:
            self.register_hud_loops()
        if self._bench_registered:
            self.register_bench(num_loops=self._bench_num_loops)
        self.seed_tempo()
        self._last_register = time.monotonic()
