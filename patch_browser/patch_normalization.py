"""Per-patch volume normalization — static calibration store and helpers."""

from __future__ import annotations

import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from patch_browser.json_store import atomic_write_json, read_json_dict

# Integrated LUFS target for relative loudness matching across patches.
TARGET_LUFS = -18.0

# After applying gain, the gesture's true peak should land ~3 dB below clip (0 dBFS).
# Sound-engineer spec: normalize close to 0 with headroom, not open-ended LUFS boost.
SAFE_PEAK_DBTP = -3.0

# Surge OSC /param/*/amp/volume ceiling (Pi headroom / xrun guard).
MAX_AMP_VOLUME_LINEAR = 1.5

# Runtime cap when per-patch normalization is active. Was 0.85 — but that clamped away
# almost all of the calibrated gain for genuinely quiet patches (e.g. Acid needs +16.6dB /
# 6.78x linear; 0.85 capped it to roughly unity, giving <1.5dB difference between norm on
# and off). Raised to match the norm-off ceiling (MAX_AMP_VOLUME_LINEAR) — same Surge
# amp/volume range either way, pending Pi xrun/CPU test under dense MPE polyphony
# (2026-08-01, see PATCH_NORMALIZATION.md).
NORM_MAX_AMP_VOLUME_LINEAR = MAX_AMP_VOLUME_LINEAR

# Per-patch manual level slider range (dB gain sent to Surge amp/volume).
NORM_GAIN_DB_MIN = -12.0
NORM_GAIN_DB_MAX = 24.0


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


def volume_fader_display_pct(
    trim: float,
    *,
    fader_min: float,
    fader_max: float,
) -> int:
    """Map fader value to 0–100 for the touch UI label."""
    span = fader_max - fader_min
    if span <= 0:
        return 100
    ratio = (trim - fader_min) / span
    return round(max(0.0, min(1.0, ratio)) * 100)


def volume_fader_to_amp_linear(
    trim: float,
    *,
    patch_gain_linear: float,
    cap: float,
    fader_min: float,
    fader_max: float,
    norm_active: bool = False,
) -> float:
    """Map Vol fader position to Surge amp/volume with even dB steps across travel."""
    if norm_active:
        # Peak-safe gain_db is computed at calibration — do not re-clamp here (#31 Stage 1).
        eff_max = max(0.0, float(patch_gain_linear))
    else:
        eff_max = min(patch_gain_linear, cap)
    eff_min = eff_max * fader_min
    if eff_max <= 0:
        return 0.0
    if eff_min <= 0:
        eff_min = eff_max * 0.001

    span = fader_max - fader_min
    if span <= 0:
        return eff_max
    t = (trim - fader_min) / span
    t = max(0.0, min(1.0, t))
    log_min = math.log(eff_min)
    log_max = math.log(eff_max)
    return math.exp(log_min + t * (log_max - log_min))


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


