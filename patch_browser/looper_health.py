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
import time
from datetime import datetime, timezone

_BUCKETS = 128


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


def read_jack_cpu_load_pct(*, timeout_s: float = 1.0) -> float | None:
    """Latest ``jack_cpu_load`` sample, or None if unavailable."""
    try:
        proc = subprocess.run(
            ["timeout", str(max(0.2, timeout_s)), "jack_cpu_load"],
            capture_output=True,
            text=True,
            timeout=timeout_s + 1.0,
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
    """Xrun mentions in ``mpe-jackd`` journal since *started_at*, or None if unreadable."""
    since = datetime.fromtimestamp(started_at, tz=timezone.utc).astimezone().isoformat()
    try:
        proc = subprocess.run(
            ["journalctl", "-u", "mpe-jackd.service", "--since", since, "--no-pager"],
            capture_output=True,
            text=True,
            timeout=5.0,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if not proc.stdout.strip():
        return None
    return sum(1 for line in proc.stdout.splitlines() if "xrun" in line.lower())


def collect_jack_graph_health(tracker: JackGraphHealth) -> dict:
    """Sample JACK and return a health dict for the HUD state file."""
    now = time.monotonic()
    xruns = jackd_journal_xruns_since(tracker.started_at)
    cpu = read_jack_cpu_load_pct()
    tracker.sample(
        cpu_load_pct=cpu,
        xruns_total=xruns if xruns is not None else tracker._session_xruns,
        now_s=now,
    )
    return tracker.snapshot()
