"""Publish HUD timing at ~25 Hz (touch interpolates frames between publishes)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from patch_browser.looper_hud import looper_hud_eighth_index, looper_hud_tick_in_bar
from patch_browser.looper_timing_state import clear_timing_state, write_timing_state

PUBLISH_INTERVAL_S = 0.040


@dataclass
class LooperTimingPublisher:
    """Write transport position while clips run, capped at ``PUBLISH_INTERVAL_S``.

    The audio callback runs at ~94 Hz; serialising JSON that often was the transport
    stall this throttle exists to prevent. The touch HUD covers the gap by
    extrapolating from ``updated_at`` + ``sample_rate`` (see ``looper_hud``).
    """

    publish_interval: float = PUBLISH_INTERVAL_S
    time_source: Callable[[], float] = time.monotonic
    _last_total_frames: int | None = field(default=None, init=False)
    _last_publish_at: float | None = field(default=None, init=False)

    def publish_from_matrix(self, matrix, *, health: dict | None = None) -> None:
        if not matrix.is_active:
            # Arm the immediate-publish path for whenever clips start again.
            self._last_publish_at = None
            return

        clock = matrix.clock
        snap = clock.snapshot()
        fpb = max(1, clock.frames_per_beat)
        beats = max(1, clock.beats_per_bar)
        total = int(snap["total_frames"])
        if total == self._last_total_frames:
            return

        # First publish after activation (or after clear) goes out immediately so the
        # HUD appears without a visible delay; later ones are rate-capped.
        now = self.time_source()
        if (
            self._last_publish_at is not None
            and (now - self._last_publish_at) < self.publish_interval
        ):
            return

        self._last_total_frames = total
        self._last_publish_at = now
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
            sample_rate=int(clock.sample_rate),
            health=health,
        )

    def clear(self) -> None:
        clear_timing_state()
        self._last_total_frames = None
        self._last_publish_at = None
