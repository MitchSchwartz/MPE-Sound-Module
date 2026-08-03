"""Detect when Surge output is pinned at the limiter ceiling (header LIM flash)."""

from __future__ import annotations

import threading
import time

from patch_browser.output_peak_monitor import OutputPeakMonitor
from patch_browser.surge_output_limiter import (
    LIM_LABEL,
    at_limiter_ceiling,
    limiter_active,
)

POLL_INTERVAL_S = 0.08
REDUCING_HOLD_S = 0.2


class SurgeLimiterMonitor:
    """Flash LIM when measured output peak sits at the configured limiter ceiling.

    Surge headless does not expose Conditioner gain-reduction over OSC. On analog
    (standalone) we snoop playback via ALSA dsnoop and treat peak ≈ ceiling dBFS
    as "at limit". USB-host mode has no Pi-side tap — badge stays solid only.
    """

    def __init__(self, surge_monitor, cpu_monitor, patch_loader) -> None:
        self.surge_monitor = surge_monitor
        self.cpu_monitor = cpu_monitor
        self.loader = patch_loader
        self.peak_monitor = OutputPeakMonitor()
        self._lock = threading.Lock()
        self._reducing = False
        self._reducing_until = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self.peak_monitor.start()
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._worker,
            daemon=True,
            name="SurgeLimiterMonitor",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.peak_monitor.stop()
        if self._thread:
            self._thread.join(timeout=1.0)

    def snapshot(self) -> dict:
        with self._lock:
            active = self._reducing and time.monotonic() < self._reducing_until
            peak = self.peak_monitor.snapshot()
            return {
                "reducing": active,
                "label": LIM_LABEL if active else None,
                "peak_dbtp": peak.get("peak_dbtp"),
                "peak_online": peak.get("online"),
            }

    def _worker(self) -> None:
        while not self._stop.wait(POLL_INTERVAL_S):
            try:
                self._poll_once()
            except Exception as exc:
                print(f"Surge limiter monitor poll error: {exc}")

    def _poll_once(self) -> None:
        now = time.monotonic()
        if not limiter_active():
            with self._lock:
                self._reducing = False
                self._reducing_until = 0.0
            return

        healthy, _ = self.surge_monitor.check_health()
        if not healthy:
            with self._lock:
                self._reducing = False
                self._reducing_until = 0.0
            return

        peak = self.peak_monitor.snapshot()
        if not peak.get("online"):
            with self._lock:
                if now >= self._reducing_until:
                    self._reducing = False
            return

        peak_db = float(peak.get("peak_dbtp", -120.0))
        at_ceiling = at_limiter_ceiling(peak_db)
        with self._lock:
            if at_ceiling:
                self._reducing = True
                self._reducing_until = now + REDUCING_HOLD_S
            elif now >= self._reducing_until:
                self._reducing = False
