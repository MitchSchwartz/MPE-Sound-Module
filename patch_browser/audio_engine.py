"""Audio engine constants, watchdog cooldown, looper guard, and HUD state reader.

JACK is the only audio engine (spec D3, amended 2026-08-13 — ALSA removed
entirely as a product audio path, not just its automatic fallback). There is
no ``MPE_AUDIO_ENGINE`` to resolve and nothing to switch between.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

ReconcileAction = Literal["proceed", "skip_cooldown", "skip_jackd_settling", "escalate_failed"]

LOOPER_GUARD_MESSAGE = (
    "looper is unavailable until the JACK callback client ships (spec Phase 2)"
    " — there is no ALSA route to run it through."
)

ENGINE_NAME = "jack"
VALID_ENGINE_STATES = frozenset({"ok", "recovering", "failed"})
VALID_LOOPER_LABELS = frozenset({"guarded", "enabled", "off"})

ENGINE_STATE_FILE = Path("/run/mpe/engine.state")
RECONCILE_STATE_FILE = Path("/run/mpe/engine-reconcile.state")
JACK_STATE_FILE = Path("/run/mpe/jack.state")
METER_STATE_FILE = Path("/run/mpe/meter.state")
ENGINE_STATE_MAX_BYTES = 4096

COOLDOWN_SEC = 30
# 15s was sized for an ALSA-contention hazard (Surge holding the tier device on
# the fallback path) that no longer exists — ALSA is not a reachable engine at
# all now. jackd is typically ready in ~6s on the Sound Blaster Play! 3; 5s
# clears that plus the watchdog's 5s poll cycle without the old margin.
JACKD_SETTLE_SEC = 5
MAX_SUPERVISOR_RESTARTS = 3


def looper_guard_blocked(*, looper_enabled: str | int | None = None) -> bool:
    """True whenever the looper is asked for — JACK cannot run it until Phase 2."""
    return str(looper_enabled or "0").strip() == "1"


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


def read_reconcile_state(path: Path | None = None) -> dict[str, str]:
    """Parse supervisor reconcile state (cooldown counter)."""
    target = path or RECONCILE_STATE_FILE
    return read_engine_state(target)


def read_jack_state(path: Path | None = None) -> dict[str, str]:
    target = path or JACK_STATE_FILE
    return read_engine_state(target)


def _parse_epoch(value: str | None) -> int:
    if not value:
        return 0
    try:
        parsed = int(value.strip())
    except ValueError:
        return 0
    return parsed if parsed > 0 else 0


def audio_switch_progress_message(
    engine: dict[str, str] | None,
    reconcile: dict[str, str] | None = None,
    *,
    now: int | None = None,
    jack: dict[str, str] | None = None,
) -> tuple[str, str | None, float]:
    """Overlay hint + optional toast while a planned audio switch runs.

    Returns ``(overlay_hint, toast_message_or_none, toast_seconds)``.
    """
    import time as _time

    engine = engine or {}
    reconcile = reconcile or {}
    jack = jack or {}
    now_ts = now if now is not None else int(_time.time())

    state = engine.get("state") or ""
    reason = engine.get("reason") or ""
    active = engine.get("active") or ""

    if state == "ok" and active == "jack":
        return "Audio restored", "Audio ready", 2.0

    if state == "failed":
        if reason == "no-server":
            return "JACK server failed — check DAC", "Audio failed — check DAC", 4.0
        if reason == "supervisor-exhausted":
            return (
                "Recovery paused — replug DAC or wait",
                f"Recovery paused — wait {COOLDOWN_SEC}s or replug DAC",
                5.0,
            )
        return "Audio failed", "Audio failed — check connection", 4.0

    last_restart = _parse_epoch(reconcile.get("last_restart"))
    restart_count = _parse_epoch(reconcile.get("restarts"))
    if restart_count > 0 and last_restart > 0:
        since = now_ts - last_restart
        if since < COOLDOWN_SEC:
            remaining = COOLDOWN_SEC - since
            hint = f"Waiting to retry ({remaining}s)…"
            toast = f"Recovery paused — {remaining}s until retry"
            return hint, toast, min(float(remaining + 1), 8.0)

    jack_started = _parse_epoch(jack.get("started"))
    if jack_started > 0 and (now_ts - jack_started) < JACKD_SETTLE_SEC:
        return "JACK server starting…", "Restarting audio engine…", 3.0

    if reason == "promote-planned":
        return "Reconnecting Surge…", "Reconnecting Surge to JACK…", 3.0
    if reason in {"settings-change", "profile-change"}:
        return "Restarting JACK server…", "Applying audio settings…", 3.0
    if reason == "promote-timeout":
        return "Surge reconnect slow — still trying…", "Still reconnecting Surge…", 3.0

    return "Restarting audio…", "Applying audio settings…", 3.0


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


def read_meter_state(path: Path | None = None) -> dict[str, str]:
    """Parse ``meter.state`` from the compiled peak meter process."""
    return read_engine_state(path or METER_STATE_FILE)


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
    if active == ENGINE_NAME:
        parts.append(active.upper())
    else:
        parts.append(str(active)[:4].upper())
    if status in VALID_ENGINE_STATES and status != "ok":
        parts.append(status[:3])
    if state.get("looper") == "guarded":
        parts.append("L⛔")
    return "·".join(parts) if parts else ""


def engine_hud_semantic(state: dict[str, str] | None) -> str:
    """Theme token: accent for recovering, danger for failed."""
    if not state:
        return "muted"
    status = state.get("state")
    if status == "failed":
        return "danger"
    if status == "recovering":
        return "accent"
    if state.get("looper") == "guarded":
        return "accent"
    return "muted"
