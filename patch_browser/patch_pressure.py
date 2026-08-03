"""Per-patch MPE pressure floor — light touch vs full press (issue #29 / #31).

Touch fader semantics (canon): docs/TOUCH_PATCH_BROWSER.md §Mixer faders.
Implementation: cal_floor_to_touch_anchor + user_touch_offset → touch_fader_value.
Do not describe Touch as "0 at center like Tail" — Tail is offset-centered; Touch is cal-anchored.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from patch_browser.json_store import atomic_write_json, read_json_dict

# Floor at zero pressure: mult(0)=floor, mult(1)=1.0 always.
PRESSURE_FLOOR_MIN = 0.0
PRESSURE_FLOOR_MAX = 0.9
DEFAULT_PRESSURE_FLOOR = 0.0

# Touch fader UI: −50…+50 scale; cal floor anchors handle, trim moves from there.
TOUCH_FADER_MIN = -50.0
TOUCH_FADER_MAX = 50.0
TOUCH_CAL_DISPLAY_MAX = 50.0
TOUCH_DISPLAY_MIN = TOUCH_FADER_MIN
TOUCH_DISPLAY_MAX = TOUCH_FADER_MAX
TOUCH_DISPLAY_CLEAR_EPSILON = 1.0

# User trim stored as offset from calibrated baseline (internal floor units).
PRESSURE_OFFSET_MIN = -PRESSURE_FLOOR_MAX
PRESSURE_OFFSET_MAX = PRESSURE_FLOOR_MAX
TOUCH_OFFSET_CLEAR_EPSILON = 0.01

# Light-touch calibration gesture (7-bit MPE pressure held constant).
LIGHT_TOUCH_PRESSURE = 25
LIGHT_TOUCH_HOLD_SECONDS = 1.5
LIGHT_TOUCH_GESTURE_SECONDS = 2.5

# When batch size is too small for a stable cohort median, align to this LUFS target.
LIGHT_TOUCH_TARGET_LUFS = -28.0

# dB shortfall at light touch mapped to floor=1.0 (empirical; tune on Pi).
FLOOR_DB_PER_UNIT = 18.0

# Strike vs sustain spread — patches with huge dynamic range get extra floor (#31 Stage 3).
EXPRESSION_GAP_DB_THRESHOLD = 12.0
GAP_FLOOR_DB_PER_UNIT = 24.0

LIVE_STATE_FILE = Path.home() / ".patch_browser_pressure_live.json"


def default_pressure_path() -> Path:
    env = os.environ.get("MPE_PRESSURE_FILE", "").strip()
    if env:
        return Path(env).expanduser()
    return Path.home() / ".patch_browser_pressure.json"


def effective_pressure_mult(raw: float, floor: float) -> float:
    """Map normalized pressure p∈[0,1] with floor pinned at full press."""
    p = max(0.0, min(1.0, float(raw)))
    f = clamp_effective_floor(floor)
    return f + (1.0 - f) * p


def clamp_effective_floor(floor: float) -> float:
    return max(PRESSURE_FLOOR_MIN, min(PRESSURE_FLOOR_MAX, float(floor)))


def clamp_touch_display(display: float) -> float:
    return max(TOUCH_DISPLAY_MIN, min(TOUCH_DISPLAY_MAX, float(display)))


def cal_floor_to_touch_anchor(floor: float) -> float:
    """Map calibrated floor (0…0.9) to anchor on fader (0…+50). Zero cal → 0."""
    f = clamp_effective_floor(floor)
    if PRESSURE_FLOOR_MAX <= 0:
        return 0.0
    return (f / PRESSURE_FLOOR_MAX) * TOUCH_CAL_DISPLAY_MAX


def offset_to_touch_trim(offset: float) -> float:
    """Map user trim (−0.9…+0.9 floor units) to ±50 fader delta."""
    o = clamp_touch_offset(offset)
    if PRESSURE_FLOOR_MAX <= 0:
        return 0.0
    return o / PRESSURE_FLOOR_MAX * TOUCH_FADER_MAX


def touch_trim_to_offset(trim: float) -> float:
    """Map ±50 fader delta back to user trim offset."""
    t = max(-TOUCH_FADER_MAX, min(TOUCH_FADER_MAX, float(trim)))
    if TOUCH_FADER_MAX == 0:
        return 0.0
    return clamp_touch_offset(t / TOUCH_FADER_MAX * PRESSURE_FLOOR_MAX)


def touch_fader_value(baseline: float, offset: float) -> float:
    """Fader position: cal anchor + user trim, clamped to −50…+50."""
    anchor = cal_floor_to_touch_anchor(baseline)
    trim = offset_to_touch_trim(offset)
    return max(TOUCH_FADER_MIN, min(TOUCH_FADER_MAX, anchor + trim))


def touch_fader_to_offset(display: float, baseline: float) -> float:
    """Derive user trim from fader position relative to cal anchor."""
    d = max(TOUCH_FADER_MIN, min(TOUCH_FADER_MAX, float(display)))
    anchor = cal_floor_to_touch_anchor(baseline)
    return touch_trim_to_offset(d - anchor)


def clamp_touch_offset(offset: float) -> float:
    return max(PRESSURE_OFFSET_MIN, min(PRESSURE_OFFSET_MAX, float(offset)))


def effective_floor_from_offset(baseline: float, offset: float) -> float:
    """Apply user Touch trim relative to calibrated baseline."""
    return clamp_effective_floor(float(baseline) + clamp_touch_offset(offset))


def compute_pressure_floor(lufs_light: float, target_lufs_light: float) -> float:
    """Derive calibrated floor from how quiet light touch is vs cohort target."""
    shortfall_db = float(target_lufs_light) - float(lufs_light)
    if shortfall_db <= 0.5:
        return DEFAULT_PRESSURE_FLOOR
    floor = shortfall_db / FLOOR_DB_PER_UNIT
    return max(PRESSURE_FLOOR_MIN, min(PRESSURE_FLOOR_MAX, floor))


def compute_touch_calibration_floor(
    lufs_light: float,
    target_lufs_light: float,
    lufs_strike: float,
    lufs_sustain: float,
) -> float:
    """Light-touch cohort alignment + strike/sustain gap lift (#31 Stage 3)."""
    from_light = compute_pressure_floor(lufs_light, target_lufs_light)
    gap_db = float(lufs_sustain) - float(lufs_strike)
    from_gap = DEFAULT_PRESSURE_FLOOR
    if gap_db > EXPRESSION_GAP_DB_THRESHOLD:
        from_gap = (gap_db - EXPRESSION_GAP_DB_THRESHOLD) / GAP_FLOOR_DB_PER_UNIT
        from_gap = max(PRESSURE_FLOOR_MIN, min(PRESSURE_FLOOR_MAX, from_gap))
    return max(from_light, from_gap)


def resolve_light_touch_target(lufs_light_values: list[float]) -> float:
    """Cohort median for batches; fixed target for single-patch runs."""
    if len(lufs_light_values) >= 2:
        sorted_vals = sorted(float(v) for v in lufs_light_values)
        mid = len(sorted_vals) // 2
        if len(sorted_vals) % 2:
            return sorted_vals[mid]
        return (sorted_vals[mid - 1] + sorted_vals[mid]) / 2.0
    return LIGHT_TOUCH_TARGET_LUFS


def remap_pressure_7bit(value: int, floor: float) -> int:
    p = max(0, min(127, int(value))) / 127.0
    out = effective_pressure_mult(p, floor) * 127.0
    return max(0, min(127, int(round(out))))


class PatchPressureStore:
    """Persist per-patch pressure floor overrides."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_pressure_path()
        self._data: dict[str, dict[str, Any]] = {}
        self.load()

    def load(self) -> None:
        self._data = {}
        if self.path.exists():
            for key, entry in read_json_dict(self.path, label="pressure file").items():
                if isinstance(entry, dict):
                    self._data[key] = dict(entry)

    def save(self) -> None:
        atomic_write_json(self.path, self._data)

    @staticmethod
    def patch_key(patch_name: str) -> str:
        return Path(patch_name).stem

    def get_user_floor(self, patch_name: str) -> float | None:
        """Legacy absolute override — prefer get_user_touch_offset()."""
        entry = self._data.get(self.patch_key(patch_name))
        if not entry:
            return None
        val = entry.get("user_floor")
        if isinstance(val, (int, float)):
            return float(val)
        return None

    def get_user_touch_offset(self, patch_name: str) -> float:
        """User trim relative to calibrated baseline; 0 = no override (fader at cal anchor)."""
        key = self.patch_key(patch_name)
        entry = self._data.get(key)
        if not entry:
            return 0.0
        val = entry.get("user_touch_offset")
        if isinstance(val, (int, float)):
            return clamp_touch_offset(float(val))
        legacy = entry.get("user_floor")
        if isinstance(legacy, (int, float)):
            baseline = self.get_calibrated_baseline(patch_name)
            return clamp_touch_offset(float(legacy) - baseline)
        return 0.0

    def has_user_touch_override(self, patch_name: str) -> bool:
        entry = self._data.get(self.patch_key(patch_name))
        if not entry:
            return False
        if "user_touch_offset" in entry:
            return abs(clamp_touch_offset(float(entry["user_touch_offset"]))) >= TOUCH_OFFSET_CLEAR_EPSILON
        return "user_floor" in entry

    def get_calibrated_floor(self, patch_name: str) -> float | None:
        entry = self._data.get(self.patch_key(patch_name))
        if not entry:
            return None
        val = entry.get("cal_floor")
        if isinstance(val, (int, float)):
            return float(val)
        return None

    def get_slider_default_floor(self, patch_name: str) -> float:
        return self.get_calibrated_baseline(patch_name)

    def get_calibrated_baseline(self, patch_name: str) -> float:
        calibrated = self.get_calibrated_floor(patch_name)
        if calibrated is not None:
            return clamp_effective_floor(calibrated)
        return DEFAULT_PRESSURE_FLOOR

    def has_user_floor_override(self, patch_name: str) -> bool:
        return self.has_user_touch_override(patch_name)

    def get_effective_floor(self, patch_name: str) -> float:
        baseline = self.get_calibrated_baseline(patch_name)
        if not self.has_user_touch_override(patch_name):
            return baseline
        return effective_floor_from_offset(baseline, self.get_user_touch_offset(patch_name))

    def set_user_touch_offset(
        self, patch_name: str, offset: float, *, persist: bool = True
    ) -> None:
        key = self.patch_key(patch_name)
        clamped = clamp_touch_offset(offset)
        if abs(clamped) < TOUCH_OFFSET_CLEAR_EPSILON:
            self.clear_user_touch_offset(patch_name, persist=persist)
            return
        entry = dict(self._data.get(key) or {})
        entry["user_touch_offset"] = round(clamped, 3)
        entry.pop("user_floor", None)
        self._data[key] = entry
        if persist:
            self.save()

    def clear_user_touch_offset(self, patch_name: str, *, persist: bool = True) -> None:
        key = self.patch_key(patch_name)
        entry = self._data.get(key)
        if not entry:
            return
        if "user_touch_offset" not in entry and "user_floor" not in entry:
            return
        entry = dict(entry)
        entry.pop("user_touch_offset", None)
        entry.pop("user_floor", None)
        if entry:
            self._data[key] = entry
        else:
            self._data.pop(key, None)
        if persist:
            self.save()

    def set_user_floor(self, patch_name: str, floor: float, *, persist: bool = True) -> None:
        """Legacy absolute API — stores as offset from calibrated baseline."""
        baseline = self.get_calibrated_baseline(patch_name)
        self.set_user_touch_offset(patch_name, float(floor) - baseline, persist=persist)

    def clear_user_floor(self, patch_name: str, *, persist: bool = True) -> None:
        self.clear_user_touch_offset(patch_name, persist=persist)

    def set_calibration(
        self,
        patch_name: str,
        cal_floor: float,
        lufs_light: float,
        *,
        calibrated_at: str | None = None,
    ) -> None:
        """Write system-calibrated floor; preserve user_floor override if present."""
        from datetime import datetime, timezone

        key = self.patch_key(patch_name)
        existing = self._data.get(key)
        clamped = max(PRESSURE_FLOOR_MIN, min(PRESSURE_FLOOR_MAX, float(cal_floor)))
        entry: dict[str, Any] = {
            "cal_floor": round(clamped, 3),
            "lufs_light": round(float(lufs_light), 2),
            "calibrated_at": calibrated_at or datetime.now(timezone.utc).isoformat(),
        }
        if existing and "user_touch_offset" in existing:
            entry["user_touch_offset"] = existing["user_touch_offset"]
        elif existing and "user_floor" in existing:
            baseline = clamp_effective_floor(clamped)
            entry["user_touch_offset"] = round(
                clamp_touch_offset(float(existing["user_floor"]) - baseline),
                3,
            )
        self._data[key] = entry

    def format_floor(self, floor: float) -> str:
        """Absolute effective floor as whole-percent label (legacy)."""
        return f"{round(clamp_effective_floor(floor) * 100)}"

    def format_touch_display(self, display: float) -> str:
        """Touch fader label on the −50…+50 trim scale."""
        pts = round(clamp_touch_display(display))
        if pts > 0:
            return f"+{pts}"
        return str(pts)

    def format_touch_offset(self, offset: float) -> str:
        """Trim component label on ±50 scale."""
        pts = round(offset_to_touch_trim(offset))
        if pts > 0:
            return f"+{pts}"
        return str(pts)

    def write_live_state(self, patch_name: str, floor: float | None = None) -> None:
        """Signal the MIDI remapper daemon (read on each message / mtime)."""
        eff = self.get_effective_floor(patch_name) if floor is None else clamp_effective_floor(floor)
        payload = {
            "patch": self.patch_key(patch_name),
            "floor": eff,
        }
        atomic_write_json(LIVE_STATE_FILE, payload)

    @staticmethod
    def read_live_floor(default: float = DEFAULT_PRESSURE_FLOOR) -> float:
        data = read_json_dict(LIVE_STATE_FILE)
        val = data.get("floor")
        if isinstance(val, (int, float)):
            return clamp_effective_floor(float(val))
        return default
