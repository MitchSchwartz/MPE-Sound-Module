"""Per-patch volume normalization — static calibration store and helpers."""

from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Integrated LUFS target for the mid-loudness point of the performance gesture.
TARGET_LUFS = -18.0

# True-peak headroom (dBTP) when capping gain at calibration time.
SAFE_PEAK_DBTP = -1.0


def default_normalization_path() -> Path:
    """Resolve normalization JSON path (env override, then user state file)."""
    env = os.environ.get("MPE_NORMALIZATION_FILE", "").strip()
    if env:
        return Path(env).expanduser()
    return Path.home() / ".patch_browser_normalization.json"


def repo_starter_path() -> Path:
    """Shipped starter file in the repo (empty `{}` until calibrated)."""
    return Path(__file__).resolve().parent.parent / "config" / "patch_normalization.json"


def db_to_linear(gain_db: float) -> float:
    """Convert dB gain to Surge amp/volume linear scale (1.0 = unity)."""
    return 10.0 ** (gain_db / 20.0)


def linear_to_db(linear: float) -> float:
    if linear <= 0:
        return -120.0
    return 20.0 * math.log10(linear)


def compute_gain_db(
    lufs_integrated: float,
    true_peak_dbtp: float,
    *,
    target_lufs: float = TARGET_LUFS,
    safe_peak_dbtp: float = SAFE_PEAK_DBTP,
) -> float:
    """
    Compute static per-patch gain from measured gesture render.

    Normalizes toward mid-loudness (integrated LUFS) but caps gain so boosting
    does not push true peak past safe_peak_dbtp — computed once at calibration.
    """
    mid_gain = target_lufs - lufs_integrated
    peak_gain = safe_peak_dbtp - true_peak_dbtp
    return min(mid_gain, peak_gain)


class PatchNormalizationStore:
    """Load/save patch_normalization.json keyed by patch name (stem)."""

    def __init__(self, path: Path | None = None):
        self.path = path or default_normalization_path()
        self._data: dict[str, dict[str, Any]] = {}
        self.load()

    def load(self) -> None:
        for candidate in (self.path, repo_starter_path()):
            if not candidate.exists():
                continue
            try:
                raw = json.loads(candidate.read_text())
            except (OSError, json.JSONDecodeError) as exc:
                print(f"Warning: could not load normalization file {candidate}: {exc}")
                continue
            if isinstance(raw, dict):
                self._data = raw
                return
        self._data = {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=2, sort_keys=True) + "\n")

    @staticmethod
    def patch_key(patch_name: str) -> str:
        return Path(patch_name).stem

    def get_entry(self, patch_name: str) -> dict[str, Any] | None:
        entry = self._data.get(self.patch_key(patch_name))
        return entry if isinstance(entry, dict) else None

    def get_gain_db(self, patch_name: str) -> float | None:
        entry = self.get_entry(patch_name)
        if not entry or not entry.get("enabled", True):
            return None
        gain = entry.get("gain_db")
        if gain is None:
            return None
        return float(gain)

    def get_gain_linear(self, patch_name: str) -> float:
        gain_db = self.get_gain_db(patch_name)
        if gain_db is None:
            return 1.0
        return db_to_linear(gain_db)

    def set_calibration(
        self,
        patch_name: str,
        gain_db: float,
        lufs_measured: float,
        *,
        enabled: bool = True,
        calibrated_at: str | None = None,
        true_peak_dbtp: float | None = None,
    ) -> None:
        key = self.patch_key(patch_name)
        entry: dict[str, Any] = {
            "gain_db": round(float(gain_db), 3),
            "enabled": enabled,
            "lufs_measured": round(float(lufs_measured), 2),
            "calibrated_at": calibrated_at or datetime.now(timezone.utc).isoformat(),
        }
        if true_peak_dbtp is not None:
            entry["true_peak_dbtp"] = round(float(true_peak_dbtp), 2)
        self._data[key] = entry

    def list_missing(self, patch_names: list[str]) -> list[str]:
        missing: list[str] = []
        seen: set[str] = set()
        for name in patch_names:
            key = self.patch_key(name)
            if key in seen:
                continue
            seen.add(key)
            entry = self._data.get(key)
            if not entry or entry.get("gain_db") is None:
                missing.append(key)
        return sorted(missing)

    def count_missing(self, patch_names: list[str]) -> tuple[int, int]:
        """Return (missing_count, unique_patch_count)."""
        keys: list[str] = []
        seen: set[str] = set()
        for name in patch_names:
            key = self.patch_key(name)
            if key not in seen:
                seen.add(key)
                keys.append(key)
        missing = self.list_missing(keys)
        return len(missing), len(keys)


def log_missing_normalization_summary(
    patch_names: list[str],
    store: PatchNormalizationStore | None = None,
) -> None:
    """Lightweight scan-complete log — no rendering."""
    store = store or PatchNormalizationStore()
    missing_count, total = store.count_missing(patch_names)
    if total == 0:
        return
    if missing_count == 0:
        print(f"Patch normalization: all {total} patches have calibration entries")
    else:
        print(
            f"Patch normalization: {missing_count} of {total} patches missing calibration "
            f"(run scripts/calibrate-patch-normalization.py --favorites-only)"
        )
