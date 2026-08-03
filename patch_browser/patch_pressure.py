"""Per-patch MPE pressure floor — light touch vs full press (issue #29 / #31)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from patch_browser.json_store import atomic_write_json, read_json_dict

# Floor at zero pressure: mult(0)=floor, mult(1)=1.0 always.
PRESSURE_FLOOR_MIN = 0.0
PRESSURE_FLOOR_MAX = 0.9
DEFAULT_PRESSURE_FLOOR = 0.0

# Light-touch calibration gesture (7-bit MPE pressure held constant).
LIGHT_TOUCH_PRESSURE = 25
LIGHT_TOUCH_HOLD_SECONDS = 1.5
LIGHT_TOUCH_GESTURE_SECONDS = 2.5

# When batch size is too small for a stable cohort median, align to this LUFS target.
LIGHT_TOUCH_TARGET_LUFS = -28.0

# dB shortfall at light touch mapped to floor=1.0 (empirical; tune on Pi).
FLOOR_DB_PER_UNIT = 18.0

LIVE_STATE_FILE = Path.home() / ".patch_browser_pressure_live.json"


def default_pressure_path() -> Path:
    env = os.environ.get("MPE_PRESSURE_FILE", "").strip()
    if env:
        return Path(env).expanduser()
    return Path.home() / ".patch_browser_pressure.json"


def effective_pressure_mult(raw: float, floor: float) -> float:
    """Map normalized pressure p∈[0,1] with floor pinned at full press."""
    p = max(0.0, min(1.0, float(raw)))
    f = max(PRESSURE_FLOOR_MIN, min(PRESSURE_FLOOR_MAX, float(floor)))
    return f + (1.0 - f) * p


def compute_pressure_floor(lufs_light: float, target_lufs_light: float) -> float:
    """Derive calibrated floor from how quiet light touch is vs cohort target."""
    shortfall_db = float(target_lufs_light) - float(lufs_light)
    if shortfall_db <= 0.5:
        return DEFAULT_PRESSURE_FLOOR
    floor = shortfall_db / FLOOR_DB_PER_UNIT
    return max(PRESSURE_FLOOR_MIN, min(PRESSURE_FLOOR_MAX, floor))


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
        entry = self._data.get(self.patch_key(patch_name))
        if not entry:
            return None
        val = entry.get("user_floor")
        if isinstance(val, (int, float)):
            return float(val)
        return None

    def get_calibrated_floor(self, patch_name: str) -> float | None:
        entry = self._data.get(self.patch_key(patch_name))
        if not entry:
            return None
        val = entry.get("cal_floor")
        if isinstance(val, (int, float)):
            return float(val)
        return None

    def get_slider_default_floor(self, patch_name: str) -> float:
        calibrated = self.get_calibrated_floor(patch_name)
        if calibrated is not None:
            return max(PRESSURE_FLOOR_MIN, min(PRESSURE_FLOOR_MAX, calibrated))
        return DEFAULT_PRESSURE_FLOOR

    def has_user_floor_override(self, patch_name: str) -> bool:
        entry = self._data.get(self.patch_key(patch_name))
        return bool(entry and "user_floor" in entry)

    def get_effective_floor(self, patch_name: str) -> float:
        user = self.get_user_floor(patch_name)
        if user is not None:
            return max(PRESSURE_FLOOR_MIN, min(PRESSURE_FLOOR_MAX, user))
        calibrated = self.get_calibrated_floor(patch_name)
        if calibrated is not None:
            return max(PRESSURE_FLOOR_MIN, min(PRESSURE_FLOOR_MAX, calibrated))
        return DEFAULT_PRESSURE_FLOOR

    def set_user_floor(self, patch_name: str, floor: float, *, persist: bool = True) -> None:
        key = self.patch_key(patch_name)
        clamped = max(PRESSURE_FLOOR_MIN, min(PRESSURE_FLOOR_MAX, float(floor)))
        default = self.get_slider_default_floor(patch_name)
        if abs(clamped - default) < 0.01:
            self.clear_user_floor(patch_name, persist=persist)
            return
        entry = dict(self._data.get(key) or {})
        entry["user_floor"] = clamped
        self._data[key] = entry
        if persist:
            self.save()

    def clear_user_floor(self, patch_name: str, *, persist: bool = True) -> None:
        key = self.patch_key(patch_name)
        entry = self._data.get(key)
        if not entry or "user_floor" not in entry:
            return
        entry = dict(entry)
        entry.pop("user_floor", None)
        if entry:
            self._data[key] = entry
        else:
            self._data.pop(key, None)
        if persist:
            self.save()

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
        if existing and "user_floor" in existing:
            entry["user_floor"] = existing["user_floor"]
        if abs(clamped - DEFAULT_PRESSURE_FLOOR) < 0.01 and not (
            existing and "user_floor" in existing
        ):
            self._data.pop(key, None)
            return
        self._data[key] = entry

    def format_floor(self, floor: float) -> str:
        return f"{round(floor * 100)}"

    def write_live_state(self, patch_name: str, floor: float | None = None) -> None:
        """Signal the MIDI remapper daemon (read on each message / mtime)."""
        eff = self.get_effective_floor(patch_name) if floor is None else float(floor)
        payload = {
            "patch": self.patch_key(patch_name),
            "floor": max(PRESSURE_FLOOR_MIN, min(PRESSURE_FLOOR_MAX, eff)),
        }
        atomic_write_json(LIVE_STATE_FILE, payload)

    @staticmethod
    def read_live_floor(default: float = DEFAULT_PRESSURE_FLOOR) -> float:
        data = read_json_dict(LIVE_STATE_FILE)
        val = data.get("floor")
        if isinstance(val, (int, float)):
            return max(PRESSURE_FLOOR_MIN, min(PRESSURE_FLOOR_MAX, float(val)))
        return default
