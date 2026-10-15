"""Per-patch effect setup: which effects are on, one depth, a level per effect,
the mod-knob target.

A sidecar store like normalization and hold (`SidecarKeyMixin`): keyed by the
patch's stable key, read with the legacy stem as a fallback, migrated by the
post-scan stem migration. A moved or renamed patch has a new stable key and
starts again from the defaults, the same as its Hold and normalization.

**Effect state is entirely per patch.** A patch with no entry plays the
factory default: no effect on (`FACTORY_MASK`) at depth 0 (`FACTORY_DEPTH`),
every level 1.0 (`FACTORY_LEVELS`), never a global or last-used value. Every
entry this store holds has a mask, a depth and the levels: an entry read
without one (written before #142 round 5, which resolved a missing field from
the global default, or before per-effect levels) is completed with the factory
value on load, and the next save writes it whole. Only `target` may be
missing; it then follows the global Settings default, on purpose.

Entry shape (one per patch):

    {"mask": 5, "depth": 0.4, "levels": [1.0, 1.0, 0.4, 1.0, 1.0], "target": "effect",
     "bass_hz": 150.0}

`mask` is the chain's enable mask (`fx_host_protocol`: bit `1 << effect_id`
per enabled effect), `depth` the shared depth (the knob), and `levels` one
level per effect, 0-1, by effect id: effect i plays at `depth * levels[i]`.
An unticked effect keeps its level. Nothing read from the file is trusted: a
mask naming an effect this build does not ship, a non-finite or out-of-range
depth and an unknown target are dropped on read, and levels are read by
`fx_host_protocol.levels_from` (append-only; a bad entry is 1.0).

**Bass bypass** (`bass_hz`, #173): the crossover below which the lows skip
the effects, 0 for Off. Like `target`, it may be missing, and missing is not
completed on load: an entry without it (every entry written before it) is
*unset*, which plays at `fx_host_protocol.BASS_DEFAULT_HZ` (120 Hz). So the
patches already set up keep every value they had and pick up the default, and
a patch keeps a crossover only once the slider sets one. A value out of range
or not a number reads as unset.

**Migration to per-effect levels.** An entry with no `levels` (every entry
written before them) is read with every level 1.0, so the patch sounds as it
did: `depth * 1.0` is the depth it played at.

**Migration from the per-effect entries of #150**, which looked like
`{"effect": 1, "depths": {"0": 0.4, "1": 0.25}, "target": "effect"}`: an entry
with `effect` or `depths` and no `mask` is read as that shape. The selected effect becomes a
one-bit mask and its depth the shared depth; the other effects' depths are
dropped, because there is now one depth. An old entry with depths but no
selected effect keeps no depth: which effect those belonged to depended on the
global default at the time, which the store cannot see; it gets the factory
default like any other missing field. Entries are converted
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

#: The factory default effect state: nothing on, depth 0, every level 1.0.
FACTORY_MASK = fx_host_protocol.MASK_NONE
FACTORY_DEPTH = 0.0
FACTORY_LEVELS = fx_host_protocol.LEVELS_DEFAULT


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
    """One patch's own values. None: the factory default (mask, depth,
    levels) or the global default (target). The store never hands out a None
    mask, depth or levels for a patch it has an entry for."""

    mask: int | None = None
    depth: float | None = None
    target: str | None = None
    #: One level per shipped effect, by effect id.
    levels: tuple[float, ...] | None = None
    #: The bass bypass's crossover, Hz, or BASS_OFF. None: unset, which plays
    #: at the default (`fx_host_protocol.BASS_DEFAULT_HZ`).
    bass_hz: float | None = None

    @property
    def is_empty(self) -> bool:
        return (
            self.mask is None
            and self.depth is None
            and self.target is None
            and self.levels is None
            and self.bass_hz is None
        )

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.mask is not None:
            out["mask"] = self.mask
        if self.depth is not None:
            out["depth"] = self.depth
        if self.levels is not None:
            out["levels"] = list(self.levels)
        if self.target is not None:
            out["target"] = self.target
        if self.bass_hz is not None:
            out["bass_hz"] = self.bass_hz
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
        return cls(
            mask=mask,
            depth=depth,
            target=target if target in STORED_TARGETS else None,
            levels=fx_host_protocol.levels_from(raw.get("levels")),
            bass_hz=fx_host_protocol.bass_value(raw.get("bass_hz")),
        )

    def completed(self) -> "PatchFx":
        """The same entry with a missing mask, depth or levels set to the
        factory default (never a global one). The target and the bass
        crossover stay optional: unset is a value of its own."""
        return PatchFx(
            mask=FACTORY_MASK if self.mask is None else self.mask,
            depth=FACTORY_DEPTH if self.depth is None else self.depth,
            target=self.target,
            levels=FACTORY_LEVELS if self.levels is None else self.levels,
            bass_hz=self.bass_hz,
        )


class PatchFxStore(SidecarKeyMixin):
    """Persist each patch's effect setup."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_fx_path()
        self._data: dict[str, dict[str, Any]] = {}
        self.load()

    def load(self) -> None:
        """Read the file, converting every entry to the current shape in memory
        and completing a missing mask, depth or levels with the factory default (the
        file itself changes at the next save). An entry with nothing usable in
        it is dropped: the patch then plays the factory default anyway."""
        self._data = {}
        if self.path.exists():
            for key, entry in read_json_dict(self.path, label="fx file").items():
                fx = PatchFx.from_json(entry)
                if not fx.is_empty:
                    self._data[key] = fx.completed().to_json()

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
            self._data[key] = fx.completed().to_json()
        if persist:
            self.save()
