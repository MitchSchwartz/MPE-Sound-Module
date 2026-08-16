"""Cached reader for /run/mpe/engine.state (touch HUD)."""

from __future__ import annotations

import threading
import time

from patch_browser.audio_engine import read_engine_state

POLL_INTERVAL_S = 0.5


class EngineStateMonitor:
    """Background reader for engine.state — avoids per-frame disk reads."""

    def __init__(self, *, poll_interval: float = POLL_INTERVAL_S) -> None:
        self.poll_interval = poll_interval
        self._lock = threading.Lock()
        self._snapshot: dict[str, str] = read_engine_state()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._worker,
            daemon=True,
            name="EngineStateMonitor",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)

    def snapshot(self) -> dict[str, str]:
        with self._lock:
            return dict(self._snapshot)

    def _worker(self) -> None:
        while not self._stop.wait(self.poll_interval):
            snap = read_engine_state()
            with self._lock:
                self._snapshot = snap
