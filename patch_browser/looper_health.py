"""Always-on looper health: deadline utilization and xrun count.

CPU% cannot answer "is the audio OK". The header meter samples the Surge
process, so a looper thread that blows every period deadline is invisible in it
— which is how a 13 ms mixer hid behind a comfortable-looking meter while the
DAC starved. The metrics that do answer it are how much of the period budget
producing one period actually costs (worst case, not mean, because the worst
case is what you hear) and how many periods ALSA dropped.

``LooperPeriodDebug`` covers the same ground in far more detail, but only when
MPE_LOOPER_DEBUG=1 and only into the journal. This is the cheap always-on
subset the HUD can show. Pure logic — the caller supplies elapsed times and
xrun totals.
"""

from __future__ import annotations

from patch_browser.looper_period_debug import MsHistogram

# Report the previous window rather than a running peak so the HUD holds a
# readable value instead of flickering, and stale spikes age out.
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
        """Health for the last completed window; percentages are of the period budget."""
        return {
            "budget_ms": round(self.budget_ms, 3),
            "max_pct": None if self._max_pct is None else round(self._max_pct, 1),
            "p95_pct": None if self._p95_pct is None else round(self._p95_pct, 1),
            "over_budget": self._last_over_budget,
            "xruns": int(xruns),
        }
