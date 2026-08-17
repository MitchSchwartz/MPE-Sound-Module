"""Peak dBFS helpers for the live output meter."""

from __future__ import annotations

import math

# Bar maps this floor to empty and 0 dBFS to full.
PEAK_METER_FLOOR_DBFS = -48.0
PEAK_METER_CEIL_DBFS = 0.0


def linear_peak_to_dbfs(peak: float) -> float | None:
    """Convert linear peak (0..1+) to dBFS. Returns None when silent."""
    if peak <= 0.0 or not math.isfinite(peak):
        return None
    return 20.0 * math.log10(peak)


def dbfs_to_meter_ratio(dbfs: float | None) -> float | None:
    """Map dBFS to 0..1 bar fill. None when offline/silent."""
    if dbfs is None or not math.isfinite(dbfs):
        return None
    if dbfs <= PEAK_METER_FLOOR_DBFS:
        return 0.0
    if dbfs >= PEAK_METER_CEIL_DBFS:
        return 1.0
    span = PEAK_METER_CEIL_DBFS - PEAK_METER_FLOOR_DBFS
    return (dbfs - PEAK_METER_FLOOR_DBFS) / span


def peak_meter_color_dbfs(dbfs: float | None) -> str:
    """Semantic bucket for theming: ok | warn | hot."""
    if dbfs is None:
        return "muted"
    if dbfs < -18.0:
        return "ok"
    if dbfs < -6.0:
        return "warn"
    return "hot"
