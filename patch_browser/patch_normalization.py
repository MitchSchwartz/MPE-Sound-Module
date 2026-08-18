"""Per-patch volume normalization — static calibration store and helpers."""

from __future__ import annotations

import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from patch_browser.json_store import atomic_write_json, read_json_dict
from patch_browser.patch_sidecar_store import SidecarKeyMixin

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

# v2 Norm fader: trim offset from calibrated gain_db (-24..+12 dB; 0 = cal baseline).
NORM_TRIM_DB_MIN = -24.0
NORM_TRIM_DB_MAX = 12.0

# Legacy aliases -- NormControl uses trim range; keep names for import stability.
NORM_GAIN_DB_MIN = NORM_TRIM_DB_MIN
NORM_GAIN_DB_MAX = NORM_TRIM_DB_MAX

# Closed-loop cal verify: post-gain peak must land at or below SAFE_PEAK + tolerance.
POST_GAIN_VERIFY_PEAK_MAX_DBTP = SAFE_PEAK_DBTP + 0.2

VOL_FADER_LAW_CONSOLE = "console"
VOL_FADER_LAW_LINEAR = "linear"


def post_gain_verify_passes(peak_dbtp: float) -> bool:
    """True when closed-loop post-gain peak is finite and within v2 save gate."""
    return math.isfinite(peak_dbtp) and peak_dbtp <= POST_GAIN_VERIFY_PEAK_MAX_DBTP



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


# Vol fader attenuation display/range: bottom = mute, top = 0 dB (unity vs patch baseline).
VOLUME_FADER_FLOOR_DB = -60.0

# IEC 60268-17 console fader law, as position(%) -> dB breakpoints. A fader that is
# linear in dB over a 60 dB span puts everything musically useful (0 to -12 dB) in the
# top 20% of travel and wastes the bottom half below -30 dB, which reads as a cliff on
# a short touchscreen column. The console law spends half the travel on the top 20 dB.
# Segments are (position_fraction, dB_at_that_position), ascending.
_IEC_FADER_POINTS: tuple[tuple[float, float], ...] = (
    (0.000, -70.0),
    (0.025, -60.0),
    (0.075, -50.0),
    (0.150, -40.0),
    (0.300, -30.0),
    (0.500, -20.0),
    (1.000, 0.0),
)
_IEC_FADER_BOTTOM_DB = _IEC_FADER_POINTS[0][1]


def _iec_fader_db(t: float) -> float:
    """Console-law attenuation in dB for fader travel *t* (0..1), bottoming at -70 dB."""
    if t <= 0.0:
        return _IEC_FADER_BOTTOM_DB
    if t >= 1.0:
        return 0.0
    for (t_lo, db_lo), (t_hi, db_hi) in zip(_IEC_FADER_POINTS, _IEC_FADER_POINTS[1:]):
        if t <= t_hi:
            span = t_hi - t_lo
            if span <= 0:
                return db_hi
            return db_lo + (db_hi - db_lo) * ((t - t_lo) / span)
    return 0.0


def _volume_fader_t(
    trim: float,
    *,
    fader_min: float,
    fader_max: float,
) -> float:
    span = fader_max - fader_min
    if span <= 0:
        return 1.0
    return max(0.0, min(1.0, (trim - fader_min) / span))


def volume_fader_law() -> str:
    """Vol fader taper — env ``MPE_VOL_FADER_LAW`` (``console`` default, or ``linear``)."""
    raw = os.environ.get("MPE_VOL_FADER_LAW", VOL_FADER_LAW_CONSOLE).strip().lower()
    if raw == VOL_FADER_LAW_LINEAR:
        return VOL_FADER_LAW_LINEAR
    return VOL_FADER_LAW_CONSOLE


