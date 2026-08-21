"""Always-on audio graph health for the looper HUD.

Salvaged from ``yolo/looper-phase0`` commit f39d0a6 (mpe-looper period timing).
Re-pointed at JACK: the header CPU meter samples Surge only, so SooperLooper
xruns that never touch Surge were invisible — exactly the 15 xruns / 30 s /
journal-0 failure mode.

``LooperHealth`` keeps the original period-budget tracker for tests and any
future JACK callback instrumentation. ``JackGraphHealth`` is what
``sl_hud_monitor`` uses: rolling ``jack_cpu_load`` peak plus session xrun
count from ``mpe-jackd`` journal lines.
"""

from __future__ import annotations

import re
import subprocess
import threading
import time
from datetime import datetime, timezone

_BUCKETS = 128

# jack_cpu_load prints a fresh sample on its own cadence; give up on a reader that has
# produced nothing for this long and respawn it.
JACK_CPU_STALE_S = 10.0
JACK_CPU_RESPAWN_BACKOFF_S = 5.0


class MsHistogram:
    """Fixed-width histogram of millisecond (or percent) samples."""

    def __init__(self, *, bucket_ms: float, buckets: int = _BUCKETS) -> None:
        self.bucket_ms = bucket_ms if bucket_ms > 0 else 1.0
        self.buckets = buckets
        self.counts = [0] * buckets
        self.count = 0
        self.max_ms = 0.0

    def add(self, value_ms: float) -> None:
        idx = int(value_ms / self.bucket_ms)
        if idx < 0:
            idx = 0
        elif idx >= self.buckets:
            idx = self.buckets - 1
        self.counts[idx] += 1
        self.count += 1
        if value_ms > self.max_ms:
            self.max_ms = value_ms

    def percentile(self, p: float) -> float:
        if self.count <= 0:
            return 0.0
        target = max(0, min(1, p)) * self.count
        seen = 0
        for idx, count in enumerate(self.counts):
            seen += count
            if seen >= target:
                return (idx + 0.5) * self.bucket_ms
        return self.max_ms

    def reset(self) -> None:
        self.counts = [0] * self.buckets
        self.count = 0
        self.max_ms = 0.0


WINDOW_S = 2.0
_BUCKETS_PER_BUDGET = 32


class LooperHealth:
    """Rolling deadline-utilization tracker. One compare + increment per period."""

    def __init__(self, *, period_budget_s: float, window_s: float = WINDOW_S) -> None:
        budget_ms = period_budget_s * 1000.0
        self.budget_ms = budget_ms if budget_ms > 0 else 1.0
        self.window_s = window_s
        self._hist = MsHistogram(bucket_ms=self.budget_ms / _BUCKETS_PER_BUDGET)
        self._over_budget = 0
        self._window_started_s: float | None = None
        self._max_pct: float | None = None
        self._p95_pct: float | None = None
        self._last_over_budget = 0

    def record_period(self, elapsed_s: float, now_s: float) -> None:
        elapsed_ms = elapsed_s * 1000.0
        self._hist.add(elapsed_ms)
        if elapsed_ms > self.budget_ms:
            self._over_budget += 1
        if self._window_started_s is None:
            self._window_started_s = now_s
        elif now_s - self._window_started_s >= self.window_s:
            self._roll(now_s)

    def _roll(self, now_s: float) -> None:
        self._max_pct = self._hist.max_ms / self.budget_ms * 100.0
        self._p95_pct = self._hist.percentile(0.95) / self.budget_ms * 100.0
        self._last_over_budget = self._over_budget
        self._hist.reset()
        self._over_budget = 0
        self._window_started_s = now_s

    def snapshot(self, *, xruns: int = 0) -> dict:
        return {
            "budget_ms": round(self.budget_ms, 3),
            "max_pct": None if self._max_pct is None else round(self._max_pct, 1),
            "p95_pct": None if self._p95_pct is None else round(self._p95_pct, 1),
            "over_budget": self._last_over_budget,
            "xruns": int(xruns),
        }


