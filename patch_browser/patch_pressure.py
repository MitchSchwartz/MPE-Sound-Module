"""Per-patch MPE pressure floor — light touch vs full press (issue #29 / #31)."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

# Floor at zero pressure: mult(0)=floor, mult(1)=1.0 always.
PRESSURE_FLOOR_MIN = 0.0
PRESSURE_FLOOR_MAX = 0.9
DEFAULT_PRESSURE_FLOOR = 0.0

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


def remap_pressure_7bit(value: int, floor: float) -> int:
    p = max(0, min(127, int(value))) / 127.0
    out = effective_pressure_mult(p, floor) * 127.0
    return max(0, min(127, int(round(out))))


def _read_json_dict(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Warning: could not load pressure file {path}: {exc}")
        return {}
    return raw if isinstance(raw, dict) else {}


class PatchPressureStore:
    """Persist per-patch pressure floor overrides."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_pressure_path()
        self._data: dict[str, dict[str, Any]] = {}
        self.load()

    def load(self) -> None:
        self._data = {}
        if self.path.exists():
            for key, entry in _read_json_dict(self.path).items():
                if isinstance(entry, dict):
                    self._data[key] = dict(entry)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(self._data, indent=2, sort_keys=True) + "\n"
        fd, tmp_name = tempfile.mkstemp(
            dir=self.path.parent,
            prefix=f".{self.path.name}.",
            suffix=".tmp",
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self.path)
            dir_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

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

    def get_effective_floor(self, patch_name: str) -> float:
        user = self.get_user_floor(patch_name)
        if user is None:
            return DEFAULT_PRESSURE_FLOOR
        return max(PRESSURE_FLOOR_MIN, min(PRESSURE_FLOOR_MAX, user))

    def set_user_floor(self, patch_name: str, floor: float, *, persist: bool = True) -> None:
        key = self.patch_key(patch_name)
        clamped = max(PRESSURE_FLOOR_MIN, min(PRESSURE_FLOOR_MAX, float(floor)))
        if abs(clamped - DEFAULT_PRESSURE_FLOOR) < 0.01:
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

    def format_floor(self, floor: float) -> str:
        return f"{round(floor * 100)}"

    def write_live_state(self, patch_name: str, floor: float | None = None) -> None:
        """Signal the MIDI remapper daemon (read on each message / mtime)."""
        eff = self.get_effective_floor(patch_name) if floor is None else float(floor)
        payload = {
            "patch": self.patch_key(patch_name),
            "floor": max(PRESSURE_FLOOR_MIN, min(PRESSURE_FLOOR_MAX, eff)),
        }
        text = json.dumps(payload) + "\n"
        fd, tmp_name = tempfile.mkstemp(
            dir=LIVE_STATE_FILE.parent,
            prefix=".patch_browser_pressure_live.",
            suffix=".tmp",
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, LIVE_STATE_FILE)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    @staticmethod
    def read_live_floor(default: float = DEFAULT_PRESSURE_FLOOR) -> float:
        try:
            data = json.loads(LIVE_STATE_FILE.read_text())
            val = data.get("floor")
            if isinstance(val, (int, float)):
                return max(PRESSURE_FLOOR_MIN, min(PRESSURE_FLOOR_MAX, float(val)))
        except (OSError, json.JSONDecodeError, TypeError):
            pass
        return default
