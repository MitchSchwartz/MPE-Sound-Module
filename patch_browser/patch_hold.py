"""Per-patch Hold multiplier — scales AEG sustain, decay, and release."""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any

from patch_browser.json_store import atomic_write_json, read_json_dict
from patch_browser.patch_sidecar_store import SidecarKeyMixin

# Multiplier range for the Hold mixer fader (1.0 = patch-as-loaded).
HOLD_MULT_MIN = 0.25
HOLD_MULT_MAX = 4.0
DEFAULT_HOLD_MULT = 1.0

# Tail fader UI: bipolar offset from 1.0× (0 = center). Same ±50 *display range* as Touch, different zero semantics — see docs/TOUCH_PATCH_BROWSER.md §Mixer faders.
HOLD_OFFSET_MIN = -0.50
HOLD_OFFSET_MAX = 0.50
HOLD_OFFSET_SPAN = 0.50
HOLD_OFFSET_CLEAR_EPSILON = 0.01

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


def clamp_hold_offset(offset: float) -> float:
    return max(HOLD_OFFSET_MIN, min(HOLD_OFFSET_MAX, float(offset)))


def hold_offset_to_mult(offset: float) -> float:
    """Map bipolar Tail fader position to Hold multiplier (log2, ±2 octaves)."""
    offset = clamp_hold_offset(offset)
    mult = 2.0 ** (offset / HOLD_OFFSET_SPAN * 2.0)
    return max(HOLD_MULT_MIN, min(HOLD_MULT_MAX, mult))


def hold_mult_to_offset(mult: float) -> float:
    """Map stored Hold multiplier to bipolar Tail fader position."""
    mult = max(HOLD_MULT_MIN, min(HOLD_MULT_MAX, float(mult)))
    return clamp_hold_offset(math.log2(mult) * HOLD_OFFSET_SPAN / 2.0)


class PatchHoldStore(SidecarKeyMixin):
    """Persist per-patch AEG baselines and optional user Hold multiplier."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_hold_path()
        self._data: dict[str, dict[str, Any]] = {}
        self.load()

    def load(self) -> None:
        self._data = {}
        if self.path.exists():
            for key, entry in read_json_dict(self.path, label="hold file").items():
                if isinstance(entry, dict):
                    self._data[key] = dict(entry)

    def save(self) -> None:
        atomic_write_json(self.path, self._data)

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

    def get_baseline(
        self,
        patch_name: str,
        *,
        patch_path: str | None = None,
        stable_key: str | None = None,
    ) -> dict[str, dict[str, float]] | None:
        entry = self.get_entry(
            patch_name, patch_path=patch_path, stable_key=stable_key
        )
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

    def set_baseline(
        self,
        patch_name: str,
        baseline: dict[str, dict[str, float]],
        *,
        patch_path: str | None = None,
        stable_key: str | None = None,
    ) -> None:
        key = self._storage_key(
            patch_name, patch_path=patch_path, stable_key=stable_key
        )
        entry, matched = self._lookup(
            patch_name, patch_path=patch_path, stable_key=stable_key
        )
        entry = dict(entry or {})
        entry["baseline"] = {
            scene: {stage: float(baseline[scene][stage]) for stage in AEG_HOLD_STAGES}
            for scene in AEG_HOLD_SCENES
        }
        if matched and matched != key:
            self._data.pop(matched, None)
        self._data[key] = entry
        self.save()

    def get_user_hold_mult(
        self,
        patch_name: str,
        *,
        patch_path: str | None = None,
        stable_key: str | None = None,
    ) -> float | None:
        entry = self.get_entry(
            patch_name, patch_path=patch_path, stable_key=stable_key
        )
        if not entry:
            return None
        val = entry.get("user_hold_mult")
        if isinstance(val, (int, float)):
            return float(val)
        return None

    def get_effective_hold_mult(
        self,
        patch_name: str,
        *,
        patch_path: str | None = None,
        stable_key: str | None = None,
    ) -> float:
        user = self.get_user_hold_mult(
            patch_name, patch_path=patch_path, stable_key=stable_key
        )
        if user is None:
            return DEFAULT_HOLD_MULT
        return max(HOLD_MULT_MIN, min(HOLD_MULT_MAX, user))

    def set_user_hold_mult(
        self,
        patch_name: str,
        mult: float,
        *,
        persist: bool = True,
        patch_path: str | None = None,
        stable_key: str | None = None,
    ) -> None:
        key = self._storage_key(
            patch_name, patch_path=patch_path, stable_key=stable_key
        )
        entry, matched = self._lookup(
            patch_name, patch_path=patch_path, stable_key=stable_key
        )
        entry = dict(entry or {})
        entry["user_hold_mult"] = max(HOLD_MULT_MIN, min(HOLD_MULT_MAX, float(mult)))
        if matched and matched != key:
            self._data.pop(matched, None)
        self._data[key] = entry
        if persist:
            self.save()

    def clear_user_hold_mult(
        self,
        patch_name: str,
        *,
        persist: bool = True,
        patch_path: str | None = None,
        stable_key: str | None = None,
    ) -> None:
        entry, key = self._lookup(
            patch_name, patch_path=patch_path, stable_key=stable_key
        )
        if not entry or "user_hold_mult" not in entry or not key:
            return
        entry = dict(entry)
        entry.pop("user_hold_mult", None)
        storage = self._storage_key(
            patch_name, patch_path=patch_path, stable_key=stable_key
        )
        if key != storage:
            self._data.pop(key, None)
        if entry:
            self._data[storage] = entry
        else:
            self._data.pop(storage, None)
        if persist:
            self.save()

    def format_hold_offset(self, offset: float) -> str:
        """Bipolar Tail fader label — 0 at center, ±50 scale."""
        pts = round(clamp_hold_offset(offset) * 100)
        if pts > 0:
            return f"+{pts}"
        return str(pts)
