"""The real SooperLooper, in a container, driven over OSC from a test.

Why this exists (2026-09-07): every looper regression of the last month lived
in a dimension the test double cannot express -- loop length, quantize, phase,
what a `/set` does -- because `tests/fake_sl_engine.py` discards every `/set`
and takes the loop length as a parameter. A claim about the engine is worth
something only if the engine can contradict it. This module hands a test the
engine: SooperLooper 1.7.9, the appliance's version with the appliance's liblo
patch, on a headless JACK dummy backend, launched the way the appliance
launches it (`run-engine.sh` mirrors `run-sooperlooper.sh`), on the
appliance's OSC port.

Opt-in: `MPE_ENGINE_TESTS=1` and a working `docker`. Otherwise every test
that uses this skips with a reason, so `unittest discover` and the
fake-engine suite are unaffected. First use builds the image (minutes; see
the Dockerfile); after that it is cached.

Time is real. The dummy backend runs at wall-clock speed, so a one-cycle take
at 120 BPM costs two seconds of test time. That is the price of an honest
clock and it is paid on purpose: the fake's `boundary()` was a clock that
ticked when the test told it to.

The engine's OSC port is bound in the host network namespace (`--network
host`), which is what lets replies reach a listener on 127.0.0.1 without any
port plumbing. While a test runs, 9951/udp is open on the laptop.
"""

from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from tests import conftest  # noqa: F401 -- puts scripts/sooperlooper on sys.path

from sl_grid_sync import apply_grid_sync, set_grid_active
from sl_loop_states import EMPTY_STATES, SL_STATE_OFF, SL_STATE_OFF_MUTED

IMAGE = "mpe-sl-engine:1.7.9"
CONTEXT = Path(__file__).resolve().parent
OSC_PORT = 9951
NUM_LOOPS = 15
ENV_FLAG = "MPE_ENGINE_TESTS"
SKIP_REASON = f"real-engine tests are opt-in: set {ENV_FLAG}=1 with docker available"
#: One JACK period at the harness's rate and period size (run-engine.sh).
PERIOD_S = 256 / 48000


def enabled() -> bool:
    return os.environ.get(ENV_FLAG, "").strip().lower() not in ("", "0", "off", "false")


def docker_ok() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def cycle_s(bpm: float, eighth_per_cycle: int = 8) -> float:
    """Seconds per cycle for an internal-tempo grid: eighths x (30 / BPM)."""
    return eighth_per_cycle * 30.0 / bpm


