"""Session control plane snapshot — schema v1 (spec D6, Phase 1).

Aggregates existing runtime truth under ``/run/mpe/session.snapshot.json``.
Does not own engine state; readers treat stale sub-sources as unknown (never
last-known-good).

Do not run ``python3 -m patch_browser.session_snapshot`` on a systemd
timer — CLI startup is ~360 ms on the Pi vs ~58 ms in-process. The CLI is
for debugging; Phase 3 publisher must call ``build_snapshot()`` in-process.
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from patch_browser.audio_engine import read_engine_state
from patch_browser.mpe_run_dir import run_dir
from patch_browser.sl_hud_state import read_sl_hud_state

SCHEMA_VERSION = 1
STALE_THRESHOLD_S = 1.5
PUBLISH_INTERVAL_S = 0.5
SEQ_STALE_FACTOR = 2
MAINTENANCE_DEFAULT_DEADLINE_S = float(os.environ.get("MPE_MAINTENANCE_DEADLINE_S", "1800"))

SNAPSHOT_FILENAME = "session.snapshot.json"
SEQ_FILENAME = "session.snapshot.seq"

VALID_LOOPER_POLICIES = frozenset({"eval", "adopt", "disabled"})
VALID_MODES = frozenset({"ok", "recovering", "failed", "maintenance"})

JACK_UNIT = "mpe-jackd"
SURGE_UNIT = "surge-xt-cli"
ENGINE_STATE_WRITER_UNIT = "surge-watchdog"

STATUS_SERVICE_UNITS = (
    "mpe-jackd",
    "surge-xt-cli",
    "surge-watchdog",
    "touch-patch-browser",
    "patch-browser",
    "usb-audio-gadget",
    "uac2-stall-watchdog",
    "mpe-pressure-remap",
    "mpe-looper-session",
    "mpe-sooperlooper",
    "sl-watchdog",
)

MPE_ENV_PATH = Path("/etc/mpe/mpe.env")
MPE_ENV_STATUS_KEYS = ("MPE_UI_MODE", "MPE_AUDIO_PROFILE")

# Liveness probe cadence. `active` is a runtime fact and is sampled every build;
# `enabled` is a CONFIGURATION fact that changes only on `systemctl enable/disable`
# or a deploy, so sampling it at publish rate was the original mistake — measured at
# 31-43 ms for 11 units no matter the transport, because systemd walks the enablement
# symlink tree on disk. See docs/measurements/systemd-liveness-cost-2026-08-19.md.
SERVICES_PROBE_TTL_S = float(os.environ.get("MPE_SNAPSHOT_SERVICES_TTL_S", "5"))
ENABLED_PROBE_TTL_S = float(os.environ.get("MPE_SNAPSHOT_ENABLED_TTL_S", "30"))

_services_probe_cache: dict[str, tuple[float, object]] = {}


def _ttl_probe(key: str, probe: Callable[[], object], ttl: float | None = None) -> object:
    """Cache a systemd probe result. Callers must surface the age (see _probe_age_s)."""
    now = time.monotonic()
    limit = SERVICES_PROBE_TTL_S if ttl is None else ttl
    cached = _services_probe_cache.get(key)
    if cached is not None and (now - cached[0]) < limit:
        return cached[1]
    value = probe()
    _services_probe_cache[key] = (now, value)
    return value


def _probe_age_s(key: str) -> float | None:
    """Seconds since the cached probe behind `key` was actually taken.

    A cached judgement is the last-known-good problem wearing a different hat. Every
    cached field in the snapshot must be able to say how old it is, or a reader cannot
    tell a live reading from a memory.
    """
    cached = _services_probe_cache.get(key)
    if cached is None:
        return None
    return round(max(0.0, time.monotonic() - cached[0]), 3)


def _reset_probe_cache() -> None:
    """Test seam — the cache is process-global and must not leak between cases."""
    _services_probe_cache.clear()


# --- batched liveness -------------------------------------------------------------
#
# Measured on the appliance, 11 units: 202 ms as one fork per unit, 46 ms as a single
# batched fork, 7.0 ms over D-Bus. The publisher runs at 1-2 Hz, so the transport is
# the difference between 40% of a core and 1.4%.

_DBUS_MANAGER: Any = None
_DBUS_UNAVAILABLE = False


def _dbus_manager() -> Any:
    global _DBUS_MANAGER, _DBUS_UNAVAILABLE
    if _DBUS_MANAGER is not None or _DBUS_UNAVAILABLE:
        return _DBUS_MANAGER
    try:
        import dbus  # type: ignore[import-not-found]

        bus = dbus.SystemBus()
        _DBUS_MANAGER = dbus.Interface(
            bus.get_object("org.freedesktop.systemd1", "/org/freedesktop/systemd1"),
            "org.freedesktop.systemd1.Manager",
        )
    except Exception:
        _DBUS_UNAVAILABLE = True
        _DBUS_MANAGER = None
    return _DBUS_MANAGER


def _dbus_active_states(units: tuple[str, ...]) -> dict[str, bool | None] | None:
    """ActiveState for every unit in one round trip. None when D-Bus is unusable."""
    mgr = _dbus_manager()
    if mgr is None:
        return None
    try:
        rows = mgr.ListUnitsByNames([f"{u}.service" for u in units])
    except Exception:
        return None
    out: dict[str, bool | None] = {}
    for row in rows:
        name = str(row[0])
        if not name.endswith(".service"):
            continue
        state = str(row[3])
        if state == "active":
            out[name[: -len(".service")]] = True
        elif state in {"inactive", "failed"}:
            out[name[: -len(".service")]] = False
        else:
            out[name[: -len(".service")]] = None
    return out


def _fork_active_states(units: tuple[str, ...]) -> dict[str, bool | None]:
    """Fallback: ONE fork for all units, never one per unit (202 ms -> 46 ms)."""
    names = [f"{u}.service" for u in units]
    try:
        result = subprocess.run(
            ["systemctl", "is-active", *names],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {u: None for u in units}
    lines = (result.stdout or "").strip().splitlines()
    if len(lines) != len(units):
        return {u: None for u in units}
    out: dict[str, bool | None] = {}
    for unit, line in zip(units, lines):
        state = line.strip()
        if state == "active":
            out[unit] = True
        elif state in {"inactive", "failed"}:
            out[unit] = False
        else:
            out[unit] = None
    return out


def batched_active_states(units: tuple[str, ...]) -> tuple[dict[str, bool | None], str]:
    """Return (states, source). Source is recorded so the cost stays visible."""
    states = _dbus_active_states(units)
    if states is not None:
        return {u: states.get(u) for u in units}, "dbus"
    return _fork_active_states(units), "fork"


def _dbus_enabled_states(units: tuple[str, ...]) -> dict[str, str | None] | None:
    """UnitFileState for every unit in one round trip.

    A unit with no unit file simply does not appear in the reply, which is the same
    signal `systemctl is-enabled` gives as "not-found" — build_services skips those
    rather than rendering a phantom inactive row.
    """
    mgr = _dbus_manager()
    if mgr is None:
        return None
    names = [f"{u}.service" for u in units]
    try:
        rows = mgr.ListUnitFilesByPatterns([], names)
    except Exception:
        return None
    found: dict[str, str | None] = {}
    for row in rows:
        leaf = str(row[0]).rsplit("/", 1)[-1]
        if leaf.endswith(".service"):
            found[leaf[: -len(".service")]] = str(row[1])
    return {u: found.get(u, "not-found") for u in units}


def _fork_enabled_states(units: tuple[str, ...]) -> dict[str, str | None]:
    """Fallback: ONE fork for all units (220 ms -> 46 ms)."""
    names = [f"{u}.service" for u in units]
    try:
        result = subprocess.run(
            ["systemctl", "is-enabled", *names],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {u: None for u in units}
    lines = (result.stdout or "").strip().splitlines()
    if len(lines) != len(units):
        return {u: None for u in units}
    return {u: (line.strip() or None) for u, line in zip(units, lines)}


def batched_enabled_states(units: tuple[str, ...]) -> tuple[dict[str, str | None], str]:
    states = _dbus_enabled_states(units)
    if states is not None:
        return states, "dbus"
    return _fork_enabled_states(units), "fork"



def _tri_state_label(value: bool | None, *, true_label: str, false_label: str) -> str:
    if value is True:
        return true_label
    if value is False:
        return false_label
    return "unknown"


def _memoized_unit_enabled_raw(
    base: Callable[[str], str | None],
) -> Callable[[str], str | None]:
    cache: dict[str, str | None] = {}

    def check(unit: str) -> str | None:
        if unit not in cache:
            cache[unit] = base(unit)
        return cache[unit]

    return check


def _read_mpe_env_keys(path: Path = MPE_ENV_PATH, keys: tuple[str, ...] = MPE_ENV_STATUS_KEYS) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" not in line or line.strip().startswith("#"):
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if key in keys:
                out[key] = value.strip()
    except OSError:
        pass
    return out


def _probe_process_pid(*, exe: str) -> tuple[int | None, bool]:
    """Return (pid, probe_ok). probe_ok is False on subprocess failure."""
    try:
        result = subprocess.run(
            ["pgrep", "-x", exe],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, False
    line = (result.stdout or "").strip().splitlines()
    if not line:
        return None, True
    try:
        return int(line[0]), True
    except ValueError:
        return None, True


METER_STATE_MAX_AGE_S = float(os.environ.get("MPE_METER_STATE_MAX_AGE_S", "5"))


def _surge_on_jack_graph(*, jackd_pid: int | None = None, now: float | None = None) -> bool | None:
    """Read wired= from meter.state — same contract as mpe_surge_on_jack_graph()."""
    if jackd_pid is None:
        return None
    from patch_browser.audio_engine import METER_STATE_FILE, read_meter_state

    raw = read_meter_state(METER_STATE_FILE)
    wired = raw.get("wired")
    updated = raw.get("updated")
    if wired not in {"0", "1"} or not updated:
        return None
    try:
        age = (time.time() if now is None else now) - float(updated)
    except ValueError:
        return None
    if age < 0 or age > METER_STATE_MAX_AGE_S:
        return None
    return wired == "1"


def _enabled_label(raw_enabled: str | None) -> str:
    if raw_enabled in {"masked", "masked-runtime"}:
        return "masked"
    if raw_enabled in {"enabled", "enabled-runtime", "static", "indirect", "alias"}:
        return "enabled"
    if raw_enabled == "disabled":
        return "disabled"
    return "unknown"


def build_services(
    *,
    unit_active: Callable[[str], bool | None] | None = None,
    unit_enabled: Callable[[str], str | None] | None = None,
) -> dict[str, Any]:
    """Per-unit liveness. Every cached field carries the age of what it cached.

    `active` comes from one batched probe (D-Bus where available, otherwise a single
    fork); `enabled` is per-unit on a long TTL because it is configuration, not
    runtime. See docs/measurements/systemd-liveness-cost-2026-08-19.md.
    """
    if unit_enabled is None:
        enabled_batch = _batched_enabled_cache()
        enabled_source = _services_probe_cache["enabled:batch"][1][1]  # type: ignore[index]
        enabled_age = _probe_age_s("enabled:batch")
        check_enabled_raw: Callable[[str], str | None] = lambda u: enabled_batch.get(u)
    else:
        check_enabled_raw = _memoized_unit_enabled_raw(unit_enabled)
        enabled_source = "injected"
        enabled_age = None

    batch: dict[str, bool | None] | None = None
    active_source = "injected"
    active_age: float | None = None
    if unit_active is None:
        batch = _batched_active_cache()
        active_source = _services_probe_cache["active:batch"][1][1]  # type: ignore[index]
        active_age = _probe_age_s("active:batch")
        check_active: Callable[[str], bool | None] = lambda u: batch.get(u)  # type: ignore[union-attr]
    else:
        check_active = _memoized_unit_active(unit_active)

    services: dict[str, Any] = {}
    for unit in STATUS_SERVICE_UNITS:
        raw_enabled = check_enabled_raw(unit)
        if raw_enabled == "not-found":
            continue
        active = check_active(unit)
        entry: dict[str, Any] = {
            "active": _tri_state_label(active, true_label="active", false_label="inactive"),
            "enabled": _enabled_label(raw_enabled),
            "stale": active is None and raw_enabled is None,
            "active_source": active_source,
            "enabled_source": enabled_source,
        }
        if active_age is not None:
            entry["active_age_s"] = active_age
        if enabled_age is not None:
            entry["enabled_age_s"] = enabled_age
        services[unit] = entry
    return services



def build_processes() -> dict[str, Any]:
    jackd_pid, jackd_ok = _probe_process_pid(exe="jackd")
    surge_pid, surge_ok = _probe_process_pid(exe="surge-xt-cli")
    return {
        "jackd_pid": jackd_pid,
        "surge_pid": surge_pid,
        "stale": not (jackd_ok and surge_ok),
    }


def build_graph_probe(*, jackd_pid: int | None = None) -> dict[str, Any]:
    on_graph = _surge_on_jack_graph(jackd_pid=jackd_pid, now=time.time())
    return {
        "surge_on_graph": on_graph,
        "stale": on_graph is None,
    }



def snapshot_path(*, run: Path | None = None) -> Path:
    base = run or run_dir()
    return base / SNAPSHOT_FILENAME


def seq_path(*, run: Path | None = None) -> Path:
    base = run or run_dir()
    return base / SEQ_FILENAME


def maintenance_flag_path(*, run: Path | None = None) -> Path:
    base = run or run_dir()
    return base / "maintenance"


def _parse_epoch(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        parsed = float(str(value).strip())
    except ValueError:
        return 0.0
    return parsed if parsed > 0 else 0.0


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def read_maintenance_flag(*, run: Path | None = None) -> dict[str, str]:
    path = maintenance_flag_path(run=run)
    return read_engine_state(path)


def set_maintenance_flag(
    *,
    run: Path | None = None,
    source: str = "calibration",
    deadline_s: float | None = None,
    pid: int | None = None,
) -> Path:
    """Write maintenance flag (D11 minimum slice) before suppressing reconciler action."""
    base = run or run_dir()
    base.mkdir(parents=True, exist_ok=True)
    path = maintenance_flag_path(run=base)
    owner = os.getpid() if pid is None else pid
    deadline = time.time() + (MAINTENANCE_DEFAULT_DEADLINE_S if deadline_s is None else deadline_s)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    tmp.write_text(
        f"pid={owner}\n"
        f"deadline={deadline:.3f}\n"
        f"source={source}\n",
        encoding="utf-8",
    )
    os.chmod(tmp, 0o644)
    tmp.replace(path)
    return path


def clear_maintenance_flag(*, run: Path | None = None) -> None:
    path = maintenance_flag_path(run=run)
    try:
        path.unlink()
    except OSError:
        pass


def maintenance_mode_active(*, run: Path | None = None, now: float | None = None) -> bool:
    """True while maintenance flag exists, unexpired, and setter PID is alive.

    Side effect: expired or dead-PID flags are unlinked (self-clearing D11).
    """
    raw = read_maintenance_flag(run=run)
    if not raw:
        return False
    now_ts = time.time() if now is None else now
    deadline = _parse_epoch(raw.get("deadline"))
    if deadline <= 0 or now_ts >= deadline:
        clear_maintenance_flag(run=run)
        return False
    owner = int(_parse_epoch(raw.get("pid")))
    if owner > 0 and not _pid_alive(owner):
        clear_maintenance_flag(run=run)
        return False
    return True


def field_age_stale(updated: float, *, now: float, threshold: float = STALE_THRESHOLD_S) -> bool:
    if updated <= 0:
        return True
    return (now - updated) >= threshold


def _systemd_unit_active_raw(unit: str) -> bool | None:
    """Return True/False when systemctl answers; None when unavailable or transitional."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", f"{unit}.service"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    state = (result.stdout or "").strip()
    if state == "active":
        return True
    if state in {"inactive", "failed", "deactivating"}:
        return False
    return None


