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
from patch_browser.governor_load import LoadSample, LoadTracker
from patch_browser.governor_v2 import (
    adaptive_poll_interval,
    always_on_target_limit,
    continuous_target_limit,
    rate_limited_target,
    rise_bias,
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
DEFAULT_GOVERNOR_V2 = False
DEFAULT_LIMIT_SOFT_START = 68.0
DEFAULT_LIMIT_HARD = 86.0
DEFAULT_RISE_FULL_RATE = 65.0
DEFAULT_RISE_BIAS_MAX = 8.0
DEFAULT_RISE_MIN_RATE = 20.0
DEFAULT_RAMP_APPLY = True
DEFAULT_LIMIT_MAX_STEP_DOWN = 1
DEFAULT_LIMIT_STEP_INTERVAL_S = 0.25
DEFAULT_LIMIT_RECOVER_HOLD_S = 5.0
DEFAULT_POLL_FAST_S = 0.05
DEFAULT_POLL_SLOW_S = 0.15
DEFAULT_XRUN_NUDGE = 8.0
DEFAULT_MIN_HEADROOM = 3
DEFAULT_JACK_BASELINE = -1.0
DEFAULT_LIMIT_MODE = "always_on"
VERBOSE_TRACE_FILE = "poly-governor.trace"


def limit_mode_name() -> str:
    raw = os.environ.get("MPE_POLY_LIMIT_MODE", DEFAULT_LIMIT_MODE).strip().lower()
    if raw == "legacy":
        return "legacy"
    if raw in ("progressive", "threshold"):
        return "progressive"
    return "always_on"


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


def _env_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key, "1" if default else "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def governor_v2_enabled() -> bool:
    return _env_bool("MPE_POLY_GOVERNOR_V2", DEFAULT_GOVERNOR_V2)


def limit_mode_legacy() -> bool:
    return limit_mode_name() == "legacy"


def limit_mode_always_on() -> bool:
    return limit_mode_name() == "always_on"


def governor_v2_active() -> bool:
    return governor_v2_enabled() and not limit_mode_legacy()


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
    governor_v2: bool
    limit_soft_start: float
    limit_hard: float
    rise_enable: bool
    rise_full_rate: float
    rise_bias_max: float
    rise_min_rate: float
    limit_max_step_down: int
    limit_step_interval_s: float
    limit_recover_hold_s: float
    poll_fast_s: float
    poll_slow_s: float
    xrun_nudge: float
    min_headroom: int
    jack_baseline: float
    emergency_xrun_only: bool
    limit_mode: str
    ramp_apply: bool


def load_governor_config() -> GovernorConfig:
    poll_slow = _env_float("MPE_POLY_POLL_SLOW_S", DEFAULT_POLL_SLOW_S)
    return GovernorConfig(
        poll_interval_s=_env_float("MPE_POLY_POLL_INTERVAL_S", poll_slow),
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
        governor_v2=governor_v2_enabled(),
        limit_soft_start=_env_float("MPE_POLY_LIMIT_SOFT_START", DEFAULT_LIMIT_SOFT_START),
        limit_hard=_env_float("MPE_POLY_LIMIT_HARD", DEFAULT_LIMIT_HARD),
        rise_enable=_env_bool("MPE_POLY_RISE_ENABLE", True),
        rise_full_rate=_env_float("MPE_POLY_RISE_FULL_RATE", DEFAULT_RISE_FULL_RATE),
        rise_bias_max=_env_float("MPE_POLY_RISE_BIAS_MAX", DEFAULT_RISE_BIAS_MAX),
        rise_min_rate=_env_float("MPE_POLY_RISE_MIN_RATE", DEFAULT_RISE_MIN_RATE),
        limit_max_step_down=_env_int(
            "MPE_POLY_LIMIT_MAX_STEP_DOWN", DEFAULT_LIMIT_MAX_STEP_DOWN
        ),
        limit_step_interval_s=_env_float(
            "MPE_POLY_LIMIT_STEP_INTERVAL_S", DEFAULT_LIMIT_STEP_INTERVAL_S
        ),
        limit_recover_hold_s=_env_float(
            "MPE_POLY_LIMIT_RECOVER_HOLD_S", DEFAULT_LIMIT_RECOVER_HOLD_S
        ),
        poll_fast_s=_env_float("MPE_POLY_POLL_FAST_S", DEFAULT_POLL_FAST_S),
        poll_slow_s=poll_slow,
        xrun_nudge=_env_float("MPE_POLY_XRUN_NUDGE", DEFAULT_XRUN_NUDGE),
        min_headroom=_env_int("MPE_POLY_MIN_HEADROOM", DEFAULT_MIN_HEADROOM),
        jack_baseline=_env_float("MPE_POLY_JACK_BASELINE", DEFAULT_JACK_BASELINE),
        emergency_xrun_only=_env_bool("MPE_POLY_EMERGENCY_XRUN_ONLY", True),
        limit_mode=limit_mode_name(),
        ramp_apply=_env_bool("MPE_POLY_RAMP_APPLY", DEFAULT_RAMP_APPLY),
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
            f"emergency_poly={poly_emergency()} "
            f"v2={int(governor_v2_active())} "
            f"mode={config.limit_mode} "
            f"soft={config.limit_soft_start} "
            f"hard={config.limit_hard} "
            f"headroom={config.min_headroom} "
            f"jack_base={config.jack_baseline} "
            f"rise={int(config.rise_enable)} "
            f"rise_full={config.rise_full_rate} "
            f"rise_min={config.rise_min_rate} "
            f"ramp_apply={int(config.ramp_apply)}",
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
        self._load_tracker = LoadTracker(
            cpu_monitor=self.cpu_monitor,
            surge_monitor=self.surge_monitor,
        )
        self._last_step_down_at: float | None = None
        self._adaptive_poll_interval = self.config.poll_interval_s
        self._last_load: float | None = None
        self._last_dload_dt: float | None = None
        self._last_normalized_stress: float | None = None

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

    def _load_rising(self, sample: LoadSample) -> bool:
        """True when stress is climbing — ramp apply sends OSC without fade deferral."""
        if not self.config.ramp_apply:
            return False
        dload = sample.dload_dt
        if dload is not None and dload > self.config.rise_min_rate:
            return True
        prev = self._last_normalized_stress
        if prev is not None and sample.normalized_load > prev + 0.25:
            return True
        return False

    def _resolve_applicable_limit(
        self,
        new_limit: int,
        *,
        reason: Reason,
        old_limit: int | None,
        ramp_apply: bool = False,
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

        if ramp_apply and reason in ("high", "warm", "spike"):
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
        ramp_apply: bool = False,
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
            ramp_apply=ramp_apply,
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
        while not self._stop.is_set():
            interval = (
                self._adaptive_poll_interval
                if governor_v2_active()
                else self.poll_interval
            )
            if self._stop.wait(interval):
                break
            try:
                self._tick()
            except Exception as exc:
                self._journal.log_error(str(exc))

    def _tick(self) -> None:
        if governor_v2_active():
            if limit_mode_always_on():
                self._tick_v2_always_on()
            else:
                self._tick_v2_progressive()
        else:
            self._tick_legacy()

    def _tick_legacy(self) -> None:
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

    def _tick_v2_always_on(self) -> None:
        """Jack deadline meter; continuous overhead; loosen via baseline not proc fallback."""
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

        sample = self._load_tracker.sample()
        if sample is None:
            return

        cfg = self.config
        stress = sample.normalized_load
        self._last_load = sample.load
        self._last_dload_dt = sample.dload_dt
        ramp_apply = self._load_rising(sample)
        self._adaptive_poll_interval = adaptive_poll_interval(
            load=stress,
            dload_dt=sample.dload_dt,
            soft_start=25.0,
            fast_s=cfg.poll_fast_s,
            slow_s=cfg.poll_slow_s,
        )

        bias = rise_bias(
            sample.dload_dt or 0.0,
            full_rate=cfg.rise_full_rate,
            max_bias=cfg.rise_bias_max,
            min_rate=cfg.rise_min_rate,
            enabled=cfg.rise_enable,
        )
        effective_stress = stress + bias
        if sample.xrun_delta > 0:
            effective_stress += cfg.xrun_nudge

        cpu = sample.load
        raw_cpu = sample.raw_load
        now = time.monotonic()

        self._try_apply_pending_limit(cpu=cpu, raw_cpu=raw_cpu)

        ceiling = self._ceiling_poly
        floor = self._floor_poly
        if ceiling is None or self._effective_poly is None:
            return

        if sample.xrun_delta > 0 and cfg.emergency_xrun_only:
            emergency = poly_emergency()
            if self._effective_poly > emergency:
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
            not cfg.emergency_xrun_only
            and effective_stress >= cfg.cpu_emergency_threshold
        ):
            emergency = poly_emergency()
            if self._effective_poly > emergency:
                self._apply_limit(
                    emergency,
                    reason="emergency",
                    cpu=cpu,
                    raw_cpu=raw_cpu,
                    held_s=0.0,
                    minimum=emergency,
                )
            return

        desired = always_on_target_limit(
            effective_stress,
            ceiling=ceiling,
            floor=floor,
            min_headroom=cfg.min_headroom,
            hard=cfg.limit_hard,
        )

        recover_below = max(8.0, cfg.min_headroom * 3.0)
        if effective_stress <= recover_below:
            self._high_since = None
            if self._low_since is None:
                self._low_since = now
            elif (
                now - self._low_since >= cfg.limit_recover_hold_s
                and self._effective_poly < ceiling
            ):
                self._apply_limit(
                    min(ceiling, self._effective_poly + cfg.step_up),
                    reason="recover",
                    cpu=cpu,
                    raw_cpu=raw_cpu,
                    held_s=now - self._low_since,
                )
                self._low_since = now
        else:
            self._low_since = None
            max_step = cfg.limit_max_step_down
            if sample.xrun_delta > 0:
                max_step = min(ceiling - floor, max_step + 1)
            self._apply_v2_step(
                desired,
                reason="high",
                cpu=cpu,
                raw_cpu=raw_cpu,
                now=now,
                max_step_down=max_step,
                ramp_apply=ramp_apply,
            )

        self._last_normalized_stress = stress

    def _tick_v2_progressive(self) -> None:
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

        sample = self._load_tracker.sample()
        if sample is None:
            return

        cfg = self.config
        self._last_load = sample.load
        self._last_dload_dt = sample.dload_dt
        ramp_apply = self._load_rising(sample)
        self._adaptive_poll_interval = adaptive_poll_interval(
            load=sample.load,
            dload_dt=sample.dload_dt,
            soft_start=cfg.limit_soft_start,
            fast_s=cfg.poll_fast_s,
            slow_s=cfg.poll_slow_s,
        )

        bias = rise_bias(
            sample.dload_dt or 0.0,
            full_rate=cfg.rise_full_rate,
            max_bias=cfg.rise_bias_max,
            min_rate=cfg.rise_min_rate,
            enabled=cfg.rise_enable,
        )
        effective_load = sample.load + bias
        if sample.xrun_delta > 0:
            effective_load += cfg.xrun_nudge

        cpu = sample.load
        raw_cpu = sample.raw_load
        now = time.monotonic()

        self._try_apply_pending_limit(cpu=cpu, raw_cpu=raw_cpu)

        if effective_load >= cfg.cpu_emergency_threshold:
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

        ceiling = self._ceiling_poly
        floor = self._floor_poly
        if ceiling is None or self._effective_poly is None:
            return

        if (
            self._patch_changed_at is not None
            and not self._warm_preempt_done
            and now - self._patch_changed_at <= cfg.patch_warm_window_s
            and effective_load > cfg.limit_soft_start
            and self._effective_poly > floor
        ):
            self._warm_preempt_done = True
            desired = continuous_target_limit(
                effective_load,
                ceiling=ceiling,
                floor=floor,
                soft_start=cfg.limit_soft_start,
                hard=cfg.limit_hard,
            )
            self._apply_v2_step(
                desired,
                reason="warm",
                cpu=cpu,
                raw_cpu=raw_cpu,
                now=now,
                ramp_apply=ramp_apply,
            )
            self._last_normalized_stress = sample.normalized_load
            return

        desired = continuous_target_limit(
            effective_load,
            ceiling=ceiling,
            floor=floor,
            soft_start=cfg.limit_soft_start,
            hard=cfg.limit_hard,
        )

        if sample.load <= cfg.limit_soft_start:
            self._high_since = None
            if self._low_since is None:
                self._low_since = now
            elif (
                now - self._low_since >= cfg.limit_recover_hold_s
                and self._effective_poly < ceiling
            ):
                self._apply_limit(
                    self._effective_poly + cfg.step_up,
                    reason="recover",
                    cpu=cpu,
                    raw_cpu=raw_cpu,
                    held_s=now - self._low_since,
                )
                self._low_since = now
        else:
            self._low_since = None
            self._apply_v2_step(
                desired,
                reason="high",
                cpu=cpu,
                raw_cpu=raw_cpu,
                now=now,
                ramp_apply=ramp_apply,
            )
        self._last_normalized_stress = sample.normalized_load

    def _apply_v2_step(
        self,
        desired: int,
        *,
        reason: Reason,
        cpu: float,
        raw_cpu: float | None,
        now: float,
        max_step_down: int | None = None,
        ramp_apply: bool = False,
    ) -> None:
        if self._effective_poly is None:
            return
        current = self._effective_poly
        if desired >= current:
            return

        step_cap = (
            self.config.limit_max_step_down
            if max_step_down is None
            else max_step_down
        )
        next_limit = rate_limited_target(
            current,
            desired,
            last_step_down_at=self._last_step_down_at,
            now=now,
            step_interval_s=self.config.limit_step_interval_s,
            max_step_down=step_cap,
        )
        if next_limit is None or next_limit >= current:
            return
        self._apply_limit(
            next_limit,
            reason=reason,
            cpu=cpu,
            raw_cpu=raw_cpu,
            held_s=0.0,
            ramp_apply=ramp_apply,
        )
        self._last_step_down_at = now
        if self._high_since is None:
            self._high_since = now
