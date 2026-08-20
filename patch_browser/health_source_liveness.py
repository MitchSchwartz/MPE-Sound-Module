"""Boot-time liveness checks for health/monitor sources (T3b).

A counter whose source is missing or stale must fail loudly at start — not report 0
forever. See docs/measurements/t2-bug-class-sweep-2026-08-20.md Class B-live.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SourceSpec:
    name: str
    path: Path
    required: bool = True
    min_bytes: int = 1
    max_age_s: float | None = None
    required_keys: tuple[str, ...] = ()


def _parse_kv(path: Path) -> dict[str, str]:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    out: dict[str, str] = {}
    for line in raw.splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


def check_source(spec: SourceSpec, *, now: float | None = None) -> str | None:
    """Return an error string, or None if live."""
    t = time.time() if now is None else now
    if not spec.path.exists():
        if spec.required:
            return f"{spec.name}: missing ({spec.path})"
        return None
    try:
        size = spec.path.stat().st_size
    except OSError as exc:
        return f"{spec.name}: cannot stat {spec.path} ({exc.__class__.__name__})"
    if size < spec.min_bytes:
        return f"{spec.name}: empty ({spec.path}, {size} bytes)"
    if spec.required_keys:
        kv = _parse_kv(spec.path)
        missing = [k for k in spec.required_keys if k not in kv]
        if missing:
            return f"{spec.name}: missing keys {missing} in {spec.path}"
        if spec.max_age_s is not None and "updated" in kv:
            try:
                age = t - float(kv["updated"])
            except ValueError:
                return f"{spec.name}: bad updated= in {spec.path}"
            if age < 0 or age > spec.max_age_s:
                return f"{spec.name}: stale ({age:.1f}s > {spec.max_age_s}s)"
    return None


def specs_for_role(role: str) -> list[SourceSpec]:
    run = Path(os.environ.get("MPE_RUN_DIR", "/run/mpe"))
    meter = Path(os.environ.get("MPE_METER_STATE", str(run / "meter.state")))
    stale = float(os.environ.get("MPE_METER_STALE_AFTER_S", "3.0"))
    hud = Path(os.environ.get("MPE_SL_HUD_STATE", str(Path.home() / ".mpe_sl_hud_state.json")))

    if role == "sl-watchdog":
        return [
            SourceSpec(
                "meter.state",
                meter,
                required=True,
                max_age_s=stale,
                required_keys=("xruns", "updated"),
            ),
        ]
    if role == "looper-session":
        return [
            SourceSpec(
                "meter.state",
                meter,
                required=True,
                max_age_s=stale,
                required_keys=("xruns", "updated"),
            ),
            SourceSpec("engine.state", run / "engine.state", required=True, min_bytes=8),
        ]
    if role == "full":
        return [
            SourceSpec(
                "meter.state",
                meter,
                required=True,
                max_age_s=stale,
                required_keys=("xruns", "updated"),
            ),
            SourceSpec("engine.state", run / "engine.state", required=True),
            SourceSpec("jack.state", run / "jack.state", required=True),
            SourceSpec("surge.state", run / "surge.state", required=False),
        ]
    raise ValueError(f"unknown role: {role}")


def verify_role(role: str, *, now: float | None = None) -> list[str]:
    return [err for spec in specs_for_role(role) if (err := check_source(spec, now=now))]


def verify_or_exit(role: str, *, now: float | None = None) -> None:
    errors = verify_role(role, now=now)
    if not errors:
        print(f"health-source-liveness: {role} ok", flush=True)
        return
    for err in errors:
        print(f"HEALTH_SOURCE_FAIL: {err}", file=sys.stderr, flush=True)
    raise SystemExit(1)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--role",
        choices=("sl-watchdog", "looper-session", "full"),
        default="full",
    )
    args = ap.parse_args(argv)
    try:
        verify_or_exit(args.role)
    except SystemExit as exc:
        return int(exc.code or 1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