def _batched_active_cache() -> dict[str, bool | None]:
    states, _source = _ttl_probe(  # type: ignore[misc]
        "active:batch",
        lambda: batched_active_states(STATUS_SERVICE_UNITS),
    )
    return states


def systemd_unit_active(unit: str) -> bool | None:
    """Liveness for one unit, served from the batch when the unit is in it.

    build_snapshot() asks about three units and build_services() about eleven. Answering
    each with its own probe was 22 forks per snapshot; the batch answers all of them in
    one round trip, so the per-unit entry point must consult it rather than reimplement
    it. A unit outside STATUS_SERVICE_UNITS still gets its own probe.
    """
    if unit in STATUS_SERVICE_UNITS:
        return _batched_active_cache().get(unit)
    return _ttl_probe(f"active:{unit}", lambda: _systemd_unit_active_raw(unit))  # type: ignore[return-value]


def _systemd_unit_enabled_raw(unit: str) -> str | None:
    try:
        result = subprocess.run(
            ["systemctl", "is-enabled", f"{unit}.service"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    state = (result.stdout or "").strip()
    return state or None


def _batched_enabled_cache() -> dict[str, str | None]:
    states, _source = _ttl_probe(  # type: ignore[misc]
        "enabled:batch",
        lambda: batched_enabled_states(STATUS_SERVICE_UNITS),
        ttl=ENABLED_PROBE_TTL_S,
    )
    return states


def systemd_unit_enabled_raw(unit: str) -> str | None:
    # Configuration, not runtime — long TTL, and build_services reports its age.
    if unit in STATUS_SERVICE_UNITS:
        return _batched_enabled_cache().get(unit)
    return _ttl_probe(  # type: ignore[return-value]
        f"enabled:{unit}",
        lambda: _systemd_unit_enabled_raw(unit),
        ttl=ENABLED_PROBE_TTL_S,
    )


def systemd_unit_enabled(unit: str) -> bool | None:
    """True/False when systemctl answers; None when unavailable.

    ``disabled`` is an explicit operator decision. Anything that auto-starts units
    must respect it, or ``systemctl disable`` silently does nothing.
    """
    try:
        result = subprocess.run(
            ["systemctl", "is-enabled", f"{unit}.service"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    state = (result.stdout or "").strip()
    if state in {"enabled", "enabled-runtime", "static", "indirect", "alias"}:
        return True
    if state in {"disabled", "masked", "masked-runtime"}:
        return False
    return None


def _memoized_unit_active(
    base: Callable[[str], bool | None],
) -> Callable[[str], bool | None]:
    cache: dict[str, bool | None] = {}

    def check(unit: str) -> bool | None:
        if unit not in cache:
            cache[unit] = base(unit)
        return cache[unit]

    return check


def process_field_stale(
    raw: dict[str, str] | None,
    *,
    unit: str,
    started_key: str,
    unit_active: Callable[[str], bool | None],
) -> bool:
    """Liveness-based staleness for transition-only state (jack/surge ``started``)."""
    if not raw:
        return True
    if _parse_epoch(raw.get(started_key)) <= 0:
        return True
    return unit_active(unit) is not True


def engine_field_stale(
    raw: dict[str, str] | None,
    *,
    unit_active: Callable[[str], bool | None],
) -> bool:
    """Engine state freshness follows the writer (surge-watchdog), not Surge itself.

    During recovery Surge is down but watchdog has just published state=recovering;
    gating on surge-xt-cli would null the field exactly when it carries the reason.
    """
    if not raw:
        return True
    if _parse_epoch(raw.get("updated")) <= 0:
        return True
    return unit_active(ENGINE_STATE_WRITER_UNIT) is not True


def reconcile_field_stale(raw: dict[str, str] | None) -> bool:
    """``last_restart=0`` means never restarted — valid, not stale."""
    return not raw


def looper_policy(*, looper_policy_env: str | None = None) -> str:
    """D15 placeholder — ``eval`` until adopt/kill verdict lands."""
    raw = (looper_policy_env if looper_policy_env is not None else os.environ.get("MPE_LOOPER_POLICY", "")).strip()
    if raw in VALID_LOOPER_POLICIES:
        return raw
    return "eval"


def looper_guard_label(*, looper_enabled: str | None = None) -> str:
    """D16 reflection — inverted ``MPE_LOOPER_ENABLED`` semantics."""
    enabled = looper_enabled if looper_enabled is not None else os.environ.get("MPE_LOOPER_ENABLED", "0")
    return "guarded" if str(enabled).strip() == "1" else "off"


def _wrap_field(
    value: Any,
    *,
    source: str,
    updated: float,
    stale: bool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "source": source,
        "updated": updated,
        "stale": stale,
    }
    payload["value"] = None if stale else value
    return payload


def _read_kv_file(path: Path) -> dict[str, str]:
    return read_engine_state(path)


def derive_mode(
    engine: dict[str, str],
    *,
    maintenance: bool = False,
) -> str:
    if maintenance:
        return "maintenance"
    state = (engine.get("state") or "").strip()
    if state in VALID_MODES:
        return state
    if state:
        return "failed"
    return "ok"


def build_snapshot(
    *,
    now: float | None = None,
    run: Path | None = None,
    hud_path: Path | None = None,
    looper_policy_env: str | None = None,
    looper_enabled: str | None = None,
    seq: int | None = None,
    unit_active: Callable[[str], bool | None] | None = None,
    unit_enabled: Callable[[str], str | None] | None = None,
    include_services: bool = True,
    include_runtime_probes: bool = False,
) -> dict[str, Any]:
    """Aggregate existing truth into schema v1 document."""
    base = run or run_dir()
    now_ts = time.time() if now is None else now
    check_unit = _memoized_unit_active(unit_active or systemd_unit_active)
    check_enabled_raw = _memoized_unit_enabled_raw(unit_enabled or systemd_unit_enabled_raw)

    engine_path = base / "engine.state"
    jack_path = base / "jack.state"
    surge_path = base / "surge.state"
    reconcile_path = base / "engine-reconcile.state"

    engine_raw = _read_kv_file(engine_path)
    jack_raw = _read_kv_file(jack_path)
    surge_raw = _read_kv_file(surge_path)
    reconcile_raw = _read_kv_file(reconcile_path)

    engine_updated = _parse_epoch(engine_raw.get("updated"))
    jack_updated = _parse_epoch(jack_raw.get("started"))
    surge_updated = _parse_epoch(surge_raw.get("started"))

    maintenance = maintenance_mode_active(run=base, now=now_ts)
    mode = derive_mode(engine_raw, maintenance=maintenance)

    default_hud = Path(os.environ.get("MPE_SL_HUD_STATE_FILE", str(Path.home() / ".mpe_sl_hud_state.json")))
    hud_file = hud_path or default_hud
    hud = read_sl_hud_state(path=hud_file, now=now_ts)

    if seq is None:
        seq = next_seq(run=base)

    policy = looper_policy(looper_policy_env=looper_policy_env)
    guard = looper_guard_label(looper_enabled=looper_enabled)

    engine_stale = engine_field_stale(engine_raw, unit_active=check_unit)
    jack_stale = process_field_stale(jack_raw, unit=JACK_UNIT, started_key="started", unit_active=check_unit)
    surge_stale = process_field_stale(surge_raw, unit=SURGE_UNIT, started_key="started", unit_active=check_unit)
    reconcile_stale = reconcile_field_stale(reconcile_raw)

    snap = {
        "schema": SCHEMA_VERSION,
        "seq": seq,
        "published_at": now_ts,
        "mode": mode,
        "engine": _wrap_field(
            engine_raw or None,
            source=str(engine_path),
            updated=engine_updated,
            stale=engine_stale,
        ),
        "jack": _wrap_field(
            jack_raw or None,
            source=str(jack_path),
            updated=jack_updated,
            stale=jack_stale,
        ),
        "surge": _wrap_field(
            surge_raw or None,
            source=str(surge_path),
            updated=surge_updated,
            stale=surge_stale,
        ),
        "reconcile": _wrap_field(
            reconcile_raw or None,
            source=str(reconcile_path),
            updated=_parse_epoch(reconcile_raw.get("last_restart")),
            stale=reconcile_stale,
        ),
        "looper": {
            "policy": {"value": policy, "source": "env:MPE_LOOPER_POLICY|default:eval", "stale": False},
            "guard": {
                "value": guard,
                "source": "env:MPE_LOOPER_ENABLED",
                "stale": False,
            },
            "hud": _wrap_field(
                hud or None,
                source=str(hud_file),
                updated=float(hud.get("updated_at") or 0.0) if hud else 0.0,
                stale=field_age_stale(float(hud.get("updated_at") or 0.0), now=now_ts) if hud else True,
            ),
        },
        "config": {
            "mpe_env": _read_mpe_env_keys(),
            "source": str(MPE_ENV_PATH),
            "stale": not MPE_ENV_PATH.is_file(),
        },
    }
    if include_services:
        # Pass the caller's injections through, never our own defaults — injecting
        # the per-unit defaults here bypassed the batch and cost 22 forks per build.
        snap["services"] = build_services(
            unit_active=check_unit if unit_active is not None else None,
            unit_enabled=check_enabled_raw if unit_enabled is not None else None,
        )
    if include_runtime_probes:
        snap["processes"] = build_processes()
        snap["graph"] = build_graph_probe(jackd_pid=snap["processes"].get("jackd_pid"))
    return snap


def read_seq(*, run: Path | None = None) -> int:
    path = seq_path(run=run)
    try:
        raw = path.read_text(encoding="utf-8").strip()
        return max(0, int(raw))
    except (OSError, ValueError):
        return 0


def next_seq(*, run: Path | None = None) -> int:
    """Atomically increment and return the next snapshot sequence number."""
    path = seq_path(run=run)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.touch(mode=0o644, exist_ok=True)
    with path.open("r+", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        raw = handle.read().strip()
        current = max(0, int(raw)) if raw else 0
        value = current + 1
        handle.seek(0)
        handle.truncate()
        handle.write(f"{value}\n")
        handle.flush()
        os.fsync(handle.fileno())
        return value


def write_snapshot(snapshot: dict[str, Any], *, run: Path | None = None) -> Path:
    """Atomically publish snapshot JSON (seq is allocated by ``next_seq`` in ``build_snapshot``)."""
    base = run or run_dir()
    base.mkdir(parents=True, exist_ok=True)
    target = snapshot_path(run=base)
    tmp = target.with_name(f"{target.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(snapshot, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o644)
    tmp.replace(target)
    return target


def read_snapshot(
    path: Path | None = None,
    *,
    now: float | None = None,
    max_schema: int | None = SCHEMA_VERSION,
) -> dict[str, Any]:
    """Load snapshot; raise ``ValueError`` on unknown schema major."""
    target = path or snapshot_path()
    now_ts = time.time() if now is None else now
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"snapshot unreadable: {target}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"snapshot not an object: {target}")
    schema = int(raw.get("schema") or 0)
    if max_schema is not None and schema > max_schema:
        raise ValueError(f"unsupported snapshot schema {schema} (max {max_schema})")
    published = float(raw.get("published_at") or 0.0)
    seq_stale = published > 0 and (now_ts - published) > (PUBLISH_INTERVAL_S * SEQ_STALE_FACTOR)
    raw["_meta"] = {
        "snapshot_stale": seq_stale,
        "age_s": (now_ts - published) if published > 0 else None,
    }
    return raw


def publish_snapshot(**kwargs: Any) -> Path:
    snap = build_snapshot(**kwargs)
    return write_snapshot(snap, run=kwargs.get("run"))


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Publish session.snapshot.json (Phase 1)")
    parser.add_argument("--run-dir", default=None, help="Override MPE_RUN_DIR (default: resolved run dir)")
    args = parser.parse_args()
    run = Path(args.run_dir) if args.run_dir else None
    path = publish_snapshot(run=run)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
