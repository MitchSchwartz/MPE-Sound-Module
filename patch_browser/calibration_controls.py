"""Pass/fail rules for the calibration instrument's controls and the stage audit.

Calibration measures Surge alone and applies the gain arithmetically
(`scripts/calibrate-patch-normalization.py`). These rules decide whether the
capture can be trusted to do that, and whether the live stage agrees:

- positive: a pad of known size, taken off through Surge's volume law, reads
  as that size (a capture that cannot see a known change is blind);
- repeatability: the same capture twice reads the same;
- isolation: muting mpe-fx-host's stage during a capture changes nothing, so
  the stage is outside the calibration path and cannot be counted twice;
- stage audit: the live insert's output, captured at depth 0, lands where the
  stored gain predicts it will.

Each reading is (integrated LUFS, true peak dBTP). Pure functions: the script
captures, these judge.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

CONTROL_TOLERANCE_DB = 0.3
AUDIT_LUFS_TOLERANCE_DB = 0.5
AUDIT_PEAK_MAX_DBTP = -2.8


@dataclass(frozen=True)
class ControlResult:
    name: str
    ok: bool
    detail: str


def _deltas(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
    return a[0] - b[0], a[1] - b[1]


def _finite(*readings: tuple[float, float]) -> bool:
    return all(math.isfinite(v) for reading in readings for v in reading)


def positive_control(
    unity: tuple[float, float],
    padded: tuple[float, float],
    *,
    pad_db: float,
    tolerance_db: float = CONTROL_TOLERANCE_DB,
) -> ControlResult:
    """The padded capture reads pad_db quieter, on LUFS and on peak."""
    if not _finite(unity, padded):
        return ControlResult("positive", False, "a reading is not finite")
    d_lufs, d_peak = _deltas(unity, padded)
    ok = abs(d_lufs - pad_db) <= tolerance_db and abs(d_peak - pad_db) <= tolerance_db
    return ControlResult(
        "positive", ok,
        f"pad {pad_db:.1f} dB read as {d_lufs:.2f} LUFS / {d_peak:.2f} dBTP "
        f"(±{tolerance_db:.1f})",
    )


def repeatability_control(
    first: tuple[float, float],
    second: tuple[float, float],
    *,
    tolerance_db: float = CONTROL_TOLERANCE_DB,
) -> ControlResult:
    """The same capture twice differs by no more than the tolerance."""
    if not _finite(first, second):
        return ControlResult("repeatability", False, "a reading is not finite")
    d_lufs, d_peak = _deltas(first, second)
    ok = abs(d_lufs) <= tolerance_db and abs(d_peak) <= tolerance_db
    return ControlResult(
        "repeatability", ok,
        f"Δ {d_lufs:+.2f} LUFS / {d_peak:+.2f} dBTP (≤{tolerance_db:.1f})",
    )


def isolation_control(
    before: tuple[float, float],
    stage_moved: tuple[float, float],
    *,
    tolerance_db: float = CONTROL_TOLERANCE_DB,
) -> ControlResult:
    """Moving the stage gain during a capture changes nothing in it."""
    if not _finite(before, stage_moved):
        return ControlResult(
            "isolation", False,
            "a reading is not finite: the stage may be in the calibration path",
        )
    d_lufs, d_peak = _deltas(before, stage_moved)
    ok = abs(d_lufs) <= tolerance_db and abs(d_peak) <= tolerance_db
    return ControlResult(
        "isolation", ok,
        f"stage moved, capture Δ {d_lufs:+.2f} LUFS / {d_peak:+.2f} dBTP (≤{tolerance_db:.1f})",
    )


def stage_audit(
    predicted_lufs: float,
    measured: tuple[float, float],
    *,
    lufs_tolerance_db: float = AUDIT_LUFS_TOLERANCE_DB,
    peak_max_dbtp: float = AUDIT_PEAK_MAX_DBTP,
) -> ControlResult:
    """The live insert's output against the arithmetic: LUFS where predicted, and
    the peak under the ceiling the calibration promised."""
    if not _finite(measured) or not math.isfinite(predicted_lufs):
        return ControlResult("stage audit", False, "a reading is not finite")
    lufs, peak = measured
    off = lufs - predicted_lufs
    ok = abs(off) <= lufs_tolerance_db and peak <= peak_max_dbtp
    return ControlResult(
        "stage audit", ok,
        f"{lufs:.2f} LUFS vs {predicted_lufs:.2f} predicted ({off:+.2f}, ±{lufs_tolerance_db:.1f}); "
        f"peak {peak:.2f} dBTP (≤{peak_max_dbtp:.1f})",
    )
