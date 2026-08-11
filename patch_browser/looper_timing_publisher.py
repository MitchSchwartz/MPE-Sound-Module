"""Publish HUD timing each audio period (frame-accurate; touch derives 1/8 ticks)."""

from __future__ import annotations

from dataclasses import dataclass, field

from patch_browser.looper_hud import looper_hud_eighth_index, looper_hud_tick_in_bar
from patch_browser.looper_timing_state import clear_timing_state, write_timing_state


@dataclass
class LooperTimingPublisher:
    """Write transport position every period while clips are running."""

    _last_total_frames: int | None = field(default=None, init=False)

    def publish_from_matrix(self, matrix) -> None:
        if not matrix.is_active:
            return

        clock = matrix.clock
        snap = clock.snapshot()
        fpb = max(1, clock.frames_per_beat)
        beats = max(1, clock.beats_per_bar)
        total = int(snap["total_frames"])
        if total == self._last_total_frames:
            return

        self._last_total_frames = total
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
            eighth_index=looper_hud_eighth_index(
                total_frames=total,
                frames_per_beat=fpb,
                beats_per_bar=beats,
            ),
            total_frames=total,
            frames_per_beat=fpb,
        )

    def clear(self) -> None:
        clear_timing_state()
        self._last_total_frames = None
