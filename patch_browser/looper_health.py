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

import os
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

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
        self.xrun_counter = MeterXrunCounter(self.started_at)

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


METER_STATE_FILE = Path(os.environ.get("MPE_METER_STATE", "/run/mpe/meter.state"))
# The meter writes at 5 Hz; three missed writes is generous and still catches a
# dead meter well inside one measurement window.
METER_STALE_AFTER_S = float(os.environ.get("MPE_METER_STALE_AFTER_S", "3.0"))


class MeterXrunCounter:
    """Cumulative xrun count read from the peak meter's state file.

    Replaces JournalXrunCounter (2026-08-20). That class followed the
    ``mpe-jackd`` journal and counted lines containing "xrun" -- but that journal
    carries **no xrun lines at all** on this appliance: three hours of it during
    runs that measured 3-7 xruns/min contained zero. It therefore reported 0
    forever, in the component whose entire job is to notice. sl-watchdog had the
    same hole from the other side, tailing /tmp/sooperlooper.log for "got xrun"
    when that file does not exist.

    ``/run/mpe/meter.state`` is where the real count lives -- written by
    mpe-peak-meter from JACK's xrun callback, and the same source
    scripts/measure-latency-run.sh has always used for its RESULT lines.

    tmpfs, so poll() is a small read with no fork and no thread.

    Returns None -- never a silently wrong 0 -- when the file is missing,
    unparseable, or stale, so callers can tell "no xruns" from "cannot see".
    """

    def __init__(
        self,
        started_at: float,
        *,
        path: Path | None = None,
        stale_after_s: float = METER_STALE_AFTER_S,
    ) -> None:
        self.started_at = started_at
        self.path = Path(path) if path is not None else METER_STATE_FILE
        self.stale_after_s = stale_after_s
        self._baseline: int | None = None
        self._total = 0

    def _read(self) -> tuple[int, float] | None:
        """(xruns, updated_epoch) from the meter file, or None if unusable."""
        try:
            raw = self.path.read_text(encoding="utf-8")
        except (FileNotFoundError, PermissionError, OSError):
            return None
        xruns: int | None = None
        updated: float | None = None
        for line in raw.splitlines():
            key, _, value = line.partition("=")
            try:
                if key == "xruns":
                    xruns = int(value)
                elif key == "updated":
                    updated = float(value)
            except ValueError:
                return None
        if xruns is None or updated is None:
            return None
        return xruns, updated

    def poll(self) -> int | None:
        """Cumulative xruns since the first successful read, or None if unseeable."""
        sample = self._read()
        if sample is None:
            return None
        xruns, updated = sample
        # A meter that has stopped writing looks identical to a quiet one. It is
        # not: report None so the caller raises a problem instead of "healthy".
        if self.stale_after_s > 0 and (time.time() - updated) > self.stale_after_s:
            return None
        if self._baseline is None or xruns < self._baseline:
            # First read, or the meter restarted and its counter went backwards.
            self._baseline = xruns
        self._total = xruns - self._baseline
        return self._total

    def close(self) -> None:
        """No-op. Kept so callers need not care which counter they hold."""


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

    NOT for polling: one fork per call. Use ``MeterXrunCounter`` on any repeating path.
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
