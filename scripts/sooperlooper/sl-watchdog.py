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

**The "wedge" was solved on 2026-08-15: it was an orphaned JACK client.**
SooperLooper survived a `jackd` restart as a process but lost its JACK client
and never re-registered. `push_nonrt_event()` is drained from the JACK process
callback, so with no callback every `/set` and `/hit` vanished while `/get` read
state directly and kept answering. Five occurrences, all misdiagnosed as an
engine fault. Spec §M has the evidence.

That is why the JACK-visibility check runs FIRST here. An orphan is
indistinguishable from a true wedge to the OSC probe, so probing first names the
wrong component. If the OSC probe ever does report a wedge on an engine that IS
on the graph, that is a genuinely new failure — the per-thread kernel dump exists
for exactly that case.
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sl_probe import (  # noqa: E402
    ALIVE,
    PROBE_LOOP,
    check_command_path,
)

SL_HOST = os.environ.get("MPE_SL_OSC_HOST", "127.0.0.1")
SL_PORT = int(os.environ.get("MPE_SL_OSC_PORT", "9951"))
LISTEN_PORT = int(os.environ.get("MPE_SL_WATCHDOG_PORT", "9961"))
JACK_CLIENT = os.environ.get("MPE_SL_JACK_CLIENT", "mpe-looper")
INTERVAL_S = float(os.environ.get("MPE_SL_WATCHDOG_INTERVAL_S", "10"))
ALARM_FILE = Path(os.environ.get(
    "MPE_SL_WATCHDOG_ALARM_FILE", str(Path.home() / ".mpe_sl_watchdog.json")))
REPO_ROOT = Path(__file__).resolve().parents[2]

# --- CPU governor -----------------------------------------------------------
# Opt-in, exactly like set-cpu-governor.sh: unset means "not managed here", not
# "performance". An appliance that never asked for a pin must not be nagged.
#
# Repair goes through `systemctl restart mpe-cpu-governor.service` rather than
# writing sysfs directly: that unit already reads MPE_CPU_GOVERNOR from
# /etc/mpe/mpe.env and runs as root, so policy stays in one place and this
# watchdog needs no privileges beyond the NOPASSWD systemctl the appliance user
# already has.
GOVERNOR_TARGET = os.environ.get("MPE_CPU_GOVERNOR", "").strip()
GOVERNOR_PATH = Path(os.environ.get(
    "MPE_CPU_GOVERNOR_PATH",
    "/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"))
GOVERNOR_UNIT = os.environ.get("MPE_CPU_GOVERNOR_UNIT", "mpe-cpu-governor.service")

# --- xruns ------------------------------------------------------------------
# SooperLooper prints "got xrun" to stdout, which restart-sooperlooper.sh
# redirects here. Until 2026-08-17 that stream went to a dead SSH pipe, so the
# appliance dropped audio continuously while every instrument reported ok.
#
# Threshold rationale, measured on this appliance the same session:
#   governor=ondemand     ~66 xruns/min
#   governor=performance  ~9 xruns/min
# 30/min sits between the two regimes: it catches the governor falling off (and
# anything else that bad) without crying wolf at the current known baseline. The
# RATE IS ALWAYS REPORTED in the alarm file regardless of the threshold — a
# number you can watch is the point; the alarm is only for the loud case.
ENGINE_LOG = Path(os.environ.get("MPE_SL_ENGINE_LOG", "/tmp/sooperlooper.log"))
XRUN_MARKER = os.environ.get("MPE_SL_XRUN_MARKER", "got xrun")
XRUN_ALARM_PER_MIN = float(os.environ.get("MPE_SL_XRUN_ALARM_PER_MIN", "30"))
XRUN_WINDOW_S = float(os.environ.get("MPE_SL_XRUN_WINDOW_S", "60"))


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


def jack_graph() -> str | None:
    """Raw `jack_lsp -c` output, or None if JACK itself is unreachable.

    None and "" are different answers and must not be conflated: None means we
    could not ask, "" means we asked and the graph is empty. Treating the
    second as the first is how this watchdog reported a healthy appliance with
    nothing connected to the speakers.
    """
    try:
        proc = subprocess.run(["jack_lsp", "-c"], capture_output=True,
                              text=True, timeout=10)
    except Exception:
        return None
    return proc.stdout if proc.returncode == 0 else None


