"""Session control plane snapshot — schema v1 (spec D6, Phase 1).

Aggregates existing runtime truth under ``/run/mpe/session.snapshot.json``.
Does not own engine state; readers treat stale sub-sources as unknown (never
last-known-good).
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
    """True while maintenance flag exists, unexpired, and setter PID is alive."""
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


def systemd_unit_active(unit: str) -> bool | None:
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
    """Engine state is transition-written; trust it only while Surge is confirmed live."""
    if not raw:
        return True
    if _parse_epoch(raw.get("updated")) <= 0:
        return True
    return unit_active(SURGE_UNIT) is not True


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
) -> dict[str, Any]:
    """Aggregate existing truth into schema v1 document."""
    base = run or run_dir()
    now_ts = time.time() if now is None else now
    check_unit = _memoized_unit_active(unit_active or systemd_unit_active)

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

    return {
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
    }


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