class JackGraphHealth:
    """Rolling JACK DSP load + session xrun count for the SooperLooper HUD."""

    def __init__(self, *, window_s: float = WINDOW_S, started_at: float | None = None) -> None:
        self.window_s = window_s
        self.started_at = time.time() if started_at is None else started_at
        self._hist = MsHistogram(bucket_ms=5.0)
        self._window_started_s: float | None = None
        self._max_pct: float | None = None
        self._p95_pct: float | None = None
        self._session_xruns = 0
        # Both probes are stateful and long-lived — one JACK client and one journal
        # cursor for the life of the monitor, not a fork per sample.
        self.cpu_reader = JackCpuLoadReader()
        self.xrun_counter = JournalXrunCounter(self.started_at)

    def close(self) -> None:
        """Release held JACK and journal follower processes."""
        self.cpu_reader.close()
        self.xrun_counter.close()

    def sample(self, *, cpu_load_pct: float | None, xruns_total: int, now_s: float) -> None:
        self._session_xruns = max(self._session_xruns, int(xruns_total))
        if cpu_load_pct is not None and cpu_load_pct >= 0.0:
            self._hist.add(cpu_load_pct)
        if self._window_started_s is None:
            self._window_started_s = now_s
        elif now_s - self._window_started_s >= self.window_s:
            self._roll(now_s)

    def _roll(self, now_s: float) -> None:
        if self._hist.count > 0:
            self._max_pct = round(self._hist.max_ms, 1)
            self._p95_pct = round(self._hist.percentile(0.95), 1)
        self._hist.reset()
        self._window_started_s = now_s

    def snapshot(self) -> dict:
        return {
            "budget_ms": None,
            "max_pct": self._max_pct,
            "p95_pct": self._p95_pct,
            "over_budget": 0,
            "xruns": self._session_xruns,
        }


_JACK_CPU_RE = re.compile(r"jack DSP load\s+([\d.]+)", re.I)


