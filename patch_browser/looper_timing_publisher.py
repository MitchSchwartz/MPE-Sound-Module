"""Throttled timing publisher — HUD reads at ~20 Hz with sub-beat phase."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from patch_browser.looper_timing_state import clear_timing_state, write_timing_state


@dataclass
class LooperTimingPublisher:
    """Publish HUD timing for touch UI (~20 Hz + beat/bar edges)."""

    min_interval_s: float = 0.05
    phase_epsilon: float = 0.03
    _last_publish: float = field(default=0.0, init=False)
    _last_key: tuple[int, int] | None = field(default=None, init=False)
    _last_phase: float = field(default=0.0, init=False)

    def publish_from_matrix(self, matrix) -> None:
        if not matrix.is_active:
            clear_timing_state()
            self._last_key = None
            self._last_phase = 0.0
            return

        snap = matrix.clock.snapshot()
        beat = int(snap["beat_in_bar"])
        bar = int(snap["bar_in_loop"])
        key = (beat, bar)
        fpb = max(1, matrix.clock.frames_per_beat)
        phase = (int(snap["total_frames"]) % fpb) / fpb
        now = time.monotonic()
        if (
            key == self._last_key
            and abs(phase - self._last_phase) < self.phase_epsilon
            and (now - self._last_publish) < self.min_interval_s
        ):
            return

        self._last_key = key
        self._last_phase = phase
        self._last_publish = now
        write_timing_state(
            active=True,
            bpm=float(snap["bpm"]),
            beat_in_bar=beat,
            beats_per_bar=int(snap["beats_per_bar"]),
            bar_in_loop=bar,
            bars_per_loop=int(snap["bars_per_loop"]),
            beat_phase=phase,
        )

    def clear(self) -> None:
        clear_timing_state()
        self._last_key = None
        self._last_phase = 0.0