def volume_fader_trim_to_db(
    trim: float,
    *,
    fader_min: float,
    fader_max: float,
    floor_db: float = VOLUME_FADER_FLOOR_DB,
    law: str | None = None,
) -> float | None:
    """Attenuation in dB relative to full patch level. None when fader is at mute.

    Console (IEC 60268-17) taper by default; ``linear`` spreads dB evenly over travel
    (``MPE_VOL_FADER_LAW=linear``).
    """
    if trim <= fader_min:
        return None
    t = _volume_fader_t(trim, fader_min=fader_min, fader_max=fader_max)
    if t <= 0.0:
        return None
    if (law or volume_fader_law()) == VOL_FADER_LAW_LINEAR:
        return floor_db * (1.0 - t)
    return _iec_fader_db(t) * (floor_db / _IEC_FADER_BOTTOM_DB)


def volume_fader_display_db(
    trim: float,
    *,
    fader_min: float,
    fader_max: float,
    floor_db: float = VOLUME_FADER_FLOOR_DB,
) -> str:
    """Touch Vol fader label: -∞ at bottom, 0 at top."""
    db = volume_fader_trim_to_db(
        trim, fader_min=fader_min, fader_max=fader_max, floor_db=floor_db
    )
    if db is None:
        return "-∞"
    if abs(db) < 0.05:
        return "0"
    return f"{db:.0f}"


def volume_fader_display_pct(
    trim: float,
    *,
    fader_min: float,
    fader_max: float,
) -> int:
    """Legacy 0–100 display — prefer volume_fader_display_db for the Vol fader."""
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
    floor_db: float = VOLUME_FADER_FLOOR_DB,
) -> float:
    """Map Vol fader to Surge amp/volume: mute at bottom, 0 dB trim at top."""
    if trim <= fader_min:
        return 0.0
    if norm_active:
        # Peak-safe gain_db is computed at calibration — do not re-clamp here (#31 Stage 1).
        eff_max = max(0.0, float(patch_gain_linear))
    else:
        eff_max = min(patch_gain_linear, cap)
    if eff_max <= 0:
        return 0.0

    db = volume_fader_trim_to_db(
        trim, fader_min=fader_min, fader_max=fader_max, floor_db=floor_db
    )
    if db is None:
        return 0.0
    return eff_max * db_to_linear(db)


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
    """Gain from strike + sustain anchors — min so neither anchor clips (#31 Stage 2).

    max() was wrong when strike is already hot and sustain is quiet (e.g. A Robotic Mind):
    boosting for sustain clips the strike. min() is peak-safe for both with one gain_db.
    """
    strike_gain = compute_gain_db(
        strike_lufs, strike_peak_dbtp, target_lufs=target_lufs, safe_peak_dbtp=safe_peak_dbtp
    )
    sustain_gain = compute_gain_db(
        sustain_lufs, sustain_peak_dbtp, target_lufs=target_lufs, safe_peak_dbtp=safe_peak_dbtp
    )
    return min(strike_gain, sustain_gain)


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


def clamp_user_trim_db(trim_db: float) -> float:
    """Clamp Norm trim offset to the v2 fader range."""
    return max(NORM_TRIM_DB_MIN, min(NORM_TRIM_DB_MAX, float(trim_db)))


def _migrate_legacy_user_gain(entry: dict[str, Any]) -> dict[str, Any]:
    """v1 ``user_gain_db`` (absolute) -> v2 ``user_trim_db`` (offset from gain_db)."""
    if "user_gain_db" not in entry:
        return entry
    updated = dict(entry)
    if "user_trim_db" not in updated:
        user_gain = float(updated.pop("user_gain_db"))
        gain_db = updated.get("gain_db")
        if gain_db is not None:
            trim = clamp_user_trim_db(user_gain - float(gain_db))
        else:
            trim = clamp_user_trim_db(user_gain)
        updated["user_trim_db"] = round(trim, 3)
    else:
        updated.pop("user_gain_db", None)
    return updated


# Reserved key in user normalization JSON — master switch, not a patch stem.
_GLOBAL_SETTINGS_KEY = "_global"


