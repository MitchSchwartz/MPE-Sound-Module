"""Session control plane snapshot — schema v1 (spec D6, Phase 1).

Aggregates existing runtime truth under ``/run/mpe/session.snapshot.json``.
Does not own engine state; readers treat stale sub-sources as unknown (never
last-known-good).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from patch_browser.audio_engine import read_engine_state
from patch_browser.sl_hud_state import read_sl_hud_state

SCHEMA_VERSION = 1
STALE_THRESHOLD_S = 1.5
PUBLISH_INTERVAL_S = 0.5
SEQ_STALE_FACTOR = 2

SNAPSHOT_FILENAME = "session.snapshot.json"
SEQ_FILENAME = "session.snapshot.seq"

VALID_LOOPER_POLICIES = frozenset({"eval", "adopt", "disabled"})
VALID_MODES = frozenset({"ok", "recovering", "failed", "maintenance"})


def run_dir() -> Path:
    return Path(os.environ.get("MPE_RUN_DIR", "/run/mpe"))


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


def field_age_stale(updated: float, *, now: float, threshold: float = STALE_THRESHOLD_S) -> bool:
    if updated <= 0:
        return True
    return (now - updated) >= threshold


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
    now: float,
    threshold: float = STALE_THRESHOLD_S,
) -> dict[str, Any]:
    stale = field_age_stale(updated, now=now, threshold=threshold)
    payload: dict[str, Any] = {
        "source": source,
        "updated": updated,
        "stale": stale,
    }
    if stale:
        payload["value"] = None
    else:
        payload["value"] = value
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
        return state
    return "ok"


def build_snapshot(
    *,
    now: float | None = None,
    run: Path | None = None,
    hud_path: Path | None = None,
    looper_policy_env: str | None = None,
    looper_enabled: str | None = None,
    seq: int | None = None,
) -> dict[str, Any]:
    """Aggregate existing truth into schema v1 document."""
    base = run or run_dir()
    now_ts = time.time() if now is None else now

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

    maintenance = maintenance_flag_path(run=base).exists()
    mode = derive_mode(engine_raw, maintenance=maintenance)

    hud = read_sl_hud_state(now=now_ts) if hud_path is None else _read_hud_at(hud_path, now=now_ts)

    if seq is None:
        seq = read_seq(run=base) + 1

    policy = looper_policy(looper_policy_env=looper_policy_env)
    guard = looper_guard_label(looper_enabled=looper_enabled)

    return {
        "schema": SCHEMA_VERSION,
        "seq": seq,
        "published_at": now_ts,
        "mode": mode,
        "engine": _wrap_field(
            engine_raw or None,
            source=str(engine_path),
            updated=engine_updated,
            now=now_ts,
        ),
        "jack": _wrap_field(
            jack_raw or None,
            source=str(jack_path),
            updated=jack_updated,
            now=now_ts,
        ),
        "surge": _wrap_field(
            surge_raw or None,
            source=str(surge_path),
            updated=surge_updated,
            now=now_ts,
        ),
        "reconcile": _wrap_field(
            reconcile_raw or None,
            source=str(reconcile_path),
            updated=_parse_epoch(reconcile_raw.get("last_restart")),
            now=now_ts,
            threshold=STALE_THRESHOLD_S * 4,
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
                source=str(hud_path or os.environ.get("MPE_SL_HUD_STATE_FILE", Path.home() / ".mpe_sl_hud_state.json")),
                updated=float(hud.get("updated_at") or 0.0) if hud else 0.0,
                now=now_ts,
            ),
        },
    }


def _read_hud_at(path: Path, *, now: float) -> dict:
    prev = os.environ.get("MPE_SL_HUD_STATE_FILE")
    os.environ["MPE_SL_HUD_STATE_FILE"] = str(path)
    try:
        return read_sl_hud_state(now=now)
    finally:
        if prev is None:
            os.environ.pop("MPE_SL_HUD_STATE_FILE", None)
        else:
            os.environ["MPE_SL_HUD_STATE_FILE"] = prev


def read_seq(*, run: Path | None = None) -> int:
    path = seq_path(run=run)
    try:
        raw = path.read_text(encoding="utf-8").strip()
        return max(0, int(raw))
    except (OSError, ValueError):
        return 0


def write_seq(value: int, *, run: Path | None = None) -> None:
    path = seq_path(run=run)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(f"{value}\n", encoding="utf-8")
    tmp.replace(path)


def write_snapshot(snapshot: dict[str, Any], *, run: Path | None = None) -> Path:
    """Atomically publish snapshot JSON and bump seq counter."""
    base = run or run_dir()
    base.mkdir(parents=True, exist_ok=True)
    target = snapshot_path(run=base)
    tmp = target.with_suffix(".tmp")
    seq = int(snapshot.get("seq") or 0)
    tmp.write_text(json.dumps(snapshot, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o644)
    tmp.replace(target)
    if seq > 0:
        write_seq(seq, run=base)
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
    seq = int(raw.get("seq") or 0)
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
    parser.add_argument("--run-dir", default=os.environ.get("MPE_RUN_DIR", "/run/mpe"))
    args = parser.parse_args()
    path = publish_snapshot(run=Path(args.run_dir))
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
