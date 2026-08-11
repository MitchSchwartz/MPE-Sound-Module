"""Throttled timing publisher — never write JSON on every audio period."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from patch_browser.looper_timing_state import clear_timing_state, write_timing_state


@dataclass
class LooperTimingPublisher:
    """Publish HUD timing at most ~10 Hz unless beat/bar changes."""

    min_interval_s: float = 0.1
    _last_publish: float = field(default=0.0, init=False)
    _last_key: tuple[int, int] | None = field(default=None, init=False)

    def publish_from_matrix(self, matrix) -> None:
        if not matrix.is_active:
            clear_timing_state()
            self._last_key = None
            return

        snap = matrix.clock.snapshot()
        beat = int(snap["beat_in_bar"])
        bar = int(snap["bar_in_loop"])
        key = (beat, bar)
        now = time.monotonic()
        if (
            key == self._last_key
            and self._last_key is not None
            and (now - self._last_publish) < self.min_interval_s
        ):
            return

        self._last_key = key
        self._last_publish = now
        write_timing_state(
            active=True,
            bpm=float(snap["bpm"]),
            beat_in_bar=beat,
            beats_per_bar=int(snap["beats_per_bar"]),
            bar_in_loop=bar,
            bars_per_loop=int(snap["bars_per_loop"]),
        )

    def clear(self) -> None:
        clear_timing_state()
        self._last_key = None