class PatchNormalizationStore(SidecarKeyMixin):
    """Load/save patch_normalization.json keyed by stable_key (path-based) with stem fallback."""

    _reserved_keys = frozenset({"_global"})

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

        migrated = False
        for key in list(merged.keys()):
            new_entry = _migrate_legacy_user_gain(merged[key])
            if new_entry != merged[key]:
                merged[key] = new_entry
                migrated = True
        self._data = merged
        if migrated and self.path.exists():
            self.save()

    def save(self) -> None:
        """Persist store atomically so cancel/interrupt mid-write cannot truncate JSON."""
        payload: dict[str, Any] = dict(self._data)
        payload[_GLOBAL_SETTINGS_KEY] = {"enabled": self._global_enabled}
        atomic_write_json(self.path, payload)

    def get_entry(
        self,
        patch_name: str,
        *,
        patch_path: str | None = None,
        stable_key: str | None = None,
    ) -> dict[str, Any] | None:
        entry, _key = self._lookup(
            patch_name, patch_path=patch_path, stable_key=stable_key
        )
        return entry

    def is_globally_enabled(self) -> bool:
        """Master switch — when False, no patch normalization is applied."""
        return self._global_enabled

    def set_globally_enabled(self, enabled: bool) -> None:
        """Persist global normalization on/off without changing per-patch flags."""
        self._global_enabled = bool(enabled)
        self.save()

    def is_enabled(
        self,
        patch_name: str,
        *,
        patch_path: str | None = None,
        stable_key: str | None = None,
    ) -> bool:
        """Per-patch enabled flag (default True when no entry). Ignores global switch."""
        entry = self.get_entry(
            patch_name, patch_path=patch_path, stable_key=stable_key
        )
        if entry is None:
            return True
        if "enabled" not in entry:
            return True
        return bool(entry["enabled"])

    def is_effectively_enabled(
        self,
        patch_name: str,
        *,
        patch_path: str | None = None,
        stable_key: str | None = None,
    ) -> bool:
        """Whether normalization applies at runtime (global + per-patch)."""
        return self.is_globally_enabled() and self.is_enabled(
            patch_name, patch_path=patch_path, stable_key=stable_key
        )

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

    def set_enabled(
        self,
        patch_name: str,
        enabled: bool,
        *,
        patch_path: str | None = None,
        stable_key: str | None = None,
    ) -> None:
        """Persist per-patch normalization on/off (issue #5 UI toggle)."""
        key = self._storage_key(
            patch_name, patch_path=patch_path, stable_key=stable_key
        )
        entry, matched = self._lookup(
            patch_name, patch_path=patch_path, stable_key=stable_key
        )
        if not isinstance(entry, dict):
            entry = {}
        entry = self._ensure_calibration_fields(matched or key, entry)
        entry["enabled"] = enabled
        if matched and matched != key:
            self._data.pop(matched, None)
        self._data[key] = entry
        self.save()

    def get_raw_gain_db(
        self,
        patch_name: str,
        *,
        patch_path: str | None = None,
        stable_key: str | None = None,
    ) -> float | None:
        """Calibration gain for a patch, ignoring the enabled flag."""
        return self.get_calibrated_gain_db(
            patch_name, patch_path=patch_path, stable_key=stable_key
        )

    def get_calibrated_gain_db(
        self,
        patch_name: str,
        *,
        patch_path: str | None = None,
        stable_key: str | None = None,
    ) -> float | None:
        """System-calibrated gain_db only (double-tap slider reset target)."""
        entry = self.get_entry(
            patch_name, patch_path=patch_path, stable_key=stable_key
        )
        if not entry:
            return None
        gain = entry.get("gain_db")
        if gain is None:
            return None
        return float(gain)

    def get_user_trim_db(
        self,
        patch_name: str,
        *,
        patch_path: str | None = None,
        stable_key: str | None = None,
    ) -> float:
        entry = self.get_entry(
            patch_name, patch_path=patch_path, stable_key=stable_key
        )
        if not entry or "user_trim_db" not in entry:
            return 0.0
        return float(entry["user_trim_db"])

    def get_effective_gain_db(
        self,
        patch_name: str,
        *,
        patch_path: str | None = None,
        stable_key: str | None = None,
    ) -> float | None:
        """Runtime gain: calibrated gain_db + user_trim_db offset (v2)."""
        entry = self.get_entry(
            patch_name, patch_path=patch_path, stable_key=stable_key
        )
        if not entry:
            return None
        calibrated = entry.get("gain_db")
        trim = entry.get("user_trim_db")
        if calibrated is None and trim is None:
            return None
        base = float(calibrated) if calibrated is not None else 0.0
        offset = float(trim) if trim is not None else 0.0
        return base + offset

    def get_slider_default_gain_db(
        self,
        patch_name: str,
        *,
        patch_path: str | None = None,
        stable_key: str | None = None,
    ) -> float:
        """Norm fader double-tap reset — 0 dB trim (calibrated baseline)."""
        return 0.0

    def has_user_trim_override(
        self,
        patch_name: str,
        *,
        patch_path: str | None = None,
        stable_key: str | None = None,
    ) -> bool:
        entry = self.get_entry(
            patch_name, patch_path=patch_path, stable_key=stable_key
        )
        return bool(entry and "user_trim_db" in entry)

    def has_user_gain_override(
        self,
        patch_name: str,
        *,
        patch_path: str | None = None,
        stable_key: str | None = None,
    ) -> bool:
        return self.has_user_trim_override(
            patch_name, patch_path=patch_path, stable_key=stable_key
        )

    def set_user_trim_db(
        self,
        patch_name: str,
        trim_db: float,
        *,
        persist: bool = True,
        patch_path: str | None = None,
        stable_key: str | None = None,
    ) -> None:
        """Set Norm trim offset from calibrated gain (defer persist=False while dragging)."""
        key = self._storage_key(
            patch_name, patch_path=patch_path, stable_key=stable_key
        )
        entry, matched = self._lookup(
            patch_name, patch_path=patch_path, stable_key=stable_key
        )
        if not isinstance(entry, dict):
            entry = {}
        entry = self._ensure_calibration_fields(matched or key, entry)
        entry["user_trim_db"] = round(clamp_user_trim_db(trim_db), 3)
        entry.pop("user_gain_db", None)
        if matched and matched != key:
            self._data.pop(matched, None)
        self._data[key] = entry
        if persist:
            self.save()

    def set_user_gain_db(
        self,
        patch_name: str,
        gain_db: float,
        *,
        persist: bool = True,
        patch_path: str | None = None,
        stable_key: str | None = None,
    ) -> None:
        """Legacy v1 API — absolute gain_db converted to trim offset when calibrated."""
        entry = self.get_entry(
            patch_name, patch_path=patch_path, stable_key=stable_key
        )
        calibrated = entry.get("gain_db") if entry else None
        if calibrated is not None:
            trim = float(gain_db) - float(calibrated)
        else:
            trim = float(gain_db)
        self.set_user_trim_db(
            patch_name,
            trim,
            persist=persist,
            patch_path=patch_path,
            stable_key=stable_key,
        )

    def clear_user_trim_db(
        self,
        patch_name: str,
        *,
        persist: bool = True,
        patch_path: str | None = None,
        stable_key: str | None = None,
    ) -> None:
        """Remove trim override; runtime reverts to calibrated gain_db only."""
        entry, key = self._lookup(
            patch_name, patch_path=patch_path, stable_key=stable_key
        )
        if not entry or not key:
            return
        if "user_trim_db" not in entry and "user_gain_db" not in entry:
            return
        updated = dict(entry)
        updated.pop("user_trim_db", None)
        updated.pop("user_gain_db", None)
        storage = self._storage_key(
            patch_name, patch_path=patch_path, stable_key=stable_key
        )
        if key != storage:
            self._data.pop(key, None)
        self._data[storage] = updated
        if persist:
            self.save()

    def clear_user_gain_db(
        self,
        patch_name: str,
        *,
        persist: bool = True,
        patch_path: str | None = None,
        stable_key: str | None = None,
    ) -> None:
        self.clear_user_trim_db(
            patch_name,
            persist=persist,
            patch_path=patch_path,
            stable_key=stable_key,
        )

    def get_gain_db(
        self,
        patch_name: str,
        *,
        patch_path: str | None = None,
        stable_key: str | None = None,
    ) -> float | None:
        if not self.is_effectively_enabled(
            patch_name, patch_path=patch_path, stable_key=stable_key
        ):
            return None
        return self.get_effective_gain_db(
            patch_name, patch_path=patch_path, stable_key=stable_key
        )

    def get_gain_linear(
        self,
        patch_name: str,
        *,
        patch_path: str | None = None,
        stable_key: str | None = None,
    ) -> float:
        gain_db = self.get_gain_db(
            patch_name, patch_path=patch_path, stable_key=stable_key
        )
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
        post_gain_peak_dbtp: float | None = None,
        cal_route: str | None = None,
        strike_lufs: float | None = None,
        sustain_lufs: float | None = None,
        patch_path: str | None = None,
        stable_key: str | None = None,
    ) -> None:
        key = self._storage_key(
            patch_name, patch_path=patch_path, stable_key=stable_key
        )
        existing, matched = self._lookup(
            patch_name, patch_path=patch_path, stable_key=stable_key
        )
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
        if post_gain_peak_dbtp is not None:
            entry["post_gain_peak_dbtp"] = round(float(post_gain_peak_dbtp), 2)
        if cal_route is not None:
            entry["cal_route"] = cal_route
        if strike_lufs is not None:
            entry["strike_lufs"] = round(float(strike_lufs), 2)
        if sustain_lufs is not None:
            entry["sustain_lufs"] = round(float(sustain_lufs), 2)
        if existing:
            if "user_trim_db" in existing:
                entry["user_trim_db"] = existing["user_trim_db"]
            elif "user_gain_db" in existing:
                entry = _migrate_legacy_user_gain(
                    {**entry, "user_gain_db": existing["user_gain_db"]}
                )
        if matched and matched != key:
            self._data.pop(matched, None)
        self._data[key] = entry

    def list_missing(self, patches: list[dict]) -> list[str]:
        missing: list[str] = []
        seen: set[str] = set()
        for patch in patches:
            key = self._storage_key(
                patch["name"],
                patch_path=patch.get("path"),
                stable_key=patch.get("stable_key"),
            )
            if key in seen:
                continue
            seen.add(key)
            entry = self.get_entry(
                patch["name"],
                patch_path=patch.get("path"),
                stable_key=patch.get("stable_key"),
            )
            if not entry or entry.get("gain_db") is None:
                missing.append(key)
        return sorted(missing)

    def count_missing(self, patches: list[dict]) -> tuple[int, int]:
        """Return (missing_count, unique_patch_count)."""
        missing = self.list_missing(patches)
        seen: set[str] = set()
        for patch in patches:
            key = self._storage_key(
                patch["name"],
                patch_path=patch.get("path"),
                stable_key=patch.get("stable_key"),
            )
            seen.add(key)
        return len(missing), len(seen)


def log_missing_normalization_summary(
    patches: list[dict],
    store: PatchNormalizationStore | None = None,
) -> None:
    """Lightweight scan-complete log — no rendering."""
    store = store or PatchNormalizationStore()
    missing_count, total = store.count_missing(patches)
    if total == 0:
        return
    if missing_count == 0:
        print(f"Patch normalization: all {total} patches have calibration entries")
    else:
        print(
            f"Patch normalization: {missing_count} of {total} patches missing calibration "
            f"(run scripts/calibrate-patch-normalization.py or System → Calibrate missing patches)"
        )
