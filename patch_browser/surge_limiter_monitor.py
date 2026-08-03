"""Detect when the Surge output limiter is actively reducing level (header LIM flash)."""

from __future__ import annotations

import threading
import time

from patch_browser.surge_output_limiter import limiter_active

POLL_INTERVAL_S = 0.1
CPU_LIMITING_FLOOR = 20.0
HOT_OUTPUT_LINEAR = 0.85
REDUCING_HOLD_S = 0.25
LIM_LABEL = "LIM"


class SurgeLimiterMonitor:
    """Best-effort gain-reduction proxy for the touch UI header.

    Surge XT headless does not expose Conditioner gain-reduction VU over OSC.
    We treat sustained engine load plus hot combined amp/volume as limiting.
    """

    def __init__(self, surge_monitor, cpu_monitor, patch_loader) -> None:
        self.surge_monitor = surge_monitor
        self.cpu_monitor = cpu_monitor
        self.loader = patch_loader
        self._lock = threading.Lock()
        self._reducing = False
        self._reducing_until = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
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
        if self._thread:
            self._thread.join(timeout=1.0)

    def snapshot(self) -> dict:
        with self._lock:
            active = self._reducing and time.monotonic() < self._reducing_until
            return {"reducing": active, "label": LIM_LABEL if active else None}

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

        cpu = self.cpu_monitor.snapshot()
        raw = cpu.get("raw_percent")
        if not cpu.get("online") or raw is None:
            return

        combined = float(getattr(self.loader, "_patch_gain_linear", 1.0)) * float(
            getattr(self.loader, "user_volume_trim", 1.0)
        )
        likely = raw >= CPU_LIMITING_FLOOR and combined >= HOT_OUTPUT_LINEAR
        with self._lock:
            if likely:
                self._reducing = True
                self._reducing_until = now + REDUCING_HOLD_S
            elif now >= self._reducing_until:
                self._reducing = False
