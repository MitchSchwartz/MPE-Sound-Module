"""Peak dBFS helpers for the live output meter."""

from __future__ import annotations

import math

# Bar scale and color bands — single source for the OUT meter.
PEAK_METER_FLOOR_DBFS = -48.0  # empty bar
PEAK_METER_CLIP_DBFS = 0.0  # full bar (digital clip / headroom reference)

# Color bands tuned for normalized Surge output (SAFE_PEAK_DBTP = -3 in patch_normalization).
# With Norm on, peaks cap near -3 dBFS — red at 0 dBFS was unreachable in normal play.
PEAK_METER_YELLOW_DBFS = -12.0  # warn from here up
PEAK_METER_ORANGE_DBFS = -6.0  # orange from here up
PEAK_METER_RED_DBFS = -3.0  # hot from here up (norm ceiling; still hot at 0 if Norm off)


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
    if dbfs >= PEAK_METER_CLIP_DBFS:
        return 1.0
    span = PEAK_METER_CLIP_DBFS - PEAK_METER_FLOOR_DBFS
    return (dbfs - PEAK_METER_FLOOR_DBFS) / span


def peak_meter_color_dbfs(dbfs: float | None) -> str:
    """Semantic bucket for theming: ok | warn | orange | hot."""
    if dbfs is None:
        return "muted"
    if dbfs >= PEAK_METER_RED_DBFS:
        return "hot"
    if dbfs >= PEAK_METER_ORANGE_DBFS:
        return "orange"
    if dbfs >= PEAK_METER_YELLOW_DBFS:
        return "warn"
    return "ok"
