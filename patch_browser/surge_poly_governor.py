"""Dynamic poly limit governor — lowers Surge voice cap when CPU is high."""

from __future__ import annotations

import os
import threading
import time

from patch_browser.surge_cpu_monitor import SurgeCpuMonitor
from patch_browser.surge_monitor import SurgeMonitor
from patch_browser.surge_playback import (
    POLY_STATE_FILE,
    clamp_poly_limit,
    poly_floor,
    query_polylimit,
    read_poly_state,
    send_polylimit,
)
from patch_browser.ui_prefs import load_ui_preference

POLL_INTERVAL_S = 0.5
CPU_HIGH_THRESHOLD = 75.0
CPU_LOW_THRESHOLD = 45.0
CPU_HIGH_HOLD_S = 1.0
CPU_LOW_HOLD_S = 5.0
STEP_DOWN = 2
STEP_UP = 1


def governor_enabled_by_env() -> bool:
    return os.environ.get("MPE_POLY_GOVERNOR", "1").strip().lower() not in ("0", "false", "no", "off")


def governor_enabled_by_pref() -> bool:
    return load_ui_preference("poly_governor_enabled", default=True)


def governor_active() -> bool:
    return governor_enabled_by_env() and governor_enabled_by_pref()


class SurgePolyGovernor:
    """Background CPU-aware poly limit adjuster (Surge softkill, not MIDI note-offs)."""

    def __init__(
        self,
        osc_client,
        surge_monitor: SurgeMonitor | None = None,
        cpu_monitor: SurgeCpuMonitor | None = None,
        *,
        osc_host: str = "127.0.0.1",
        osc_out_port: int = 53270,
        poll_interval: float = POLL_INTERVAL_S,
    ) -> None:
        self.osc_client = osc_client
        self.surge_monitor = surge_monitor or SurgeMonitor()
        self.cpu_monitor = cpu_monitor
        self.osc_host = osc_host
        self.osc_out_port = osc_out_port
        self.poll_interval = poll_interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._effective_poly: int | None = None
        self._ceiling_poly: int | None = None
        self._floor_poly = poly_floor()
        self._high_since: float | None = None
        self._low_since: float | None = None
        self._state_mtime = 0.0
        self._pref_check_counter = 0
        self._enabled = governor_active()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._worker,
            daemon=True,
            name="SurgePolyGovernor",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)

    def snapshot(self) -> dict:
        return {
            "enabled": self._enabled,
            "effective_poly": self._effective_poly,
            "ceiling_poly": self._ceiling_poly,
            "floor_poly": self._floor_poly,
        }

    def _refresh_patch_state(self) -> None:
        try:
            stat = POLY_STATE_FILE.stat()
        except OSError:
            return
        if stat.st_mtime <= self._state_mtime:
            return
        self._state_mtime = stat.st_mtime
        data = read_poly_state()
        ceiling = data.get("ceiling_poly")
        effective = data.get("effective_poly")
        if isinstance(ceiling, (int, float)):
            self._ceiling_poly = clamp_poly_limit(int(ceiling))
        if isinstance(effective, (int, float)):
            self._effective_poly = clamp_poly_limit(int(effective))

    def _apply_limit(self, new_limit: int) -> None:
        new_limit = clamp_poly_limit(new_limit, minimum=self._floor_poly)
        if self._ceiling_poly is not None:
            new_limit = min(new_limit, self._ceiling_poly)
        if self._effective_poly == new_limit:
            return
        if send_polylimit(self.osc_client, new_limit):
            self._effective_poly = new_limit

    def _cpu_percent(self) -> float | None:
        if self.cpu_monitor is not None:
            snap = self.cpu_monitor.snapshot()
            if snap.get("online") and isinstance(snap.get("percent"), (int, float)):
                return float(snap["percent"])
        healthy, _ = self.surge_monitor.check_health()
        if not healthy:
            return None
        pid = self.surge_monitor.surge_pid
        if pid is None:
            return None
        # Lightweight fallback when no shared cpu_monitor.
        try:
            with open(f"/proc/{pid}/stat", "rb") as handle:
                stat = handle.read().decode(errors="ignore").split()
            if len(stat) < 15:
                return None
            jiffies = int(stat[13]) + int(stat[14])
        except OSError:
            return None
        now = time.monotonic()
        prev = getattr(self, "_proc_prev", None)
        self._proc_prev = (jiffies, now)
        if prev is None:
            return None
        prev_jiffies, prev_time = prev
        delta_t = now - prev_time
        if delta_t <= 0.05:
            return None
        try:
            clk = os.sysconf("SC_CLK_TCK")
        except (AttributeError, OSError, ValueError):
            clk = 100
        return max(0.0, min(100.0, ((jiffies - prev_jiffies) / clk / delta_t) * 100.0))

    def _worker(self) -> None:
        while not self._stop.wait(self.poll_interval):
            try:
                self._tick()
            except Exception as exc:
                print(f"Surge poly governor tick error: {exc}")

    def _tick(self) -> None:
        self._pref_check_counter += 1
        if self._pref_check_counter % 4 == 0:
            self._enabled = governor_active()

        self._refresh_patch_state()
        if not self._enabled:
            self._high_since = None
            self._low_since = None
            return

        if self._effective_poly is None or self._ceiling_poly is None:
            return

        healthy, _ = self.surge_monitor.check_health()
        if not healthy:
            return

        cpu = self._cpu_percent()
        if cpu is None:
            return

        now = time.monotonic()
        if cpu >= CPU_HIGH_THRESHOLD:
            self._low_since = None
            if self._high_since is None:
                self._high_since = now
            elif now - self._high_since >= CPU_HIGH_HOLD_S:
                if self._effective_poly > self._floor_poly:
                    self._apply_limit(self._effective_poly - STEP_DOWN)
                self._high_since = now
        elif cpu <= CPU_LOW_THRESHOLD:
            self._high_since = None
            if self._low_since is None:
                self._low_since = now
            elif now - self._low_since >= CPU_LOW_HOLD_S:
                if self._effective_poly < self._ceiling_poly:
                    self._apply_limit(self._effective_poly + STEP_UP)
                self._low_since = now
        else:
            self._high_since = None
            self._low_since = None
