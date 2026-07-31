"""Per-patch volume normalization — static calibration store and helpers."""

from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Integrated LUFS target for relative loudness matching across patches.
TARGET_LUFS = -18.0

# After applying gain, the gesture's true peak should land ~3 dB below clip (0 dBFS).
# Sound-engineer spec: normalize close to 0 with headroom, not open-ended LUFS boost.
SAFE_PEAK_DBTP = -3.0

# Surge OSC /param/*/amp/volume ceiling — matches touch browser VOLUME_MAX (1.5).
MAX_AMP_VOLUME_LINEAR = 1.5

# Lower runtime cap when per-patch normalization is active. Heavy MPE polyphony on the Pi
# xruns before clip: boosted amp gain raises per-voice CPU and earlier buffer stress.
# Tradeoff: normalized patches run quieter; user trim can compensate for solo playing.
NORM_MAX_AMP_VOLUME_LINEAR = 0.85


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

    Uses integrated LUFS for relative matching across patches, but caps gain so
    the boosted peak lands at safe_peak_dbtp (~-3 dBFS headroom before clip).
    Attenuation-only patches use the LUFS delta; loud patches are limited by peak cap.
    """
    lufs_gain = target_lufs - lufs_integrated
    peak_gain = safe_peak_dbtp - true_peak_dbtp
    return min(lufs_gain, peak_gain)


def _read_json_dict(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Warning: could not load normalization file {path}: {exc}")
        return {}
    return raw if isinstance(raw, dict) else {}


def _merge_patch_entry(
    base: dict[str, Any] | None,
    overlay: dict[str, Any],
) -> dict[str, Any]:
    """Field-wise merge so user toggles do not drop calibration fields."""
    if not isinstance(base, dict):
        return dict(overlay)
    merged = dict(base)
    merged.update(overlay)
    return merged


# Reserved key in user normalization JSON — master switch, not a patch stem.
_GLOBAL_SETTINGS_KEY = "_global"


class PatchNormalizationStore:
    """Load/save patch_normalization.json keyed by patch name (stem)."""

    def __init__(self, path: Path | None = None):
        self.path = path or default_normalization_path()
        self._data: dict[str, dict[str, Any]] = {}
        self._global_enabled = True
        self.load()

    def load(self) -> None:
        """Load repo starter, then overlay user state so toggles keep calibration."""
        merged: dict[str, dict[str, Any]] = {}

        starter = repo_starter_path()
        if starter.exists():
            for key, entry in _read_json_dict(starter).items():
                if key == _GLOBAL_SETTINGS_KEY:
                    continue
                if isinstance(entry, dict):
                    merged[key] = dict(entry)

        if self.path.exists():
            for key, entry in _read_json_dict(self.path).items():
                if key == _GLOBAL_SETTINGS_KEY:
                    if isinstance(entry, dict) and "enabled" in entry:
                        self._global_enabled = bool(entry["enabled"])
                    continue
                if isinstance(entry, dict):
                    merged[key] = _merge_patch_entry(merged.get(key), entry)

        self._data = merged

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = dict(self._data)
        payload[_GLOBAL_SETTINGS_KEY] = {"enabled": self._global_enabled}
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

    @staticmethod
    def patch_key(patch_name: str) -> str:
        return Path(patch_name).stem

    def get_entry(self, patch_name: str) -> dict[str, Any] | None:
        entry = self._data.get(self.patch_key(patch_name))
        return entry if isinstance(entry, dict) else None

    def is_globally_enabled(self) -> bool:
        """Master switch — when False, no patch normalization is applied."""
        return self._global_enabled

    def set_globally_enabled(self, enabled: bool) -> None:
        """Persist global normalization on/off without changing per-patch flags."""
        self._global_enabled = bool(enabled)
        self.save()

    def is_enabled(self, patch_name: str) -> bool:
        """Per-patch enabled flag (default True when no entry). Ignores global switch."""
        entry = self.get_entry(patch_name)
        if entry is None:
            return True
        if "enabled" not in entry:
            return True
        return bool(entry["enabled"])

    def is_effectively_enabled(self, patch_name: str) -> bool:
        """Whether normalization applies at runtime (global + per-patch)."""
        return self.is_globally_enabled() and self.is_enabled(patch_name)

    def set_enabled(self, patch_name: str, enabled: bool) -> None:
        """Persist per-patch normalization on/off (issue #5 UI toggle)."""
        key = self.patch_key(patch_name)
        entry = self._data.get(key)
        if isinstance(entry, dict):
            entry["enabled"] = enabled
        else:
            self._data[key] = {"enabled": enabled}
        self.save()

    def get_raw_gain_db(self, patch_name: str) -> float | None:
        """Calibration gain for a patch, ignoring the enabled flag."""
        entry = self.get_entry(patch_name)
        if not entry:
            return None
        gain = entry.get("gain_db")
        if gain is None:
            return None
        return float(gain)

    def get_gain_db(self, patch_name: str) -> float | None:
        if not self.is_effectively_enabled(patch_name):
            return None
        return self.get_raw_gain_db(patch_name)

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
        enabled: bool | None = None,
        calibrated_at: str | None = None,
        true_peak_dbtp: float | None = None,
    ) -> None:
        key = self.patch_key(patch_name)
        existing = self.get_entry(patch_name)
        if enabled is None:
            if existing is None or "enabled" not in existing:
                enabled_value: bool = True
            else:
                enabled_value = bool(existing["enabled"])
        else:
            enabled_value = enabled

        entry: dict[str, Any] = {
            "gain_db": round(float(gain_db), 3),
            "enabled": enabled_value,
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
            f"(run scripts/calibrate-patch-normalization.py or System → Calibrate missing patches)"
        )
