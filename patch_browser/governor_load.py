"""Governor load sampling — JACK dsp_percent from meter.state with proc fallback."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

from patch_browser.audio_engine import (
    METER_STATE_MAX_AGE_S,
    meter_state_fresh,
    read_meter_state,
)
from patch_browser.mpe_run_dir import run_dir


@dataclass(frozen=True)
class LoadSample:
    load: float
    raw_load: float
    source: str
    dload_dt: float | None
    xruns: int | None
    xrun_delta: int


class LoadTracker:
    """Samples deadline-aligned load; tracks dLoad/dt and xrun deltas."""

    def __init__(
        self,
        *,
        cpu_monitor=None,
        surge_monitor=None,
        meter_mode: str | None = None,
        meter_max_age_s: float = METER_STATE_MAX_AGE_S,
    ) -> None:
        self.cpu_monitor = cpu_monitor
        self.surge_monitor = surge_monitor
        self.meter_mode = (meter_mode or os.environ.get("MPE_POLY_GOVERNOR_METER", "auto")).strip().lower()
        self.meter_max_age_s = meter_max_age_s
        self._prev_load: float | None = None
        self._prev_at: float | None = None
        self._prev_xruns: int | None = None
        self._meter_fallback_logged = False
        self._jack_proc_disagreement_logged = False
        self._proc_prev: tuple[int, float] | None = None

    def sample(self) -> LoadSample | None:
        now = time.monotonic()
        load, raw, source = self._read_load(now)
        if load is None:
            return None

        dload_dt = self._compute_dload_dt(load, now)
        xruns, xrun_delta = self._read_xruns()

        self._prev_load = load
        self._prev_at = now

        return LoadSample(
            load=load,
            raw_load=raw if raw is not None else load,
            source=source,
            dload_dt=dload_dt,
            xruns=xruns,
            xrun_delta=xrun_delta,
        )

    def _compute_dload_dt(self, load: float, now: float) -> float | None:
        if self._prev_load is None or self._prev_at is None:
            return None
        delta_t = now - self._prev_at
        if delta_t < 0.05:
            return None
        return (load - self._prev_load) / delta_t

    def _read_xruns(self) -> tuple[int | None, int]:
        state = read_meter_state(run_dir() / "meter.state")
        if not meter_state_fresh(state, max_age_s=self.meter_max_age_s):
            return None, 0
        raw = state.get("xruns")
        if raw is None:
            return None, 0
        try:
            xruns = int(raw)
        except ValueError:
            return None, 0
        delta = 0
        if self._prev_xruns is not None and xruns > self._prev_xruns:
            delta = xruns - self._prev_xruns
        self._prev_xruns = xruns
        return xruns, delta

    def _read_load(self, now: float) -> tuple[float | None, float | None, str]:
        jack_load: float | None = None
        jack_raw: float | None = None
        if self.meter_mode in ("auto", "jack"):
            jack_load, jack_raw = self._read_meter_dsp(now)
            if self.meter_mode == "jack" and jack_load is None:
                if not self._meter_fallback_logged:
                    print(
                        "poly-governor: meter=jack but dsp_percent stale — falling back to proc",
                        flush=True,
                    )
                    self._meter_fallback_logged = True

        proc_load = self._read_proc_cpu()

        if self.meter_mode == "proc":
            if proc_load is None:
                return None, None, "none"
            return proc_load, proc_load, "proc"

        if self.meter_mode == "jack":
            if jack_load is not None:
                return jack_load, jack_raw, "jack"
            if proc_load is not None:
                return proc_load, proc_load, "proc"
            return None, None, "none"

        # auto — prefer jack unless pegged while proc disagrees (Pi 5 @ 128×2 common)
        if jack_load is not None:
            if jack_load >= 95.0:
                if proc_load is None:
                    proc_load = self._read_proc_cpu()
                if proc_load is not None and proc_load + 15.0 < jack_load:
                    if not self._jack_proc_disagreement_logged:
                        print(
                            "poly-governor: jack dsp pegged "
                            f"({jack_load:.0f}%) vs proc ({proc_load:.0f}%) — using proc",
                            flush=True,
                        )
                        self._jack_proc_disagreement_logged = True
                    return proc_load, proc_load, "proc"
                if proc_load is None:
                    # Never act on pegged jack without a proc witness (first tick).
                    return None, None, "none"
            return jack_load, jack_raw, "jack"

        if proc_load is not None:
            return proc_load, proc_load, "proc"
        return None, None, "none"

    def _read_meter_dsp(self, now: float) -> tuple[float | None, float | None]:
        state = read_meter_state(run_dir() / "meter.state")
        if not meter_state_fresh(state, now=time.time(), max_age_s=self.meter_max_age_s):
            return None, None
        raw = state.get("dsp_percent")
        if raw is None:
            return None, None
        try:
            value = float(raw)
        except ValueError:
            return None, None
        clamped = max(0.0, min(100.0, value))
        return clamped, clamped

    def _read_proc_cpu(self) -> float | None:
        if self.cpu_monitor is not None:
            snap = self.cpu_monitor.snapshot()
            if snap.get("online"):
                raw = snap.get("raw_percent")
                if isinstance(raw, (int, float)):
                    return max(0.0, min(100.0, float(raw)))
                smoothed = snap.get("percent")
                if isinstance(smoothed, (int, float)):
                    return max(0.0, min(100.0, float(smoothed)))

        if self.surge_monitor is None:
            return None
        healthy, _ = self.surge_monitor.check_health()
        if not healthy:
            return None
        pid = self.surge_monitor.surge_pid
        if pid is None:
            return None
        try:
            with open(f"/proc/{pid}/stat", "rb") as handle:
                stat = handle.read().decode(errors="ignore").split()
            if len(stat) < 15:
                return None
            jiffies = int(stat[13]) + int(stat[14])
        except OSError:
            return None
        mono = time.monotonic()
        prev = self._proc_prev
        self._proc_prev = (jiffies, mono)
        if prev is None:
            return None
        prev_jiffies, prev_time = prev
        delta_t = mono - prev_time
        if delta_t <= 0.05:
            return None
        try:
            clk = os.sysconf("SC_CLK_TCK")
        except (AttributeError, OSError, ValueError):
            clk = 100
        return max(0.0, min(100.0, ((jiffies - prev_jiffies) / clk / delta_t) * 100.0))
