"""Poly governor v2 — continuous limit curve, rise-rate bias, rate-limited steps."""

from __future__ import annotations

import math


def smoothstep(t: float) -> float:
    """Hermite smoothstep on [0, 1]; clamps outside."""
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    return t * t * (3.0 - 2.0 * t)


def rise_bias(
    dload_dt: float,
    *,
    full_rate: float,
    max_bias: float,
    enabled: bool = True,
) -> float:
    """Virtual load points from rate-of-rise (%/s)."""
    if not enabled or dload_dt <= 0.0 or full_rate <= 0.0:
        return 0.0
    ratio = dload_dt / full_rate
    if ratio >= 1.0:
        return max_bias
    return ratio * max_bias


def continuous_target_limit(
    effective_load: float,
    *,
    ceiling: int,
    floor: int,
    soft_start: float,
    hard: float,
) -> int:
    """Map load % to target poly limit (continuous curve)."""
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
