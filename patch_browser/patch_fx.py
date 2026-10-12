"""Per-patch effect setup: the selected effect, each effect's depth, the mod-knob target.

A sidecar store like normalization and hold (`SidecarKeyMixin`): keyed by the
patch's stable key, read with the legacy stem as a fallback, migrated by the
post-scan stem migration. A moved or renamed patch has a new stable key and
starts again from the global defaults, the same as its Hold and normalization.

Each field is optional. Missing means "use the global default", resolved by
`fx_policy.resolve_config`: patch value first, then the global default. A
patch never set plays the global default effect at depth 0.

Entry shape (one per patch):

    {"effect": 1, "depths": {"0": 0.4, "1": 0.25}, "target": "effect"}

Depth keys are effect ids as strings (JSON object keys). Nothing read from the
file is trusted: unknown effect ids, non-finite or out-of-range depths and
unknown targets are dropped on read.

This module only stores. `FxPolicy` decides when to write (debounced, never
per knob tick) and what the values mean.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from patch_browser import fx_host_protocol
from patch_browser.json_store import atomic_write_json, read_json_dict
from patch_browser.patch_sidecar_store import SidecarKeyMixin

#: The mod-knob targets a patch may store. Same values as
#: `fx_policy.MOD_WHEEL_TARGETS`; restated here so the store does not import
#: the policy (the policy imports the store's types).
STORED_TARGETS = ("patch_first", "effect")


def default_fx_path() -> Path:
    env = os.environ.get("MPE_FX_FILE", "").strip()
    if env:
        return Path(env).expanduser()
    return Path.home() / ".patch_browser_fx.json"


def _valid_effect(value) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, int)
        and value in fx_host_protocol.EFFECT_IDS
    )


def _depth(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        return None
    return float(value)


@dataclass(frozen=True)
class PatchFx:
    """One patch's own values. None / absent means the global default."""

    effect: int | None = None
    depths: dict[int, float] = field(default_factory=dict)
    target: str | None = None

    @property
    def is_empty(self) -> bool:
        return self.effect is None and not self.depths and self.target is None

    def depth_for(self, effect_id: int) -> float:
        """This patch's depth for an effect; 0 for one it never set."""
        return self.depths.get(effect_id, 0.0)

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.effect is not None:
            out["effect"] = self.effect
        if self.depths:
            out["depths"] = {str(k): self.depths[k] for k in sorted(self.depths)}
        if self.target is not None:
            out["target"] = self.target
        return out

    @classmethod
    def from_json(cls, raw) -> "PatchFx":
        if not isinstance(raw, dict):
            return cls()
        effect = raw.get("effect")
        target = raw.get("target")
        depths: dict[int, float] = {}
        raw_depths = raw.get("depths")
        if isinstance(raw_depths, dict):
            for key, value in raw_depths.items():
                try:
                    effect_id = int(key)
                except (TypeError, ValueError):
                    continue
                if str(effect_id) != str(key) or not _valid_effect(effect_id):
                    continue
                depth = _depth(value)
                if depth is not None:
                    depths[effect_id] = depth
        return cls(
            effect=effect if _valid_effect(effect) else None,
            depths=depths,
            target=target if target in STORED_TARGETS else None,
        )


class PatchFxStore(SidecarKeyMixin):
    """Persist each patch's effect setup."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_fx_path()
        self._data: dict[str, dict[str, Any]] = {}
        self.load()

    def load(self) -> None:
        self._data = {}
        if self.path.exists():
            for key, entry in read_json_dict(self.path, label="fx file").items():
                if isinstance(entry, dict):
                    self._data[key] = dict(entry)

    def save(self) -> None:
        atomic_write_json(self.path, self._data)

    def get(
        self,
        patch_name: str,
        *,
        patch_path: str | None = None,
        stable_key: str | None = None,
    ) -> PatchFx:
        entry, _key = self._lookup(patch_name, patch_path=patch_path, stable_key=stable_key)
        return PatchFx.from_json(entry)

    def set(
        self,
        patch_name: str,
        fx: PatchFx,
        *,
        persist: bool = True,
        patch_path: str | None = None,
        stable_key: str | None = None,
    ) -> None:
        """Replace the patch's entry; an empty setup removes it."""
        key = self._storage_key(patch_name, patch_path=patch_path, stable_key=stable_key)
        _entry, matched = self._lookup(patch_name, patch_path=patch_path, stable_key=stable_key)
        if matched and matched != key:
            self._data.pop(matched, None)
        if fx.is_empty:
            self._data.pop(key, None)
        else:
            self._data[key] = fx.to_json()
        if persist:
            self.save()
