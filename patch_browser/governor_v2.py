"""Poly governor v2 — continuous limit curve, rise-rate bias, rate-limited steps."""

from __future__ import annotations


def smoothstep(t: float) -> float:
    """Hermite smoothstep on [0, 1]; clamps outside."""
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    return t * t * (3.0 - 2.0 * t)


def normalize_jack_load(raw: float, baseline: float) -> float:
    """Linear stress above platform idle baseline (percentage points)."""
    return max(0.0, min(100.0, raw - baseline))


def rise_bias(
    dload_dt: float,
    *,
    full_rate: float,
    max_bias: float,
    min_rate: float = 0.0,
    enabled: bool = True,
) -> float:
    """Virtual load points from rate-of-rise (%/s)."""
    if not enabled or dload_dt <= min_rate or full_rate <= 0.0:
        return 0.0
    ratio = dload_dt / full_rate
    if ratio >= 1.0:
        return max_bias
    return ratio * max_bias


def always_on_rest_top(
    *,
    ceiling: int,
    floor: int,
    min_headroom: int,
    rest_cap: int = 0,
) -> int:
    """Resting poly limit at zero stress (pre-engaged cap overrides headroom)."""
    if rest_cap > 0:
        return max(floor, min(ceiling, rest_cap))
    headroom = max(0, min_headroom)
    return max(floor, ceiling - headroom)


def always_on_target_limit(
    normalized_load: float,
    *,
    ceiling: int,
    floor: int,
    min_headroom: int,
    hard: float = 100.0,
    rest_cap: int = 0,
) -> int:
    """Always-on progressive limit — tight overhead at rest, floor at full stress."""
    top = always_on_rest_top(
        ceiling=ceiling, floor=floor, min_headroom=min_headroom, rest_cap=rest_cap
    )
    if normalized_load <= 0.0:
        return top
    if normalized_load >= hard:
        return floor
    if hard <= 0.0:
        return floor
    fraction = smoothstep(normalized_load / hard)
    target = top - int(round((top - floor) * fraction))
    return max(floor, min(ceiling, target))


def continuous_target_limit(
    effective_load: float,
    *,
    ceiling: int,
    floor: int,
    soft_start: float,
    hard: float,
) -> int:
    """Threshold progressive limit (legacy v2 path)."""
    if effective_load <= soft_start:
        return ceiling
    if effective_load >= hard:
        return floor
    span = hard - soft_start
    if span <= 0.0:
        return floor
    t = (effective_load - soft_start) / span
    fraction = smoothstep(t)
    target = ceiling - int(round((ceiling - floor) * fraction))
    return max(floor, min(ceiling, target))


def rate_limited_target(
    current: int,
    desired: int,
    *,
    last_step_down_at: float | None,
    now: float,
    step_interval_s: float,
    max_step_down: int = 1,
) -> int | None:
    """Return next limit when stepping down, or None if rate-limited."""
    if desired >= current:
        return desired
    if last_step_down_at is None:
        return max(desired, current - max_step_down)
    if now - last_step_down_at >= step_interval_s:
        return max(desired, current - max_step_down)
    return None


def adaptive_poll_interval(
    *,
    load: float | None,
    dload_dt: float | None,
    soft_start: float,
    fast_s: float,
    slow_s: float,
) -> float:
    """50 ms when hot/rising; slow interval when calm."""
    if load is None:
        return slow_s
    if dload_dt is not None and dload_dt > 0.0:
        return fast_s
    if load > soft_start:
        return fast_s
    return slow_s


# --- The emergency rule: the ONE place it is decided ---------------------------
# Measured 2026-10-12 (docs/measurements/capacity-fx-96x2-pi5-2026-10-12.md),
# 96 x 2, Pi 5:
#   * Note-on overruns happen at ANY load. Every Cloud Horn and Crystals window
#     overran in its 2 s note-on lead-in, 4 notes included (meter 21-27). In the
#     Cloud Horn governor cell, single added notes overran at raw 15-26, each
#     within 80 ms of its note step, and each one dropped the limit 48 -> 3.
#     Closed Hat, driven the same way (same channels, bend and pressure), had
#     zero attack overruns at 4-48 notes.
#   * Sustained overload looks different: with no note-ons, "overruns" (at least
#     one per 10 s) began at dsp median 51 (Cloud Horn 19, Crystals 12); "rare"
#     (one per 20-40 s) sat at 49-52.
#   * Crystals' governor cell overran at raw 92: real overload, emergency right.
# ATTACK_WINDOW_S: the measurement classed every overrun in the 2 s after a
#   note-on burst as attack (the V9 lead-in), and found them at every N. The
#   governor sees an xrun up to 200 ms (meter write interval) + one poll late,
#   which the 2 s window also covers.
# REPEAT_WINDOW_S: a second non-attack event within 10 s is the measurement's
#   own line between "rare" and "overruns".
# EVENT_MERGE_S: mpe-peak-meter writes meter.state every 200 ms, and jackd
#   notifies each client of one overrun separately (2-4 counts). Deltas closer
#   than one write interval plus margin are one event, not a repeat.
# The load arm reuses LIMIT_HARD: at stress >= HARD the curve is already at its
#   floor, so only the emergency can cut further. No second load threshold.
ATTACK_WINDOW_S = 2.0
REPEAT_WINDOW_S = 10.0
EVENT_MERGE_S = 0.25


def xrun_at_note_on(*, now: float, last_note_on_at: float | None) -> bool:
    """True when the xrun seen at ``now`` falls in a note-on burst's attack window."""
    if last_note_on_at is None:
        return False
    return 0.0 <= now - last_note_on_at <= ATTACK_WINDOW_S


def xrun_warrants_emergency(
    *,
    stress: float,
    hard: float,
    at_note_on: bool,
    prior_events: list[float] | tuple[float, ...],
    now: float,
) -> bool:
    """The emergency rule, called on a tick that saw an xrun event.

    Emergency when the load is already at the curve's floor point
    (``stress >= hard``), or when this xrun is not at a note-on and another
    non-note-on xrun event happened within ``REPEAT_WINDOW_S`` before it.
    An isolated xrun, or one at a note-on, at load below the floor point is
    logged and counted by the caller, never acted on by the emergency.
    ``prior_events`` are the times of earlier non-note-on xrun events.
    """
    if stress >= hard:
        return True
    if at_note_on:
        return False
    return any(
        EVENT_MERGE_S < now - then <= REPEAT_WINDOW_S for then in prior_events
    )
