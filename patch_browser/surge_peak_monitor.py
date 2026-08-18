"""Passive OUT peak meter reader for the touch UI.

Phase 5 (session-control-plane-spec): the JACK tap is a compiled out-of-process
client (``mpe-peak-meter``) writing ``/run/mpe/meter.state``. This module is
Edge-plane only — it never registers a JACK process callback.
"""

from __future__ import annotations

import os
import threading
import time

from patch_browser.audio_engine import METER_STATE_FILE, read_meter_state
from patch_browser.peak_meter_math import dbfs_to_meter_ratio, linear_peak_to_dbfs

POLL_INTERVAL_S = 0.2  # 5 Hz — UI reads snapshot only
# Peak hold/decay lives in the compiled meter (mpe-peak-meter writer thread).
PEAK_METER_ENV = "MPE_PEAK_METER"


def peak_meter_enabled() -> bool:
    """True when the operator has opted the compiled JACK tap in."""
    return os.environ.get(PEAK_METER_ENV, "0").strip().lower() in ("1", "true", "yes", "on")


def _parse_peak_linear(raw: str | None) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    if value < 0.0 or not (value == value):  # NaN
        return None
    return value


class SurgePeakMonitor:
    """Reads compiled meter state — never joins the JACK graph."""

    def __init__(
        self,
        surge_monitor,
        *,
        poll_interval: float = POLL_INTERVAL_S,
        state_path=None,
    ) -> None:
        self.surge_monitor = surge_monitor
        self.poll_interval = poll_interval
        self._state_path = state_path or METER_STATE_FILE
        self._lock = threading.Lock()
        self._peak_linear = 0.0
        self._online = False
        self._source = "none"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._worker,
            daemon=True,
            name="SurgePeakMonitor",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.5)

    def snapshot(self) -> dict:
        with self._lock:
            if not self._online:
                return {
                    "online": False,
                    "peak_linear": None,
                    "dbfs": None,
                    "ratio": None,
                    "source": self._source,
                }
            dbfs = linear_peak_to_dbfs(self._peak_linear)
            ratio = dbfs_to_meter_ratio(dbfs)
            if ratio is None:
                ratio = 0.0
            return {
                "online": True,
                "peak_linear": self._peak_linear,
                "dbfs": dbfs,
                "ratio": ratio,
                "source": self._source,
            }

    def _worker(self) -> None:
        while not self._stop.wait(self.poll_interval):
            try:
                self._poll_once()
            except Exception as exc:
                print(f"Surge peak monitor poll error: {exc}")

    def _poll_once(self) -> None:
        healthy, _ = self.surge_monitor.check_health()
        if not healthy or not peak_meter_enabled():
            self._reset_display(online=False)
            return

        state = read_meter_state(self._state_path)
        wired = state.get("wired", state.get("online", "0")) == "1"
        peak_raw = _parse_peak_linear(state.get("peak_linear"))
        source = state.get("source") or ("jack" if wired else "none")

        with self._lock:
            if not wired:
                self._peak_linear = 0.0
            elif peak_raw is not None:
                self._peak_linear = peak_raw
            self._online = wired
            self._source = source if wired else "none"

    def _reset_display(self, *, online: bool) -> None:
        with self._lock:
            self._online = online
            self._peak_linear = 0.0
            self._source = "none"
