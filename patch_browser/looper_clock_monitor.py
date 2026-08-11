"""Read looper MIDI clock state for the touch patch browser header HUD."""

from __future__ import annotations

import threading
import time

from patch_browser.looper_hud import merge_looper_hud_snapshot
from patch_browser.midi_clock import read_clock_state, stabilize_display_bpm

POLL_INTERVAL_S = 0.016  # ~60 Hz — keep HUD in sync with audio transport


class LooperClockMonitor:
    """Background reader for ~/.mpe_midi_clock_state.json (midi-clock-in daemon)."""

    def __init__(self, *, poll_interval: float = POLL_INTERVAL_S) -> None:
        self.poll_interval = poll_interval
        self._lock = threading.Lock()
        self._snapshot = merge_looper_hud_snapshot(read_clock_state())
        self._display_bpm: int | None = self._snapshot.get("bpm")
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._worker,
            daemon=True,
            name="LooperClockMonitor",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._snapshot)

    def _worker(self) -> None:
        while not self._stop.wait(self.poll_interval):
            snap = read_clock_state()
            raw = snap.get("bpm")
            if raw is None:
                self._display_bpm = None
            elif isinstance(raw, (int, float)):
                self._display_bpm = stabilize_display_bpm(float(raw), self._display_bpm)
            snap = dict(snap)
            snap["bpm"] = self._display_bpm
            snap = merge_looper_hud_snapshot(snap)
            with self._lock:
                self._snapshot = snap
