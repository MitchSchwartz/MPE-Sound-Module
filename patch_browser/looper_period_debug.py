"""Optional real-time period budget instrumentation (MPE_LOOPER_DEBUG=1).

Hot path is allocation-free: only monotonic compare + counters per period.
/proc/asound reads and journal lines happen on the 5s summary only.

Measures three things, all diagnostic (no runtime behaviour changes):
  H1  period arrival jitter — interval between consecutive period iterations
  H2  timing-publish cost — wall time spent inside the publish call
  H3  phase origin — clock vs clip phase at each RECORDING/PLAYING transition

Underrun counting is not here — see looper_alsa_stderr.
"""

from __future__ import annotations

import os
import time

from patch_browser.clip_matrix import ClipMatrix, ClipState
from patch_browser.looper_xruns import any_pcm_xrun_state, read_pcm_states

# Fixed-edge histogram instead of a reservoir: percentiles stay unbiased over the
# whole 5s window (a reservoir samples), and add() is an index + increment.
_HIST_BUCKETS = 128
_HIST_BUCKETS_PER_BUDGET = 16  # bucket width = budget/16; top bucket = overflow (>= ~8x budget)


def looper_debug_enabled() -> bool:
    return os.environ.get("MPE_LOOPER_DEBUG", "").strip().lower() in ("1", "true", "yes")


def count_playing_layers(matrix: ClipMatrix) -> int:
    return sum(1 for slot in matrix.slots.values() if slot.state == ClipState.PLAYING)


def _short_pcm_path(path: str) -> str:
    return path.removeprefix("/proc/asound/")


class MsHistogram:
    """Bounded fixed-width histogram of millisecond samples."""

    def __init__(self, *, bucket_ms: float, buckets: int = _HIST_BUCKETS) -> None:
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

    def percentile(self, q: float) -> float:
        """Nearest-rank percentile, reported as the containing bucket's upper edge."""
        if self.count == 0:
            return 0.0
        rank = int(q * self.count)
        if rank < 1:
            rank = 1
        elif rank > self.count:
            rank = self.count
        seen = 0
        for idx, bucket_count in enumerate(self.counts):
            seen += bucket_count
            if seen >= rank:
                if idx == self.buckets - 1:
                    return self.max_ms
                edge = (idx + 1) * self.bucket_ms
                return edge if edge < self.max_ms else self.max_ms
        return self.max_ms

    def reset(self) -> None:
        for idx in range(self.buckets):
            self.counts[idx] = 0
        self.count = 0
        self.max_ms = 0.0


class LooperPeriodDebug:
    """Track audio-loop overruns vs ALSA period budget (cheap per-period)."""

    def __init__(self, *, period_budget_s: float) -> None:
        self.budget_s = period_budget_s
        self.budget_ms = period_budget_s * 1000.0
        self.window_overruns = 0
        self.window_max_ms = 0.0
        self.window_max_layers = 0
        self.total_overruns = 0
        self.burst_threshold_ms = self.budget_ms * 0.25
        # Intervals span 0…8x budget; publishes are expected well under budget, so they
        # get 128 buckets across a single budget for usable sub-millisecond resolution.
        self.intervals = MsHistogram(bucket_ms=self.budget_ms / _HIST_BUCKETS_PER_BUDGET)
        self.publishes = MsHistogram(bucket_ms=self.budget_ms / _HIST_BUCKETS)
        self.window_bursts = 0
        self._last_arrival_s: float | None = None
        self._last_states: dict[tuple[int, int], ClipState] = {}

    @classmethod
    def create_if_enabled(cls, *, period_budget_s: float) -> LooperPeriodDebug | None:
        """Return an instrument only when MPE_LOOPER_DEBUG is set (else no hot-path work)."""
        if not looper_debug_enabled():
            return None
        return cls(period_budget_s=period_budget_s)

    def record_arrival(self, arrival_s: float) -> None:
        """H1: interval between consecutive period iterations (pipe delivery cadence)."""
        last = self._last_arrival_s
        self._last_arrival_s = arrival_s
        if last is None:
            return
        interval_ms = (arrival_s - last) * 1000.0
        self.intervals.add(interval_ms)
        if interval_ms < self.burst_threshold_ms:
            self.window_bursts += 1

    def record_publish(self, elapsed_s: float) -> None:
        """H2: wall time spent inside the timing publish call."""
        self.publishes.add(elapsed_s * 1000.0)

    def log_clip_transitions(self, matrix: ClipMatrix) -> None:
        """H3: one line per clip entering RECORDING/PLAYING — clock phase vs clip phase."""
        clock = matrix.clock
        for key, clip in matrix.slots.items():
            state = clip.state
            if self._last_states.get(key) == state:
                continue
            self._last_states[key] = state
            if state not in (ClipState.RECORDING, ClipState.PLAYING):
                continue
            print(
                f"[debug] clip r{clip.row}c{clip.col} -> {state.value} "
                f"total_frames={clock.total_frames} "
                f"bar={clock.bar_in_loop}/{clock.bars_per_loop} beat={clock.beat_in_bar} "
                f"playback_frame={clip.playback_frame} loop_frames={clip.loop_frames}",
                flush=True,
            )

    def record(self, elapsed_s: float, playing_layers: int) -> None:
        elapsed_ms = elapsed_s * 1000.0
        if elapsed_ms <= self.budget_ms:
            return
        self.window_overruns += 1
        self.total_overruns += 1
        if elapsed_ms > self.window_max_ms:
            self.window_max_ms = elapsed_ms
            self.window_max_layers = playing_layers

    def flush_window(self, label: str) -> None:
        self._flush_timing(label)
        if self.window_overruns == 0:
            return
        states = read_pcm_states()
        xrun_paths = [_short_pcm_path(p) for p in any_pcm_xrun_state(states)]
        print(
            f"[debug] {label} summary overruns={self.window_overruns} "
            f"max_elapsed={self.window_max_ms:.2f}ms budget={self.budget_ms:.2f}ms "
            f"max_layers={self.window_max_layers} total={self.total_overruns} "
            f"pcm_xrun_now={xrun_paths or 'none'}",
            flush=True,
        )
        self.window_overruns = 0
        self.window_max_ms = 0.0
        self.window_max_layers = 0

    def _flush_timing(self, label: str) -> None:
        intervals = self.intervals
        publishes = self.publishes
        if intervals.count == 0 and publishes.count == 0:
            return
        publish_max_pct = publishes.max_ms / self.budget_ms * 100.0 if self.budget_ms > 0 else 0.0
        print(
            f"[debug] {label} timing budget={self.budget_ms:.2f}ms "
            f"interval_n={intervals.count} "
            f"p50={intervals.percentile(0.50):.2f}ms "
            f"p95={intervals.percentile(0.95):.2f}ms "
            f"max={intervals.max_ms:.2f}ms "
            f"burst_lt25pct={self.window_bursts} "
            f"publish_n={publishes.count} "
            f"publish_p95={publishes.percentile(0.95):.3f}ms "
            f"publish_max={publishes.max_ms:.3f}ms "
            f"publish_max_pct={publish_max_pct:.1f}%",
            flush=True,
        )
        intervals.reset()
        publishes.reset()
        self.window_bursts = 0