def playback_sources(graph: str) -> set[str]:
    found, cur = set(), None
    for line in graph.splitlines():
        if not line.startswith((" ", "\t")):
            cur = line.strip()
        elif cur and cur.startswith("system:playback"):
            found.add(line.strip())
    return found


def jack_client_visible(graph: str) -> bool:
    """Is SooperLooper actually ON the JACK graph?

    The failure this exists to catch (verified live 2026-08-15): jackd
    restarts, SooperLooper survives as a process but loses its JACK client and
    never re-registers. `/set` and `/hit` go through push_nonrt_event(), which
    is drained from the JACK *process callback* — no callback, no drain, so
    commands vanish silently while `/get` reads state directly and keeps
    answering. That is indistinguishable from the "unknown wedge" unless
    something looks at the graph, which nothing here used to do.

    Symptoms it explains: pads go green with no audio, the grid stays
    quantized after a reset, and the JACK repair below fails forever because
    it is connecting a port that does not exist.
    """
    return any(line.startswith(f"{JACK_CLIENT}:") for line in graph.splitlines())


def read_governor() -> str | None:
    """Current CPU governor, or None if this board has no cpufreq."""
    try:
        return GOVERNOR_PATH.read_text(errors="replace").strip()
    except OSError:
        return None


def repair_governor() -> tuple[bool, str]:
    """Re-assert the pinned governor. Returns (repaired, detail).

    Deliberately loud at the call site: an auto-repair that logs nothing turns a
    governor that drifts into a governor that flaps, which is the same lost
    information with better symptoms. Every repair is logged with a timestamp so
    the drift becomes a dated series you can line up against deploys.
    """
    try:
        proc = subprocess.run(
            ["sudo", "-n", "systemctl", "restart", GOVERNOR_UNIT],
            capture_output=True, text=True, timeout=30)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip().splitlines()
        return False, f"exit {proc.returncode}: {err[0] if err else 'no output'}"
    after = read_governor()
    if after != GOVERNOR_TARGET:
        return False, f"unit ran but governor is still {after!r}"
    return True, f"{GOVERNOR_TARGET} re-asserted via {GOVERNOR_UNIT}"