class Engine:
    """One SooperLooper process in one container, and a way to ask it things.

    `send(path, args)` has the signature `sl_grid_sync` expects, so the grid
    is applied to the real engine by the production code that applies it on
    the appliance -- not by a test's paraphrase of it.
    """

    _shared: Engine | None = None

    @classmethod
    def shared(cls) -> Engine:
        """One engine per test process; stopped at interpreter exit."""
        if cls._shared is None:
            cls._shared = cls().start()
            atexit.register(cls._shared.stop)
        return cls._shared

    def __init__(self, *, port: int = OSC_PORT, num_loops: int = NUM_LOOPS) -> None:
        self.port = port
        self.num_loops = num_loops
        self.name = f"mpe-sl-engine-{os.getpid()}"
        self.sent: list[tuple[str, list]] = []
        self.pong: tuple | None = None
        self.listen_port = 0
        self.client = None
        self._server = None
        self._replies: dict[tuple[int, str], float] = {}
        self._lock = threading.Lock()

    # --- lifecycle --------------------------------------------------------
    @staticmethod
    def ensure_image() -> None:
        if subprocess.run(["docker", "image", "inspect", IMAGE],
                          capture_output=True).returncode == 0:
            return
        extra = os.environ.get("MPE_ENGINE_BUILD_ARGS", "").split()
        subprocess.run(["docker", "build", *extra, "-t", IMAGE, str(CONTEXT)], check=True)

    def start(self) -> Engine:
        from pythonosc import dispatcher as osc_dispatcher
        from pythonosc import osc_server, udp_client

        self.ensure_image()
        disp = osc_dispatcher.Dispatcher()
        disp.map("/pong", self._on_pong)
        disp.map("/r", self._on_reply)
        self._server = osc_server.ThreadingOSCUDPServer(("127.0.0.1", 0), disp)
        self.listen_port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

        subprocess.run(
            ["docker", "run", "-d", "--rm", "--network", "host", "--name", self.name,
             "-e", f"ENGINE_LOOPS={self.num_loops}", "-e", f"ENGINE_OSC_PORT={self.port}",
             IMAGE],
            check=True, capture_output=True,
        )
        self.client = udp_client.SimpleUDPClient("127.0.0.1", self.port)
        deadline = time.monotonic() + 30.0
        while self.pong is None and time.monotonic() < deadline:
            self.send("/ping", [self.returl, "/pong"])
            time.sleep(0.25)
        if self.pong is None:
            logs = self.logs()
            self.stop()
            raise RuntimeError(f"engine never answered /ping on {self.port}:\n{logs}")
        return self

    def stop(self) -> None:
        subprocess.run(["docker", "rm", "-f", self.name], capture_output=True)
        if self._server is not None:
            self._server.shutdown()
            self._server = None
        if Engine._shared is self:
            Engine._shared = None

    def logs(self) -> str:
        out = subprocess.run(["docker", "logs", self.name], capture_output=True, text=True)
        return out.stdout + out.stderr

    # --- wire -------------------------------------------------------------
    @property
    def returl(self) -> str:
        return f"127.0.0.1:{self.listen_port}"  # the bench's format (sl_osc_session.returl)

    def send(self, path: str, args) -> None:
        # python-osc takes a scalar or a list; the bench sends both shapes
        # (`"mute_on"` and `["quantize", 1.0]`). Keep the record normalised.
        args = [args] if isinstance(args, (str, int, float)) else list(args)
        self.sent.append((path, args))
        self.client.send_message(path, args)

    #: The bench's OSC client spells it this way; production helpers that take
    #: an `osc` argument (`stop_all_loops`, `settle_stop_all`) can be handed
    #: the engine directly.
    send_message = send

    def hit(self, loop: int, verb: str) -> None:
        self.send(f"/sl/{loop}/hit", [verb])

    def set(self, loop: int, ctrl: str, value: float) -> None:
        self.send(f"/sl/{loop}/set", [ctrl, float(value)])

    def gset(self, ctrl: str, value: float) -> None:
        self.send("/set", [ctrl, float(value)])

    def _on_pong(self, _addr, *args) -> None:
        self.pong = tuple(args)

    def _on_reply(self, _addr, *args) -> None:
        if len(args) >= 3:
            with self._lock:
                self._replies[(int(args[0]), str(args[1]))] = args[2]

    def get(self, loop: int, ctrl: str, timeout: float = 1.0):
        """Ask the engine; return the value or None if it never answered."""
        key = (loop, ctrl)
        with self._lock:
            self._replies.pop(key, None)
        self.send(f"/sl/{loop}/get", [ctrl, self.returl, "/r"])
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if key in self._replies:
                    return self._replies[key]
            time.sleep(0.005)
        return None

    def gget(self, ctrl: str, timeout: float = 1.0):
        """Global `/get`; the engine answers these with a negative loop index."""
        with self._lock:
            for k in [k for k in self._replies if k[1] == ctrl and k[0] < 0]:
                self._replies.pop(k, None)
        self.send("/get", [ctrl, self.returl, "/r"])
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                for (idx, name), val in self._replies.items():
                    if name == ctrl and idx < 0:
                        return val
            time.sleep(0.005)
        return None

    # --- questions a test asks ---------------------------------------------
    def state(self, loop: int) -> int:
        val = self.get(loop, "state")
        if val is None:
            raise RuntimeError(f"loop {loop}: no answer to get state")
        return int(round(val))

    def loop_len(self, loop: int) -> float:
        val = self.get(loop, "loop_len")
        if val is None:
            raise RuntimeError(f"loop {loop}: no answer to get loop_len")
        return float(val)

    def wait_state(self, loop: int, want: int, timeout: float = 5.0) -> bool:
        """Poll until the loop reports `want`; False if it never does."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.state(loop) == want:
                return True
            time.sleep(0.02)
        return False

    def wait_state_in(self, loop: int, wanted, timeout: float = 5.0) -> int | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            s = self.state(loop)
            if s in wanted:
                return s
            time.sleep(0.02)
        return None

    # --- shared setup --------------------------------------------------------
    def reset(self) -> None:
        """Every loop empty, no grid, recording starts on silence.

        `rec_thresh` is forced to 0 because the dummy backend's inputs are
        silence and a threshold would leave every take in WAIT_START forever.
        The appliance's value is its own business; this is the harness saying
        what it needs.
        """
        for loop in range(self.num_loops):
            self.hit(loop, "undo_all")
        # The appliance's resting state, sent by the appliance's own code: the
        # bench applies `apply_grid_sync` at startup (internal tempo clock,
        # default BPM) and `set_grid_active(active=False)` when the grid drops.
        # An earlier version of this reset set sync_source to NONE instead and
        # measured a different engine: with no sync source a record start is
        # never quantized. Fidelity here is the whole point.
        apply_grid_sync(self.send, num_loops=self.num_loops)
        set_grid_active(self.send, num_loops=self.num_loops, active=False)
        for loop in range(self.num_loops):
            self.set(loop, "rec_thresh", 0.0)
        # MEASURED 2026-09-07: undo_all empties a PLAYING, PAUSED or WAIT_START
        # loop to OFF, but a muted empty loop stays OFF_MUTED (20) through it.
        # Only mute_off lifts that, so Stop All's mute_on on an empty pad
        # outlives a clear -- which is why EMPTY_STATES has two members.
        for loop in range(self.num_loops):
            if self.wait_state_in(loop, EMPTY_STATES, timeout=2.0) == SL_STATE_OFF_MUTED:
                self.hit(loop, "mute_off")
            if not self.wait_state(loop, SL_STATE_OFF, timeout=2.0):
                raise RuntimeError(
                    f"loop {loop} would not go OFF on reset (state={self.state(loop)})")
        self.sent.clear()

    def grid(self, bpm: float = 120.0, eighth_per_cycle: int = 8) -> float:
        """Establish the grid the way the appliance does; return the cycle in seconds."""
        apply_grid_sync(self.send, num_loops=self.num_loops,
                        eighth_per_cycle=eighth_per_cycle, bpm=bpm)
        set_grid_active(self.send, num_loops=self.num_loops, active=True)
        return cycle_s(bpm, eighth_per_cycle)
