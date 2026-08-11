"""Publish HUD timing on each global beat (monotonic — survives loop wrap)."""

from __future__ import annotations

from dataclasses import dataclass, field

from patch_browser.looper_timing_state import clear_timing_state, write_timing_state


@dataclass
class LooperTimingPublisher:
    """Publish once per global beat index; never skip because (bar,beat) repeats."""

    _last_beat_index: int | None = field(default=None, init=False)

    def publish_from_matrix(self, matrix) -> None:
        if not matrix.is_active:
            clear_timing_state()
            self._last_beat_index = None
            return

        clock = matrix.clock
        snap = clock.snapshot()
        fpb = max(1, clock.frames_per_beat)
        beat_index = int(snap["total_frames"]) // fpb
        if beat_index == self._last_beat_index:
            return

        self._last_beat_index = beat_index
        write_timing_state(
            active=True,
            bpm=float(snap["bpm"]),
            beat_in_bar=int(snap["beat_in_bar"]),
            beats_per_bar=int(snap["beats_per_bar"]),
            bar_in_loop=int(snap["bar_in_loop"]),
            bars_per_loop=int(snap["bars_per_loop"]),
            beat_index=beat_index,
        )

    def clear(self) -> None:
        clear_timing_state()
        self._last_beat_index = None
