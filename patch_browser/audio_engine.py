"""Audio engine selection, watchdog cooldown, and looper guard (Phase 1 JACK)."""

from __future__ import annotations

import os
from typing import Literal

ReconcileAction = Literal["proceed", "skip_cooldown", "skip_jackd_settling", "escalate_failed"]

LOOPER_GUARD_MESSAGE = (
    "looper requires MPE_AUDIO_ENGINE=alsa until the JACK callback client ships (spec Phase 2)."
)

DEFAULT_ENGINE = "jack"
VALID_ENGINES = frozenset({"jack", "alsa"})

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
