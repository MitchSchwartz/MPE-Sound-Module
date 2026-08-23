"""Dynamic poly limit governor — lowers Surge voice cap when CPU is high."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Literal

from patch_browser.mpe_run_dir import run_dir
from patch_browser.surge_cpu_monitor import SurgeCpuMonitor
from patch_browser.surge_monitor import SurgeMonitor
from patch_browser.surge_playback import (
    POLY_STATE_FILE,
    clamp_poly_limit,
    poly_emergency,
    poly_floor,
    read_poly_state,
    send_polylimit,
)
from patch_browser.poly_voice_tracker import (
    fade_actuation_enabled,
    read_active_voice_count,
    write_fade_request,
)
from patch_browser.ui_prefs import load_ui_preference

Reason = Literal["high", "spike", "emergency", "warm", "recover"]

DEFAULT_POLL_INTERVAL_S = 0.15
DEFAULT_CPU_EMERGENCY_THRESHOLD = 90.0
DEFAULT_CPU_SPIKE_THRESHOLD = 78.0
DEFAULT_CPU_HIGH_THRESHOLD = 50.0
DEFAULT_CPU_WARM_THRESHOLD = 48.0
DEFAULT_CPU_LOW_THRESHOLD = 40.0
DEFAULT_CPU_HIGH_HOLD_S = 0.15
DEFAULT_CPU_LOW_HOLD_S = 5.0
DEFAULT_PATCH_WARM_WINDOW_S = 4.0
DEFAULT_STEP_DOWN = 2
DEFAULT_STEP_DOWN_SPIKE = 4
DEFAULT_STEP_DOWN_WARM = 2
DEFAULT_STEP_UP = 1
VERBOSE_TRACE_FILE = "poly-governor.trace"


def spam_threshold_per_s(poll_interval_s: float) -> int:
    """Must sit below max emits/sec (1/poll_interval) to engage at shipped cadence."""
    if poll_interval_s <= 0:
        return 2
    return max(2, int(1.0 / poll_interval_s) - 1)


def _env_float(key: str, default: float) -> float:
    raw = os.environ.get(key, str(default)).strip()
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key, str(default)).strip()
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class GovernorConfig:
    poll_interval_s: float
    cpu_emergency_threshold: float
    cpu_spike_threshold: float
    cpu_high_threshold: float
    cpu_warm_threshold: float
    cpu_low_threshold: float
    cpu_high_hold_s: float
    cpu_low_hold_s: float
    patch_warm_window_s: float
    step_down: int
    step_down_spike: int
    step_down_warm: int
    step_up: int


def load_governor_config() -> GovernorConfig:
    return GovernorConfig(
        poll_interval_s=_env_float("MPE_POLY_POLL_INTERVAL_S", DEFAULT_POLL_INTERVAL_S),
        cpu_emergency_threshold=_env_float(
            "MPE_POLY_CPU_EMERGENCY", DEFAULT_CPU_EMERGENCY_THRESHOLD
        ),
        cpu_spike_threshold=_env_float("MPE_POLY_CPU_SPIKE", DEFAULT_CPU_SPIKE_THRESHOLD),
        cpu_high_threshold=_env_float("MPE_POLY_CPU_HIGH", DEFAULT_CPU_HIGH_THRESHOLD),
        cpu_warm_threshold=_env_float("MPE_POLY_CPU_WARM", DEFAULT_CPU_WARM_THRESHOLD),
        cpu_low_threshold=_env_float("MPE_POLY_CPU_LOW", DEFAULT_CPU_LOW_THRESHOLD),
        cpu_high_hold_s=_env_float("MPE_POLY_CPU_HIGH_HOLD_S", DEFAULT_CPU_HIGH_HOLD_S),
        cpu_low_hold_s=_env_float("MPE_POLY_CPU_LOW_HOLD_S", DEFAULT_CPU_LOW_HOLD_S),
        patch_warm_window_s=_env_float(
            "MPE_POLY_PATCH_WARM_WINDOW_S", DEFAULT_PATCH_WARM_WINDOW_S
        ),
        step_down=_env_int("MPE_POLY_STEP_DOWN", DEFAULT_STEP_DOWN),
        step_down_spike=_env_int("MPE_POLY_STEP_DOWN_SPIKE", DEFAULT_STEP_DOWN_SPIKE),
        step_down_warm=_env_int("MPE_POLY_STEP_DOWN_WARM", DEFAULT_STEP_DOWN_WARM),
        step_up=_env_int("MPE_POLY_STEP_UP", DEFAULT_STEP_UP),
    )


def governor_enabled_by_env() -> bool:
    return os.environ.get("MPE_POLY_GOVERNOR", "1").strip().lower() not in ("0", "false", "no", "off")


def governor_enabled_by_pref() -> bool:
    return load_ui_preference("poly_governor_enabled", default=True)


def governor_active() -> bool:
    return governor_enabled_by_env() and governor_enabled_by_pref()


def verbose_trace_enabled() -> bool:
    return os.environ.get("MPE_POLY_GOVERNOR_VERBOSE", "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


class PolyGovernorJournal:
    """State-change journal logging with spam guard (never per-tick)."""

    def __init__(self, poll_interval_s: float = DEFAULT_POLL_INTERVAL_S) -> None:
        self._spam_threshold = spam_threshold_per_s(poll_interval_s)
        self._window_start = time.monotonic()
        self._window_count = 0
        self._suppressed = 0

    def log_startup(self, *, enabled: bool, config: GovernorConfig, floor_poly: int) -> None:
        print(
            "poly-governor: startup "
            f"enabled={int(enabled)} "
            f"floor={floor_poly} "
            f"poll={config.poll_interval_s} "
            f"emergency={config.cpu_emergency_threshold} "
            f"spike={config.cpu_spike_threshold} "
            f"high={config.cpu_high_threshold} "
            f"warm={config.cpu_warm_threshold} "
            f"low={config.cpu_low_threshold} "
            f"high_hold={config.cpu_high_hold_s} "
            f"low_hold={config.cpu_low_hold_s} "
            f"step_down={config.step_down} "
            f"step_down_spike={config.step_down_spike} "
            f"step_down_warm={config.step_down_warm} "
            f"step_up={config.step_up} "
            f"warm_window={config.patch_warm_window_s} "
            f"fade={int(fade_actuation_enabled())} "
            f"emergency_poly={poly_emergency()}",
            flush=True,
        )

    def log_transition(
        self,
        *,
        old_limit: int,
        new_limit: int,
        reason: Reason,
        cpu: float,
        raw_cpu: float | None,
        patch: str | None,
        held_s: float,
    ) -> None:
        patch_name = patch if patch else ""
        raw_part = f" raw={raw_cpu:.1f}" if raw_cpu is not None else ""
        line = (
            f"poly-governor: {old_limit} -> {new_limit} "
            f"reason={reason} cpu={cpu:.1f}{raw_part} "
            f'patch="{patch_name}" held={held_s:.2f}s'
        )
        self._emit(line)

    def log_error(self, message: str) -> None:
        self._emit(f"poly-governor: tick error {message}")

    def log_send_failed(
        self,
        *,
        old_limit: int,
        new_limit: int,
        reason: Reason,
    ) -> None:
        self._emit(
            f"poly-governor: send failed {old_limit} -> {new_limit} reason={reason}"
        )

    def log_enabled_change(self, *, was: bool, now: bool) -> None:
        self._emit(f"poly-governor: enabled {int(was)} -> {int(now)}")

    def flush_pending(self) -> None:
        self._flush_suppressed()

    def _emit(self, line: str) -> None:
        append_verbose_trace(line)
        now = time.monotonic()
        if now - self._window_start >= 1.0:
            self._flush_suppressed()
            self._window_start = now
            self._window_count = 0

        if self._window_count >= self._spam_threshold:
            self._suppressed += 1
            return

        self._window_count += 1
        print(line, flush=True)

    def _flush_suppressed(self) -> None:
        if self._suppressed < 1:
            return
        line = (
            f"poly-governor: log-spam summary suppressed={self._suppressed} "
            "events in 1.0s"
        )
        print(line, flush=True)
        append_verbose_trace(line)
        self._suppressed = 0


def append_verbose_trace(line: str) -> None:
    """Optional high-rate trace on tmpfs — never journal (MPE_POLY_GOVERNOR_VERBOSE=1)."""
    if not verbose_trace_enabled():
        return
    path = run_dir() / VERBOSE_TRACE_FILE
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            if not line.endswith("\n"):
                handle.write("\n")
    except OSError:
        pass


class SurgePolyGovernor:
    """Background CPU-aware poly limit adjuster.

    Surge voice stealing on limit drop is engine behaviour — see
    docs/measurements/poly-governor-instrumentation-2026-08-21.md (Task C).

    With ``MPE_POLY_GOVERNOR_FADE=1`` (default), step-down requests defer until
    the MIDI voice tracker reports fewer sounding notes than the target limit,
    avoiding note-on-triggered ``uber_release`` steals. Emergency still requests
    proactive MIDI note-offs via ``governor-fade-request.json``.
    """

    def __init__(
        self,
        osc_client,
        surge_monitor: SurgeMonitor | None = None,
        cpu_monitor: SurgeCpuMonitor | None = None,
        *,
        osc_host: str = "127.0.0.1",
        osc_out_port: int = 53270,
        poll_interval: float | None = None,
        config: GovernorConfig | None = None,
        journal: PolyGovernorJournal | None = None,
    ) -> None:
        self.osc_client = osc_client
        self.surge_monitor = surge_monitor or SurgeMonitor()
        self.cpu_monitor = cpu_monitor
        self.osc_host = osc_host
        self.osc_out_port = osc_out_port
        self.config = config or load_governor_config()
        self.poll_interval = (
            self.config.poll_interval_s if poll_interval is None else poll_interval
        )
        self._journal = journal or PolyGovernorJournal(self.config.poll_interval_s)
        self._last_send_fail: tuple[int, int, Reason] | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._effective_poly: int | None = None
        self._ceiling_poly: int | None = None
        self._floor_poly = poly_floor()
        self._high_since: float | None = None
        self._low_since: float | None = None
        self._state_mtime = 0.0
        self._last_patch: str | None = None
        self._patch_changed_at: float | None = None
        self._warm_preempt_done = False
        self._pref_check_counter = 0
        self._enabled = governor_active()
        self._startup_logged = False
        self._pending_limit: int | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        if not self._startup_logged:
            self._journal.log_startup(
                enabled=self._enabled,
                config=self.config,
                floor_poly=self._floor_poly,
            )
            self._startup_logged = True
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
        self._journal.flush_pending()

    def snapshot(self) -> dict:
        return {
            "enabled": self._enabled,
            "effective_poly": self._effective_poly,
            "ceiling_poly": self._ceiling_poly,
            "floor_poly": self._floor_poly,
        }

    def _limits_ready(self) -> bool:
        return (
            isinstance(self._effective_poly, int)
            and isinstance(self._ceiling_poly, int)
        )

    def _refresh_patch_state(self) -> None:
        try:
            stat = POLY_STATE_FILE.stat()
        except OSError:
            return
        if stat.st_mtime <= self._state_mtime:
            return
        self._state_mtime = stat.st_mtime
        data = read_poly_state()
        patch = data.get("patch")
        if isinstance(patch, str) and patch != self._last_patch:
            self._last_patch = patch
            self._high_since = None
            self._low_since = None
            self._patch_changed_at = time.monotonic()
            self._warm_preempt_done = False
        ceiling = data.get("ceiling_poly")
        effective = data.get("effective_poly")
        if isinstance(ceiling, (int, float)):
            self._ceiling_poly = clamp_poly_limit(int(ceiling))
        if isinstance(effective, (int, float)):
            self._effective_poly = clamp_poly_limit(int(effective))

    def _resolve_applicable_limit(
        self,
        new_limit: int,
        *,
        reason: Reason,
        old_limit: int | None,
    ) -> int | None:
        """Return OSC limit to apply, or None to defer step-down under fade policy."""
        if not fade_actuation_enabled():
            return new_limit
        if old_limit is not None and new_limit >= old_limit:
            self._pending_limit = None
            return new_limit

        active = read_active_voice_count()
        if active <= new_limit:
            self._pending_limit = None
            return new_limit

        if reason == "emergency":
            write_fade_request(release_count=active - new_limit, reason=reason)
            self._pending_limit = None
            return new_limit

        self._pending_limit = new_limit
        return None

    def _try_apply_pending_limit(
        self,
        *,
        cpu: float,
        raw_cpu: float | None,
    ) -> None:
        if self._pending_limit is None or self._effective_poly is None:
            return
        if read_active_voice_count() > self._pending_limit:
            return
        self._apply_limit(
            self._pending_limit,
            reason="high",
            cpu=cpu,
            raw_cpu=raw_cpu,
            held_s=0.0,
        )
        self._pending_limit = None

    def _apply_limit(
        self,
        new_limit: int,
        *,
        reason: Reason,
        cpu: float,
        raw_cpu: float | None,
        held_s: float,
        minimum: int | None = None,
    ) -> None:
        floor = self._floor_poly if minimum is None else minimum
        new_limit = clamp_poly_limit(new_limit, minimum=floor)
        if self._ceiling_poly is not None:
            new_limit = min(new_limit, self._ceiling_poly)
        old_limit = self._effective_poly
        if old_limit is None or old_limit == new_limit:
            return
        applicable = self._resolve_applicable_limit(
            new_limit,
            reason=reason,
            old_limit=old_limit,
        )
        if applicable is None:
            return
        new_limit = applicable
        if old_limit == new_limit:
            return
        fail_key = (old_limit, new_limit, reason)
        if send_polylimit(self.osc_client, new_limit):
            self._effective_poly = new_limit
            self._last_send_fail = None
            self._journal.log_transition(
                old_limit=old_limit,
                new_limit=new_limit,
                reason=reason,
                cpu=cpu,
                raw_cpu=raw_cpu,
                patch=self._last_patch,
                held_s=held_s,
            )
        elif self._last_send_fail != fail_key:
            self._last_send_fail = fail_key
            self._journal.log_send_failed(
                old_limit=old_limit,
                new_limit=new_limit,
                reason=reason,
            )

    def _cpu_sample(self) -> tuple[float | None, float | None]:
        if self.cpu_monitor is not None:
            snap = self.cpu_monitor.snapshot()
            if snap.get("online"):
                raw = snap.get("raw_percent")
                smoothed = snap.get("percent")
                raw_f = float(raw) if isinstance(raw, (int, float)) else None
                if raw_f is not None:
                    # Prefer raw proc/OSC sample — smoothed meter lags on rising load.
                    return raw_f, raw_f
                if isinstance(smoothed, (int, float)):
                    return float(smoothed), raw_f
        healthy, _ = self.surge_monitor.check_health()
        if not healthy:
            return None, None
        pid = self.surge_monitor.surge_pid
        if pid is None:
            return None, None
        try:
            with open(f"/proc/{pid}/stat", "rb") as handle:
                stat = handle.read().decode(errors="ignore").split()
            if len(stat) < 15:
                return None, None
            jiffies = int(stat[13]) + int(stat[14])
        except OSError:
            return None, None
        now = time.monotonic()
        prev = getattr(self, "_proc_prev", None)
        self._proc_prev = (jiffies, now)
        if prev is None:
            return None, None
        prev_jiffies, prev_time = prev
        delta_t = now - prev_time
        if delta_t <= 0.05:
            return None, None
        try:
            clk = os.sysconf("SC_CLK_TCK")
        except (AttributeError, OSError, ValueError):
            clk = 100
        sample = max(0.0, min(100.0, ((jiffies - prev_jiffies) / clk / delta_t) * 100.0))
        return sample, sample

    def _worker(self) -> None:
        while not self._stop.wait(self.poll_interval):
            try:
                self._tick()
            except Exception as exc:
                self._journal.log_error(str(exc))

    def _tick(self) -> None:
        self._pref_check_counter += 1
        if self._pref_check_counter % 4 == 0:
            enabled = governor_active()
            if enabled != self._enabled:
                self._journal.log_enabled_change(was=self._enabled, now=enabled)
            self._enabled = enabled

        self._refresh_patch_state()
        if not self._enabled:
            self._high_since = None
            self._low_since = None
            return

        if not self._limits_ready():
            return

        healthy, _ = self.surge_monitor.check_health()
        if not healthy:
            return

        cpu, raw_cpu = self._cpu_sample()
        if cpu is None:
            return

        self._try_apply_pending_limit(cpu=cpu, raw_cpu=raw_cpu)

        cfg = self.config
        now = time.monotonic()
        if cpu >= cfg.cpu_emergency_threshold:
            self._low_since = None
            self._high_since = now
            emergency = poly_emergency()
            if self._effective_poly is not None and self._effective_poly > emergency:
                self._apply_limit(
                    emergency,
                    reason="emergency",
                    cpu=cpu,
                    raw_cpu=raw_cpu,
                    held_s=0.0,
                    minimum=emergency,
                )
            return

        if (
            self._patch_changed_at is not None
            and not self._warm_preempt_done
            and now - self._patch_changed_at <= cfg.patch_warm_window_s
            and cpu >= cfg.cpu_warm_threshold
            and self._effective_poly > self._floor_poly
        ):
            self._warm_preempt_done = True
            self._high_since = now
            self._low_since = None
            self._apply_limit(
                self._effective_poly - cfg.step_down_warm,
                reason="warm",
                cpu=cpu,
                raw_cpu=raw_cpu,
                held_s=0.0,
            )
            return

        if cpu >= cfg.cpu_spike_threshold:
            self._low_since = None
            if self._high_since is None:
                self._high_since = now
                if self._effective_poly > self._floor_poly:
                    self._apply_limit(
                        self._effective_poly - cfg.step_down_spike,
                        reason="spike",
                        cpu=cpu,
                        raw_cpu=raw_cpu,
                        held_s=0.0,
                    )
            elif now - self._high_since >= cfg.cpu_high_hold_s:
                if self._effective_poly > self._floor_poly:
                    self._apply_limit(
                        self._effective_poly - cfg.step_down,
                        reason="high",
                        cpu=cpu,
                        raw_cpu=raw_cpu,
                        held_s=now - self._high_since,
                    )
                self._high_since = now
        elif cpu >= cfg.cpu_high_threshold:
            self._low_since = None
            if self._high_since is None:
                self._high_since = now
            elif now - self._high_since >= cfg.cpu_high_hold_s:
                if self._effective_poly > self._floor_poly:
                    self._apply_limit(
                        self._effective_poly - cfg.step_down,
                        reason="high",
                        cpu=cpu,
                        raw_cpu=raw_cpu,
                        held_s=now - self._high_since,
                    )
                self._high_since = now
        elif cpu <= cfg.cpu_low_threshold:
            self._high_since = None
            if self._low_since is None:
                self._low_since = now
            elif now - self._low_since >= cfg.cpu_low_hold_s:
                if self._effective_poly < self._ceiling_poly:
                    self._apply_limit(
                        self._effective_poly + cfg.step_up,
                        reason="recover",
                        cpu=cpu,
                        raw_cpu=raw_cpu,
                        held_s=now - self._low_since,
                    )
                self._low_since = now
        else:
            self._high_since = None
            self._low_since = None