class XrunCounter:
    """Counts new xrun markers in the engine log without re-reading it.

    The log grows without bound while the engine runs (161 KB and climbing when
    this was written), and this polls every 10 s on the same box that is trying
    to make audio — so it tracks a byte offset and reads only what is new.

    Handles the log being rotated, truncated or replaced (inode change or a
    shrink): that resets the offset rather than reporting a wild negative delta.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._offset = 0
        self._inode: int | None = None
        self._events: list[tuple[float, int]] = []

    def poll(self, when: float) -> tuple[int, str | None]:
        """Read new bytes. Returns (new_marker_count, error_or_None)."""
        try:
            st = self.path.stat()
        except OSError as exc:
            return 0, f"no engine log at {self.path} ({exc.__class__.__name__})"
        if self._inode is not None and (st.st_ino != self._inode
                                        or st.st_size < self._offset):
            self._offset = 0
        self._inode = st.st_ino
        if st.st_size == self._offset:
            self._events.append((when, 0))
            return 0, None
        try:
            with self.path.open("r", errors="replace") as fh:
                fh.seek(self._offset)
                chunk = fh.read()
                self._offset = fh.tell()
        except OSError as exc:
            return 0, f"cannot read engine log ({exc.__class__.__name__})"
        count = chunk.count(XRUN_MARKER)
        self._events.append((when, count))
        return count, None

    def rate_per_min(self, when: float) -> float | None:
        """Xruns/min over the trailing window, or None until it has a span."""
        cutoff = when - XRUN_WINDOW_S
        self._events = [(t, c) for t, c in self._events if t >= cutoff]
        if len(self._events) < 2:
            return None
        span = when - self._events[0][0]
        if span <= 0:
            return None
        # The first sample's count accrued before the window opened.
        total = sum(c for _t, c in self._events[1:])
        return round(total * 60.0 / span, 1)


def engine_running() -> bool | None:
    """Is there a SooperLooper process at all? None if we could not ask.

    An orphan and a stopped engine look identical on the JACK graph — neither
    has a `mpe-looper:*` client. They are opposite situations: an orphan is a
    live process silently discarding commands and needs a human now; a stopped
    engine is the normal state on a freshly booted appliance nobody has started
    the looper on yet. Reporting the second as the first is how a watchdog
    running at boot alarms ORPHAN every 10 s forever and teaches its operator
    to ignore it — at which point the alarm that matters is also ignored.

    `pgrep -x` matches restart-sooperlooper.sh's own liveness test, so the two
    agree on what "running" means.
    """
    try:
        proc = subprocess.run(["pgrep", "-x", "sooperlooper"],
                              capture_output=True, text=True, timeout=5)
    except Exception:
        return None
    return proc.returncode == 0


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


CURRENT_METRICS: dict = {}


def write_alarm(state: str, detail: dict) -> None:
    # Metrics ride on EVERY write, including "ok". A rate you can only see when
    # something is already wrong is not monitoring.
    payload = {"updated_at": time.time(), "state": state,
               **CURRENT_METRICS, **detail}
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

    xruns = XrunCounter(ENGINE_LOG)

    while True:
        problems, repaired = [], []
        alarm_written = False
        cycle_t = time.time()

        # --- host health: xrun rate + governor ------------------------------
        # Computed FIRST so every alarm written below carries them, including
        # the orphan/wedge paths that return early from the interesting work.
        new_xruns, xrun_err = xruns.poll(cycle_t)
        rate = xruns.rate_per_min(cycle_t)
        governor = read_governor()
        CURRENT_METRICS.clear()
        CURRENT_METRICS["xruns_per_min"] = rate
        CURRENT_METRICS["xruns_last_cycle"] = new_xruns
        CURRENT_METRICS["governor"] = governor
        if xrun_err:
            CURRENT_METRICS["xrun_source"] = xrun_err

        # Safe to repair: re-pinning the governor cannot destroy a take, so it
        # belongs with the JACK reconnect below, not with the alarm-and-wait
        # cases. Opt-in — an appliance with no MPE_CPU_GOVERNOR is not managed.
        if GOVERNOR_TARGET and governor is not None and governor != GOVERNOR_TARGET:
            if args.no_repair:
                problems.append(f"governor is {governor}, expected {GOVERNOR_TARGET}")
            else:
                ok, detail = repair_governor()
                if ok:
                    repaired.append(f"governor {governor} -> {GOVERNOR_TARGET}")
                    log(f"!! GOVERNOR HAD DRIFTED to {governor} — repaired "
                        f"({detail}). Something reset it; this line is the "
                        f"timestamp to correlate against.")
                    CURRENT_METRICS["governor"] = read_governor()
                else:
                    problems.append(f"governor is {governor}, expected "
                                    f"{GOVERNOR_TARGET}, and repair FAILED: {detail}")
                    log(f"!! governor drift NOT repaired: {detail}")

        if rate is not None and rate > XRUN_ALARM_PER_MIN:
            problems.append(f"xruns {rate}/min over the last "
                            f"{XRUN_WINDOW_S:.0f}s (threshold {XRUN_ALARM_PER_MIN}/min)")

        # --- is the engine even on JACK? ------------------------------------
        # Check this FIRST. An orphan looks exactly like a wedge to the OSC
        # probe below, and every JACK repair against it is futile — so
        # diagnosing in the other order names the wrong component.
        graph = jack_graph()
        orphan = False
        stopped = False
        # None means we could not ask (pgrep missing or timed out). That is not
        # the same as "running", so it must not be reported as one: the orphan
        # alarm below asserts a live process, and asserting it unverified is the
        # failure mode this whole file exists to avoid. Alarm anyway — loud on
        # the control path — but say which of the two we actually established.
        running = None if graph is None else engine_running()
        if graph is None:
            problems.append("jack_lsp unavailable — JACK down or not reachable")
        elif not jack_client_visible(graph) and running is False:
            # Not a fault. Nothing to repair, nothing to alarm — but say so
            # every cycle, because "watchdog up, engine deliberately down" and
            # "watchdog died" must not produce the same alarm file.
            stopped = True
            write_alarm("engine-down", {
                "detail": "no sooperlooper process and no JACK client — the "
                          "looper is not running",
                "action": "mpe looper sl-restart (safe: there are no loops to lose)",
            })
            alarm_written = True
        elif not jack_client_visible(graph):
            orphan = True
            _proc = ("process is up" if running
                     else "process state UNKNOWN (pgrep failed)")
            problems.append(f"ORPHAN: {JACK_CLIENT} {_proc} but has no JACK "
                            f"client (jackd restarted under it?)")
            write_alarm("orphan", {
                "detail": f"no {JACK_CLIENT}:* ports on the graph; commands go "
                          f"into a queue nothing drains ({_proc})",
                "action": "mpe looper sl-restart (DESTROYS loops — human call)",
                "diagnostics": capture_wedge_diagnostics(),
            })
            alarm_written = True
            log("!! ENGINE ORPHANED — on no JACK client, so every /set and /hit "
                "is silently discarded. Not auto-restarting (that would destroy "
                "your loops). Fix: mpe looper sl-restart")

        # --- audio path: safe to repair -------------------------------------
        # Pointless while orphaned: the ports being connected do not exist.
        if graph is not None and not orphan and not stopped:
            srcs = playback_sources(graph)
            if not any(s.startswith(f"{JACK_CLIENT}:common_out") for s in srcs):
                problems.append("common_out not connected to system:playback")
                if not args.no_repair:
                    script = REPO_ROOT / "scripts/sooperlooper/wire-jack-graph.sh"
                    try:
                        proc = subprocess.run(["bash", str(script), "connect"],
                                              capture_output=True, text=True,
                                              timeout=60)
                        after = jack_graph()
                        if after is not None and any(
                                s.startswith(f"{JACK_CLIENT}:common_out")
                                for s in playback_sources(after)):
                            repaired.append("reconnected common_out -> playback")
                            problems.pop()
                        else:
                            # A repair that fails silently every 10s is how this
                            # watchdog logged PROBLEM for 45 minutes while saying
                            # nothing about why.
                            log(f"repair did not take: wire-jack-graph.sh exited "
                                f"{proc.returncode}")
                            for stream, text in (("out", proc.stdout),
                                                 ("err", proc.stderr)):
                                for line in (text or "").strip().splitlines():
                                    log(f"  wire-jack {stream}: {line}")
                    except Exception as exc:
                        log(f"repair failed: {exc}")

        # --- control path: NEVER auto-repair (restart destroys takes) --------
        # Skipped while orphaned: it would report WEDGED, which is true but
        # names the wrong cause and sends the next debugging session into the
        # engine internals instead of at the JACK graph.
        state = None if (orphan or stopped) else osc.get("state")
        if orphan or stopped:
            pass
        elif state is None:
            problems.append("engine not answering OSC")
        else:
            # Shared with sl-health. Both used to write the same control with
            # the same two alternating values, so a hand-run health check and
            # this loop could each read the other's write and report WEDGED —
            # whose remedy destroys every recorded loop. A monitoring race must
            # never recommend a data-losing action.
            verdict, detail = check_command_path(
                lambda ctrl: osc.get(ctrl, loop=PROBE_LOOP),
                lambda ctrl, val: osc.client.send_message(
                    f"/sl/{PROBE_LOOP}/set", [ctrl, val]),
                seed="sl-watchdog",
            )
            if verdict == ALIVE:
                wedged_since = None
            else:
                problems.append(f"WEDGED: {detail}")
                if wedged_since is None:
                    wedged_since = time.time()
                    diag = capture_wedge_diagnostics()
                    write_alarm("wedged", {
                        "detail": f"OSC /set ignored; /get still answers ({detail})",
                        "action": "mpe looper sl-restart (DESTROYS loops — human call)",
                        "diagnostics": diag,
                    })
                    alarm_written = True
                    log("!! ENGINE WEDGED — not auto-restarting (that would destroy "
                        "your loops). Diagnostics captured. Fix: mpe looper sl-restart")

        for r in repaired:
            log(f"repaired: {r}")
        if problems:
            log("PROBLEM: " + "; ".join(problems))
            # The rich orphan/wedge alarms above fire only on the transition,
            # because they capture per-thread diagnostics and that is not free.
            # Every other cycle still has to refresh the file, or `updated_at`
            # becomes the timestamp of a fault rather than a heartbeat and
            # nothing downstream can tell "still broken" from "watchdog died".
            if not alarm_written:
                write_alarm("problem", {"problems": problems})
        elif not alarm_written:
            # `alarm_written` is already true in the engine-down case; writing
            # "ok" over it would report a running looper on an appliance that
            # has none.
            write_alarm("ok", {})

        if args.once:
            return 1 if problems else 0
        time.sleep(INTERVAL_S)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        sys.exit(0)
