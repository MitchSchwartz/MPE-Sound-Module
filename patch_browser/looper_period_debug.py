"""Optional real-time period budget instrumentation (MPE_LOOPER_DEBUG=1).

Hot path is allocation-free: only monotonic compare + counters per period.
/proc/asound reads and journal lines happen on the 5s summary only.
"""

from __future__ import annotations

import os
import time

from patch_browser.clip_matrix import ClipMatrix, ClipState
from patch_browser.looper_xruns import any_pcm_xrun_state, read_pcm_states, read_xrun_counts


def looper_debug_enabled() -> bool:
    return os.environ.get("MPE_LOOPER_DEBUG", "").strip().lower() in ("1", "true", "yes")


def count_playing_layers(matrix: ClipMatrix) -> int:
    return sum(1 for slot in matrix.slots.values() if slot.state == ClipState.PLAYING)


def _short_pcm_path(path: str) -> str:
    return path.removeprefix("/proc/asound/")


class LooperPeriodDebug:
    """Track audio-loop overruns vs ALSA period budget (cheap per-period)."""

    def __init__(self, *, period_budget_s: float) -> None:
        self.budget_s = period_budget_s
        self.budget_ms = period_budget_s * 1000.0
        self.window_overruns = 0
        self.window_max_ms = 0.0
        self.window_max_layers = 0
        self.total_overruns = 0

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
        if self.window_overruns == 0:
            return
        states = read_pcm_states()
        xrun_paths = [_short_pcm_path(p) for p in any_pcm_xrun_state(states)]
        hot_counts = {
            _short_pcm_path(path): count
            for path, count in read_xrun_counts().items()
            if count > 0
        }
        print(
            f"[debug] {label} summary overruns={self.window_overruns} "
            f"max_elapsed={self.window_max_ms:.2f}ms budget={self.budget_ms:.2f}ms "
            f"max_layers={self.window_max_layers} total={self.total_overruns} "
            f"pcm_xrun={xrun_paths or 'none'} xrun_counts={hot_counts or '{}'}",
            flush=True,
        )
        self.window_overruns = 0
        self.window_max_ms = 0.0
        self.window_max_layers = 0
