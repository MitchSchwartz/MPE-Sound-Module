"""Per-patch Hold multiplier — scales AEG sustain, decay, and release."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

# Multiplier range for the Hold mixer fader (1.0 = patch-as-loaded).
HOLD_MULT_MIN = 0.25
HOLD_MULT_MAX = 4.0
DEFAULT_HOLD_MULT = 1.0

# Surge OSC amp envelope params are normalized 0..1.
AEG_PARAM_MIN = 0.0
AEG_PARAM_MAX = 1.0

# Scene × stage keys captured at patch load (attack excluded — not part of Hold).
AEG_HOLD_STAGES = ("sustain", "decay", "release")
AEG_HOLD_SCENES = ("a", "b")


def default_hold_path() -> Path:
    env = os.environ.get("MPE_HOLD_FILE", "").strip()
    if env:
        return Path(env).expanduser()
    return Path.home() / ".patch_browser_hold.json"


def osc_aeg_path(scene: str, stage: str) -> str:
    """OSC set path for an amp envelope stage."""
    return f"/param/{scene}/aeg/{stage}"


def iter_hold_osc_paths() -> list[tuple[str, str, str]]:
    """Yield (scene, stage, osc_path) for all Hold-controlled params."""
    out: list[tuple[str, str, str]] = []
    for scene in AEG_HOLD_SCENES:
        for stage in AEG_HOLD_STAGES:
            out.append((scene, stage, osc_aeg_path(scene, stage)))
    return out


def empty_baseline() -> dict[str, dict[str, float]]:
    return {scene: {stage: 0.0 for stage in AEG_HOLD_STAGES} for scene in AEG_HOLD_SCENES}


def effective_aeg_value(baseline: float, mult: float) -> float:
    """Scale a captured baseline by Hold multiplier, clamped to Surge range."""
    return max(AEG_PARAM_MIN, min(AEG_PARAM_MAX, float(baseline) * float(mult)))


def _read_json_dict(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Warning: could not load hold file {path}: {exc}")
        return {}
    return raw if isinstance(raw, dict) else {}


class PatchHoldStore:
    """Persist per-patch AEG baselines and optional user Hold multiplier."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_hold_path()
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

    def get_entry(self, patch_name: str) -> dict[str, Any] | None:
        entry = self._data.get(self.patch_key(patch_name))
        return entry if isinstance(entry, dict) else None

    def get_baseline(self, patch_name: str) -> dict[str, dict[str, float]] | None:
        entry = self.get_entry(patch_name)
        if not entry:
            return None
        raw = entry.get("baseline")
        if not isinstance(raw, dict):
            return None
        baseline = empty_baseline()
        ok = False
        for scene in AEG_HOLD_SCENES:
            scene_raw = raw.get(scene)
            if not isinstance(scene_raw, dict):
                continue
            for stage in AEG_HOLD_STAGES:
                val = scene_raw.get(stage)
                if isinstance(val, (int, float)):
                    baseline[scene][stage] = float(val)
                    ok = True
        return baseline if ok else None

    def set_baseline(self, patch_name: str, baseline: dict[str, dict[str, float]]) -> None:
        key = self.patch_key(patch_name)
        entry = dict(self._data.get(key) or {})
        entry["baseline"] = {
            scene: {stage: float(baseline[scene][stage]) for stage in AEG_HOLD_STAGES}
            for scene in AEG_HOLD_SCENES
        }
        self._data[key] = entry
        self.save()

    def get_user_hold_mult(self, patch_name: str) -> float | None:
        entry = self.get_entry(patch_name)
        if not entry:
            return None
        val = entry.get("user_hold_mult")
        if isinstance(val, (int, float)):
            return float(val)
        return None

    def get_effective_hold_mult(self, patch_name: str) -> float:
        user = self.get_user_hold_mult(patch_name)
        if user is None:
            return DEFAULT_HOLD_MULT
        return max(HOLD_MULT_MIN, min(HOLD_MULT_MAX, user))

    def set_user_hold_mult(self, patch_name: str, mult: float, *, persist: bool = True) -> None:
        key = self.patch_key(patch_name)
        entry = dict(self._data.get(key) or {})
        entry["user_hold_mult"] = max(HOLD_MULT_MIN, min(HOLD_MULT_MAX, float(mult)))
        self._data[key] = entry
        if persist:
            self.save()

    def clear_user_hold_mult(self, patch_name: str, *, persist: bool = True) -> None:
        key = self.patch_key(patch_name)
        entry = self.get_entry(patch_name)
        if not entry or "user_hold_mult" not in entry:
            return
        entry = dict(entry)
        entry.pop("user_hold_mult", None)
        if entry:
            self._data[key] = entry
        else:
            self._data.pop(key, None)
        if persist:
            self.save()

    def format_hold_mult(self, mult: float) -> str:
        if abs(mult - round(mult)) < 0.05:
            return f"{mult:.0f}×"
        return f"{mult:.1f}×"
