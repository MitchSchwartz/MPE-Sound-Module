"""Publish HUD timing on each eighth-note (monotonic index — survives loop wrap)."""

from __future__ import annotations

from dataclasses import dataclass, field

from patch_browser.looper_hud import looper_hud_eighth_index, looper_hud_tick_in_bar
from patch_browser.looper_timing_state import clear_timing_state, write_timing_state


@dataclass
class LooperTimingPublisher:
    """Publish once per global eighth-note; never skip when (bar,beat) repeats."""

    _last_eighth_index: int | None = field(default=None, init=False)

    def publish_from_matrix(self, matrix) -> None:
        if not matrix.is_active:
            clear_timing_state()
            self._last_eighth_index = None
            return

        clock = matrix.clock
        snap = clock.snapshot()
        fpb = max(1, clock.frames_per_beat)
        beats = max(1, clock.beats_per_bar)
        total = int(snap["total_frames"])
        eighth_index = looper_hud_eighth_index(
            total_frames=total,
            frames_per_beat=fpb,
            beats_per_bar=beats,
        )
        if eighth_index == self._last_eighth_index:
            return

        self._last_eighth_index = eighth_index
        tick = looper_hud_tick_in_bar(
            total_frames=total,
            frames_per_beat=fpb,
            beats_per_bar=beats,
        )
        write_timing_state(
            active=True,
            bpm=float(snap["bpm"]),
            beat_in_bar=int(snap["beat_in_bar"]),
            beats_per_bar=beats,
            bar_in_loop=int(snap["bar_in_loop"]),
            bars_per_loop=int(snap["bars_per_loop"]),
            beat_index=total // fpb,
            tick_in_bar=tick,
            eighth_index=eighth_index,
        )

    def clear(self) -> None:
        clear_timing_state()
        self._last_eighth_index = None