def compute_gain_db_dual_anchor(
    strike_lufs: float,
    strike_peak_dbtp: float,
    sustain_lufs: float,
    sustain_peak_dbtp: float,
    *,
    target_lufs: float = TARGET_LUFS,
    safe_peak_dbtp: float = SAFE_PEAK_DBTP,
) -> float:
    """Gain from strike-led and sustain-led anchors — max so both land safely (#31 Stage 2)."""
    strike_gain = compute_gain_db(
        strike_lufs, strike_peak_dbtp, target_lufs=target_lufs, safe_peak_dbtp=safe_peak_dbtp
    )
    sustain_gain = compute_gain_db(
        sustain_lufs, sustain_peak_dbtp, target_lufs=target_lufs, safe_peak_dbtp=safe_peak_dbtp
    )
    return max(strike_gain, sustain_gain)


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
            for key, entry in read_json_dict(starter, label="normalization file").items():
                if key == _GLOBAL_SETTINGS_KEY:
                    continue
                if isinstance(entry, dict):
                    merged[key] = dict(entry)

        if self.path.exists():
            for key, entry in read_json_dict(self.path, label="normalization file").items():
                if key == _GLOBAL_SETTINGS_KEY:
                    if isinstance(entry, dict) and "enabled" in entry:
                        self._global_enabled = bool(entry["enabled"])
                    continue
                if isinstance(entry, dict):
                    merged[key] = _merge_patch_entry(merged.get(key), entry)

        self._data = merged

    def save(self) -> None:
        """Persist store atomically so cancel/interrupt mid-write cannot truncate JSON."""
        payload: dict[str, Any] = dict(self._data)
        payload[_GLOBAL_SETTINGS_KEY] = {"enabled": self._global_enabled}
        atomic_write_json(self.path, payload)

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

    def _ensure_calibration_fields(self, key: str, entry: dict[str, Any]) -> dict[str, Any]:
        """Keep gain_db from repo starter when persisting enable toggles."""
        if entry.get("gain_db") is not None:
            return entry
        starter = repo_starter_path()
        if not starter.exists():
            return entry
        starter_entry = read_json_dict(starter, label="normalization file").get(key)
        if isinstance(starter_entry, dict):
            return _merge_patch_entry(starter_entry, entry)
        return entry

    def set_enabled(self, patch_name: str, enabled: bool) -> None:
        """Persist per-patch normalization on/off (issue #5 UI toggle)."""
        key = self.patch_key(patch_name)
        entry = self._data.get(key)
        if not isinstance(entry, dict):
            entry = {}
        entry = self._ensure_calibration_fields(key, entry)
        entry["enabled"] = enabled
        self._data[key] = entry
        self.save()

    def get_raw_gain_db(self, patch_name: str) -> float | None:
        """Calibration gain for a patch, ignoring the enabled flag."""
        return self.get_calibrated_gain_db(patch_name)

    def get_calibrated_gain_db(self, patch_name: str) -> float | None:
        """System-calibrated gain_db only (double-tap slider reset target)."""
        entry = self.get_entry(patch_name)
        if not entry:
            return None
        gain = entry.get("gain_db")
        if gain is None:
            return None
        return float(gain)

    def get_effective_gain_db(self, patch_name: str) -> float | None:
        """Runtime gain: user_gain_db when set, else calibrated gain_db."""
        entry = self.get_entry(patch_name)
        if not entry:
            return None
        if "user_gain_db" in entry:
            return float(entry["user_gain_db"])
        return self.get_calibrated_gain_db(patch_name)

    def get_slider_default_gain_db(self, patch_name: str) -> float:
        """Slider double-tap reset — calibrated gain, or 0 dB when uncalibrated."""
        calibrated = self.get_calibrated_gain_db(patch_name)
        return calibrated if calibrated is not None else 0.0

    def has_user_gain_override(self, patch_name: str) -> bool:
        entry = self.get_entry(patch_name)
        return bool(entry and "user_gain_db" in entry)

    def set_user_gain_db(self, patch_name: str, gain_db: float, *, persist: bool = True) -> None:
        """Set manual per-patch level override (defer persist=False while dragging)."""
        key = self.patch_key(patch_name)
        entry = self._data.get(key)
        if not isinstance(entry, dict):
            entry = {}
        entry = self._ensure_calibration_fields(key, entry)
        entry["user_gain_db"] = round(float(gain_db), 3)
        self._data[key] = entry
        if persist:
            self.save()

    def clear_user_gain_db(self, patch_name: str, *, persist: bool = True) -> None:
        """Remove manual override; runtime reverts to calibrated gain_db."""
        key = self.patch_key(patch_name)
        entry = self.get_entry(patch_name)
        if not entry or "user_gain_db" not in entry:
            return
        updated = dict(entry)
        del updated["user_gain_db"]
        self._data[key] = updated
        if persist:
            self.save()

    def get_gain_db(self, patch_name: str) -> float | None:
        if not self.is_effectively_enabled(patch_name):
            return None
        return self.get_effective_gain_db(patch_name)

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
        strike_lufs: float | None = None,
        sustain_lufs: float | None = None,
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
        if strike_lufs is not None:
            entry["strike_lufs"] = round(float(strike_lufs), 2)
        if sustain_lufs is not None:
            entry["sustain_lufs"] = round(float(sustain_lufs), 2)
        if existing and "user_gain_db" in existing:
            entry["user_gain_db"] = existing["user_gain_db"]
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
