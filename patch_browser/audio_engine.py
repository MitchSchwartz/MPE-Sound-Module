"""Audio engine selection, watchdog cooldown, looper guard, and HUD state reader."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

ReconcileAction = Literal["proceed", "skip_cooldown", "skip_jackd_settling", "escalate_failed"]

LOOPER_GUARD_MESSAGE = (
    "looper requires MPE_AUDIO_ENGINE=alsa until the JACK callback client ships (spec Phase 2)."
)

DEFAULT_ENGINE = "jack"
VALID_ENGINES = frozenset({"jack", "alsa"})
VALID_ENGINE_STATES = frozenset({"ok", "degraded", "recovering", "failed"})
VALID_LOOPER_LABELS = frozenset({"guarded", "enabled", "off"})

ENGINE_STATE_FILE = Path("/run/mpe/engine.state")
ENGINE_STATE_MAX_BYTES = 4096

COOLDOWN_SEC = 90
JACKD_SETTLE_SEC = 15
MAX_SUPERVISOR_RESTARTS = 3


def resolve_audio_engine(value: str | None = None) -> str:
    """Return configured engine; default ``jack`` when unset (Gate A)."""
    raw = (value if value is not None else os.environ.get("MPE_AUDIO_ENGINE", "")).strip().lower()
    if not raw:
        return DEFAULT_ENGINE
    if raw in VALID_ENGINES:
        return raw
    return DEFAULT_ENGINE


def looper_guard_blocked(
    *,
    engine: str | None = None,
    looper_enabled: str | int | None = None,
) -> bool:
    return resolve_audio_engine(engine) == "jack" and str(looper_enabled or "0").strip() == "1"


def looper_guard_exit_code(
    *,
    looper_service: bool = False,
    invocation_id: str | None = None,
) -> int:
    """Service / systemd invocation exits 0; interactive callers exit non-zero."""
    if looper_service or invocation_id:
        return 0
    return 1


def reconcile_cooldown_decide(
    now: int,
    *,
    last_supervisor_restart: int | None,
    supervisor_restarts_without_ok: int,
    jackd_last_start: int | None,
    cooldown_sec: int = COOLDOWN_SEC,
    jackd_settle_sec: int = JACKD_SETTLE_SEC,
    max_restarts: int = MAX_SUPERVISOR_RESTARTS,
) -> tuple[ReconcileAction, str]:
    """Table-driven supervisor cooldown (spec D3)."""
    if supervisor_restarts_without_ok >= max_restarts:
        return "escalate_failed", f"{max_restarts} supervisor restarts without reaching ok"

    if jackd_last_start is not None and (now - jackd_last_start) < jackd_settle_sec:
        return "skip_jackd_settling", f"jackd restarted {now - jackd_last_start}s ago (< {jackd_settle_sec}s)"

    if last_supervisor_restart is None or last_supervisor_restart <= 0:
        return "proceed", "first supervisor-initiated restart"

    elapsed = now - last_supervisor_restart
    if elapsed < cooldown_sec:
        return "skip_cooldown", f"last supervisor restart {elapsed}s ago (< {cooldown_sec}s)"

    return "proceed", f"cooldown satisfied ({elapsed}s since last restart)"


def read_engine_state(path: Path | None = None) -> dict[str, str]:
    """Parse ``engine.state``; tolerate missing, empty, or partially-written files."""
    target = path or ENGINE_STATE_FILE
    result: dict[str, str] = {}
    try:
        with target.open(encoding="utf-8", errors="replace") as fh:
            raw = fh.read(ENGINE_STATE_MAX_BYTES)
    except OSError:
        return result
    for line in raw.splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if key:
            result[key] = value
    return result


def engine_hud_should_show(state: dict[str, str] | None) -> bool:
    """Header badge when runtime state is published or looper is guarded."""
    if not state:
        return False
    if state.get("looper") == "guarded":
        return True
    engine = state.get("engine") or state.get("active")
    status = state.get("state")
    return bool(engine or status)


def engine_hud_label(state: dict[str, str] | None) -> str:
    """Compact touch HUD label for engine + state + looper guard."""
    if not state:
        return ""
    active = state.get("active") or state.get("engine") or "?"
    status = state.get("state") or "?"
    parts: list[str] = []
    if active in VALID_ENGINES:
        parts.append(active.upper())
    else:
        parts.append(str(active)[:4].upper())
    if status in VALID_ENGINE_STATES and status != "ok":
        parts.append(status[:3])
    if state.get("looper") == "guarded":
        parts.append("L⛔")
    return "·".join(parts) if parts else ""


def engine_hud_semantic(state: dict[str, str] | None) -> str:
    """Theme token: accent for degraded/recovering, danger for failed."""
    if not state:
        return "muted"
    status = state.get("state")
    if status == "failed":
        return "danger"
    if status in {"degraded", "recovering"}:
        return "accent"
    if state.get("looper") == "guarded":
        return "accent"
    return "muted"
