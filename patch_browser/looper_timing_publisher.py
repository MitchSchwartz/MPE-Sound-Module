"""Publish HUD timing on discrete bar tick boundaries (sample-clock source)."""

from __future__ import annotations

from dataclasses import dataclass, field

from patch_browser.looper_hud import looper_hud_bar_tick_index
from patch_browser.looper_timing_state import clear_timing_state, write_timing_state


@dataclass
class LooperTimingPublisher:
    """Publish when bar or eighth-note tick changes — one source: audio frame clock."""

    _last_key: tuple[int, int, int] | None = field(default=None, init=False)

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
        tick = looper_hud_bar_tick_index(
            total_frames=int(snap["total_frames"]),
            frames_per_beat=fpb,
            beats_per_bar=beats,
        )
        key = (bar, int(snap["beat_in_bar"]), tick)
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
