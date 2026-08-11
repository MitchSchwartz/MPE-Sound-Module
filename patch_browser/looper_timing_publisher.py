"""Publish HUD timing on discrete bar tick boundaries (sample-clock source)."""

from __future__ import annotations

from dataclasses import dataclass, field

from patch_browser.looper_hud import looper_hud_filled_ticks_in_bar
from patch_browser.looper_timing_state import clear_timing_state, write_timing_state


@dataclass
class LooperTimingPublisher:
    """Publish when bar or filled tick count changes (0..8 in 4/4)."""

    _last_key: tuple[int, int] | None = field(default=None, init=False)

    def publish_from_matrix(self, matrix) -> None:
        if not matrix.is_active:
            clear_timing_state()
            self._last_key = None
            return

        clock = matrix.clock
        snap = clock.snapshot()
        fpb = max(1, clock.frames_per_beat)
        beats = max(1, clock.beats_per_bar)
        bar = int(snap["bar_in_loop"])
        filled = looper_hud_filled_ticks_in_bar(
            total_frames=int(snap["total_frames"]),
            frames_per_beat=fpb,
            beats_per_bar=beats,
        )
        key = (bar, filled)
        if key == self._last_key:
            return

        self._last_key = key
        write_timing_state(
            active=True,
            bpm=float(snap["bpm"]),
            beat_in_bar=int(snap["beat_in_bar"]),
            beats_per_bar=beats,
            bar_in_loop=bar,
            bars_per_loop=int(snap["bars_per_loop"]),
            total_frames=int(snap["total_frames"]),
            frames_per_beat=fpb,
        )

    def clear(self) -> None:
        clear_timing_state()
        self._last_key = None