class JackCpuLoadReader:
    """One long-lived ``jack_cpu_load`` client, drained by a background thread.

    Forking ``jack_cpu_load`` per sample registered **and tore down** a JACK client
    twice a second. Every client registration makes jackd recompute and re-sort the
    process graph, so the probe that measures glitching was manufacturing it. Holding
    one client open costs a single reorder for the life of the monitor.
    """

    def __init__(self, *, argv: list[str] | None = None) -> None:
        self.argv = argv or ["jack_cpu_load"]
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._latest: float | None = None
        self._latest_at = 0.0
        self._unavailable = False
        self._last_spawn_attempt = 0.0

    def _spawn(self, now: float) -> bool:
        if self._unavailable:
            return False
        if now - self._last_spawn_attempt < JACK_CPU_RESPAWN_BACKOFF_S:
            return False
        self._last_spawn_attempt = now
        try:
            proc = subprocess.Popen(
                self.argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
        except (FileNotFoundError, OSError):
            # Binary absent — never retry; jack-example-tools is not installed.
            self._unavailable = True
            return False
        self._proc = proc
        self._thread = threading.Thread(
            target=self._drain, args=(proc,), daemon=True, name="JackCpuLoadReader"
        )
        self._thread.start()
        return True

    def _drain(self, proc: subprocess.Popen) -> None:
        stream = proc.stdout
        if stream is None:
            return
        for line in stream:
            match = _JACK_CPU_RE.search(line)
            if not match:
                continue
            try:
                value = float(match.group(1))
            except ValueError:
                continue
            with self._lock:
                self._latest = value
                self._latest_at = time.monotonic()

    def _alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def read(self) -> float | None:
        """Most recent DSP load percent, or None when stale/unavailable."""
        now = time.monotonic()
        if not self._alive():
            self.close()
            self._spawn(now)
            return None
        with self._lock:
            latest, latest_at = self._latest, self._latest_at
        if latest is None:
            return None
        if now - latest_at > JACK_CPU_STALE_S:
            # Reader wedged (server gone, client zombied) — recycle it.
            self.close()
            self._spawn(now)
            return None
        return latest

    def close(self) -> None:
        proc, self._proc = self._proc, None
        with self._lock:
            self._latest = None
            self._latest_at = 0.0
        if proc is None:
            return
        # SIGKILL, not SIGTERM. Measured on the appliance 2026-08-17: jack_cpu_load
        # does not die on SIGTERM, so a polite terminate leaves it orphaned — still
        # holding 4 jackd FDs and 13 shm mappings — forever. That is what filled the
        # JACK client registry with 705 zombie clients.
        try:
            proc.kill()
            proc.wait(timeout=2.0)
        except (subprocess.TimeoutExpired, OSError):
            pass


class JournalXrunCounter:
    """Incremental xrun count from the ``mpe-jackd`` journal.

    Holds one ``journalctl -f`` process for the life of the monitor. ``poll()`` reads
    an in-memory total — no fork per HUD tick. The old form forked ``journalctl`` every
    0.5 s (even with ``--after-cursor``), hitting SD-backed journal I/O on CPU0.
    """

    def __init__(self, started_at: float, *, unit: str = "mpe-jackd.service") -> None:
        self.started_at = started_at
        self.unit = unit
        self._lock = threading.Lock()
        self._total = 0
        self._unavailable = False
        self._anchored = False
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._last_spawn_attempt = 0.0
        self._spawn()

    def _since_argv(self) -> list[str]:
        since = datetime.fromtimestamp(self.started_at, tz=timezone.utc).astimezone()
        return [
            "journalctl",
            "-u",
            self.unit,
            "-f",
            "--since",
            since.isoformat(),
            "--no-pager",
        ]

    def _spawn(self, now: float | None = None) -> bool:
        if self._unavailable:
            return False
        if now is None:
            now = time.monotonic()
        if now - self._last_spawn_attempt < JACK_CPU_RESPAWN_BACKOFF_S:
            return False
        self._last_spawn_attempt = now
        try:
            proc = subprocess.Popen(
                self._since_argv(),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
        except (FileNotFoundError, OSError):
            self._unavailable = True
            return False
        self._proc = proc
        self._thread = threading.Thread(
            target=self._drain,
            args=(proc,),
            daemon=True,
            name="JournalXrunFollower",
        )
        self._thread.start()
        return True

    def _count_line(self, line: str) -> None:
        if "xrun" not in line.lower():
            return
        with self._lock:
            self._total += 1

    def _drain(self, proc: subprocess.Popen) -> None:
        stream = proc.stdout
        if stream is None:
            return
        for line in stream:
            self._anchored = True
            self._count_line(line)

    def _alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def poll(self) -> int | None:
        """Cumulative xruns since *started_at*, or None if the journal is unreadable."""
        if self._unavailable:
            return None
        if not self._alive():
            self.close()
            self._spawn()
            if not self._alive():
                return None
        if not self._anchored:
            # Follower running but no lines yet — journal empty or still catching up.
            return 0
        with self._lock:
            return self._total

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.kill()
            proc.wait(timeout=2.0)
        except (subprocess.TimeoutExpired, OSError):
            pass


def read_jack_cpu_load_pct(*, timeout_s: float = 1.0) -> float | None:
    """One-shot DSP load — diagnostics only.

    NOT for polling: each call registers and unregisters a JACK client, forcing two
    graph reorders. Use ``JackCpuLoadReader`` on any repeating path.

    ``-k`` is load-bearing: jack_cpu_load ignores SIGTERM, so a bare ``timeout N``
    exits and leaves the client orphaned on the graph forever.
    """
    try:
        proc = subprocess.run(
            ["timeout", "-k", "0.5", str(max(0.2, timeout_s)), "jack_cpu_load"],
            capture_output=True,
            text=True,
            timeout=timeout_s + 2.0,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    last: float | None = None
    for line in proc.stdout.splitlines():
        match = _JACK_CPU_RE.search(line)
        if match:
            last = float(match.group(1))
    return last


def jackd_journal_xruns_since(started_at: float) -> int | None:
    """One-shot journal xrun count — diagnostics only.

    NOT for polling: one fork per call. Use ``JournalXrunCounter`` on any repeating path.
    """
    since = datetime.fromtimestamp(started_at, tz=timezone.utc).astimezone()
    try:
        proc = subprocess.run(
            [
                "journalctl",
                "-u",
                "mpe-jackd.service",
                "--since",
                since.isoformat(),
                "--no-pager",
            ],
            capture_output=True,
            text=True,
            timeout=5.0,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    return sum(1 for line in proc.stdout.splitlines() if "xrun" in line.lower())


def collect_jack_graph_health(tracker: JackGraphHealth) -> dict:
    """Sample JACK and return a health dict for the HUD state file."""
    now = time.monotonic()
    xruns = tracker.xrun_counter.poll()
    cpu = tracker.cpu_reader.read()
    tracker.sample(
        cpu_load_pct=cpu,
        xruns_total=xruns if xruns is not None else tracker._session_xruns,
        now_s=now,
    )
    return tracker.snapshot()
