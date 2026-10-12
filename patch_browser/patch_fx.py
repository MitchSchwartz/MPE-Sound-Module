"""Per-patch effect setup: which effects are on, one depth, the mod-knob target.

A sidecar store like normalization and hold (`SidecarKeyMixin`): keyed by the
patch's stable key, read with the legacy stem as a fallback, migrated by the
post-scan stem migration. A moved or renamed patch has a new stable key and
starts again from the global defaults, the same as its Hold and normalization.

Each field is optional. Missing means "use the global default", resolved by
`fx_policy.resolve_config`: patch value first, then the global default. A
patch never set plays the global default chain at depth 0.

Entry shape (one per patch):

    {"mask": 5, "depth": 0.4, "target": "effect"}

`mask` is the chain's enable mask (`fx_host_protocol`: bit `1 << effect_id`
per enabled effect), `depth` the one depth every enabled effect plays at.
Nothing read from the file is trusted: a mask naming an effect this build does
not ship, a non-finite or out-of-range depth and an unknown target are dropped
on read.

**Migration from the per-effect entries of #150**, which looked like
`{"effect": 1, "depths": {"0": 0.4, "1": 0.25}, "target": "effect"}`: an entry
with `effect` or `depths` and no `mask` is read as that shape. The selected effect becomes a
one-bit mask and its depth the shared depth; the other effects' depths are
dropped, because there is now one depth. An old entry with depths but no
selected effect keeps no depth: which effect those belonged to depended on the
global default at the time, which the store cannot see. Entries are converted
when the file is loaded, so the next save writes the new shape for every patch.

This module only stores. `FxPolicy` decides when to write (debounced, never
per knob tick) and what the values mean.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
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


def _int(value) -> int | None:
    return None if isinstance(value, bool) or not isinstance(value, int) else value


def valid_mask(value) -> int | None:
    """`value` if it is a mask of effects this build ships, else None."""
    mask = _int(value)
    if mask is None or mask < 0 or mask & ~fx_host_protocol.MASK_ALL:
        return None
    return mask


def _depth(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        return None
    return float(value)


#: Keys only a #150 entry has.
_LEGACY_KEYS = frozenset(("effect", "depths"))


def _from_per_effect(raw: dict) -> tuple[int | None, float | None]:
    """(mask, depth) from a #150 entry: the selected effect and its depth."""
    effect = _int(raw.get("effect"))
    if effect is None or effect not in fx_host_protocol.EFFECT_IDS:
        return None, None
    depths = raw.get("depths")
    depth = _depth(depths.get(str(effect))) if isinstance(depths, dict) else None
    return fx_host_protocol.mask_of((effect,)), depth


@dataclass(frozen=True)
class PatchFx:
    """One patch's own values. None means the global default."""

    mask: int | None = None
    depth: float | None = None
    target: str | None = None

    @property
    def is_empty(self) -> bool:
        return self.mask is None and self.depth is None and self.target is None

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.mask is not None:
            out["mask"] = self.mask
        if self.depth is not None:
            out["depth"] = self.depth
        if self.target is not None:
            out["target"] = self.target
        return out

    @classmethod
    def from_json(cls, raw) -> "PatchFx":
        if not isinstance(raw, dict):
            return cls()
        target = raw.get("target")
        if "mask" in raw or not _LEGACY_KEYS & raw.keys():
            mask, depth = valid_mask(raw.get("mask")), _depth(raw.get("depth"))
        else:
            mask, depth = _from_per_effect(raw)
        return cls(mask=mask, depth=depth, target=target if target in STORED_TARGETS else None)


class PatchFxStore(SidecarKeyMixin):
    """Persist each patch's effect setup."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_fx_path()
        self._data: dict[str, dict[str, Any]] = {}
        self.load()

    def load(self) -> None:
        """Read the file, converting every entry to the current shape in memory
        (the file itself changes at the next save)."""
        self._data = {}
        if self.path.exists():
            for key, entry in read_json_dict(self.path, label="fx file").items():
                fx = PatchFx.from_json(entry)
                if not fx.is_empty:
                    self._data[key] = fx.to_json()

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
