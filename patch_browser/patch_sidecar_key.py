"""Path-based keys for normalization / hold / pressure sidecar JSON stores."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from patch_browser.patch_identity import (
    patch_root_label,
    stable_key_for_relative_path,
)


def stem_from_name(patch_name: str) -> str:
    return Path(patch_name).stem


def is_stable_key(key: str) -> bool:
    """True when key looks like ``factory:Bass/Sub/Lead`` (not a legacy stem)."""
    if not key or key.startswith("_"):
        return False
    if ":" not in key:
        return False
    root, _, rest = key.partition(":")
    return bool(root and rest)


# Path.resolve() stats every component of every path, and this function is on the
# touch UI's per-frame read path (mixer faders and list rows all do sidecar lookups).
# Measured on the appliance 2026-08-17: ~19,000 newfstatat/s — a full core — walking
# /home, ~/MPE-Library, ~/Documents/Surge XT and friends over and over. Roots and
# patch paths do not move while the browser is up; a rescan rebuilds the patch dicts
# that feed this, so a process-lifetime memo is safe and is dropped by clear_path_caches.
_RESOLVED_PATHS: dict[str, Path] = {}
_STABLE_KEY_CACHE: dict[tuple[str, tuple[str, ...]], str | None] = {}
_PATH_CACHE_MAX = 4096


def clear_path_caches() -> None:
    """Drop memoised path resolution — call after a rescan or a patch-root change."""
    _RESOLVED_PATHS.clear()
    _STABLE_KEY_CACHE.clear()


def _resolved(path: str | Path) -> Path:
    key = str(path)
    hit = _RESOLVED_PATHS.get(key)
    if hit is not None:
        return hit
    resolved = Path(path).resolve()
    if len(_RESOLVED_PATHS) < _PATH_CACHE_MAX:
        _RESOLVED_PATHS[key] = resolved
    return resolved


def stable_key_from_absolute_path(
    patch_path: str | Path,
    patch_dirs: list[Path] | tuple[Path, ...],
) -> str | None:
    """Compute stable_key from an on-disk .fxp path and known Surge roots (memoised)."""
    cache_key = (str(patch_path), tuple(str(d) for d in patch_dirs))
    if cache_key in _STABLE_KEY_CACHE:
        return _STABLE_KEY_CACHE[cache_key]

    path = _resolved(patch_path)
    result: str | None = None
    for patch_dir in patch_dirs:
        root = _resolved(patch_dir)
        if not root.is_dir():
            continue
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        result = stable_key_for_relative_path(patch_root_label(patch_dir), rel)
        break

    if len(_STABLE_KEY_CACHE) < _PATH_CACHE_MAX:
        _STABLE_KEY_CACHE[cache_key] = result
    return result


def resolve_storage_key(
    patch_name: str,
    *,
    patch_path: str | None = None,
    stable_key: str | None = None,
    patch_dirs: list[Path] | tuple[Path, ...] | None = None,
) -> str:
    """Primary key for writes — prefer stable_key, else derive from path, else stem."""
    if stable_key:
        return stable_key
    if patch_path and patch_dirs:
        derived = stable_key_from_absolute_path(patch_path, patch_dirs)
        if derived:
            return derived
    return stem_from_name(patch_name)


def lookup_keys(
    patch_name: str,
    *,
    patch_path: str | None = None,
    stable_key: str | None = None,
    patch_dirs: list[Path] | tuple[Path, ...] | None = None,
) -> list[str]:
    """Keys to try for reads — stable first, then legacy stem (deduped)."""
    keys: list[str] = []
    storage = resolve_storage_key(
        patch_name,
        patch_path=patch_path,
        stable_key=stable_key,
        patch_dirs=patch_dirs,
    )
    if storage not in keys:
        keys.append(storage)
    stem = stem_from_name(patch_name)
    if stem not in keys:
        keys.append(stem)
    if stable_key and stable_key not in keys:
        keys.insert(0, stable_key)
    # Only derive when the caller could not supply a stable_key. Deriving regardless
    # meant every lookup paid a filesystem walk to recompute a key that was already
    # first in the list — the dominant cost of the per-frame read path.
    if not stable_key and patch_path and patch_dirs:
        derived = stable_key_from_absolute_path(patch_path, patch_dirs)
        if derived and derived not in keys:
            keys.insert(0, derived)
    return keys


def lookup_entry(
    data: dict[str, Any],
    patch_name: str,
    *,
    patch_path: str | None = None,
    stable_key: str | None = None,
    patch_dirs: list[Path] | tuple[Path, ...] | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Return (entry, matched_key) using stable-first lookup with stem fallback."""
    for key in lookup_keys(
        patch_name,
        patch_path=patch_path,
        stable_key=stable_key,
        patch_dirs=patch_dirs,
    ):
        entry = data.get(key)
        if isinstance(entry, dict):
            return entry, key
    return None, None


def build_stem_to_stable_keys(patches: list[dict]) -> dict[str, list[str]]:
    """Map patch stem → stable_key(s) from scanned patch dicts."""
    stem_map: dict[str, list[str]] = {}
    for patch in patches:
        name = patch.get("name")
        sk = patch.get("stable_key")
        if not name or not sk:
            continue
        stem = stem_from_name(str(name))
        bucket = stem_map.setdefault(stem, [])
        if sk not in bucket:
            bucket.append(sk)
    return stem_map


def migrate_sidecar_data(
    data: dict[str, Any],
    stem_to_stable: dict[str, list[str]],
    *,
    reserved_keys: frozenset[str] = frozenset(),
) -> tuple[dict[str, Any], list[str], bool]:
    """
    One-time stem → stable_key migration.

    Returns (new_data, collision_warnings, changed).
    Ambiguous stems are kept under the legacy key — no silent merge.
    """
    new_data: dict[str, Any] = {}
    warnings: list[str] = []
    changed = False

    for key, entry in data.items():
        if key in reserved_keys or is_stable_key(key):
            if key in new_data and new_data[key] != entry:
                warnings.append(f"duplicate stable key {key!r} during migration")
            new_data[key] = entry
            continue

        candidates = stem_to_stable.get(key, [])
        if len(candidates) == 1:
            target = candidates[0]
            if target != key:
                changed = True
            if target in new_data:
                warnings.append(
                    f"stem {key!r} → {target!r} collides with existing entry; keeping stem"
                )
                new_data[key] = entry
            else:
                new_data[target] = entry
        elif len(candidates) > 1:
            warnings.append(
                f"ambiguous stem {key!r}: {len(candidates)} patches "
                f"({', '.join(candidates[:3])}{'…' if len(candidates) > 3 else ''}); "
                f"keeping legacy key"
            )
            new_data[key] = entry
        else:
            new_data[key] = entry

    return new_data, warnings, changed


@dataclass(frozen=True)
class PatchRef:
    """Minimal patch identity for sidecar store I/O."""

    name: str
    path: str | None = None
    stable_key: str | None = None

    @classmethod
    def from_patch_dict(cls, patch: dict) -> PatchRef:
        return cls(
            name=str(patch["name"]),
            path=patch.get("path"),
            stable_key=patch.get("stable_key"),
        )

    def storage_key(
        self,
        patch_dirs: list[Path] | tuple[Path, ...] | None = None,
    ) -> str:
        return resolve_storage_key(
            self.name,
            patch_path=self.path,
            stable_key=self.stable_key,
            patch_dirs=patch_dirs,
        )


def sidecar_kwargs_from_patch(patch: dict | None) -> dict[str, str | None]:
    """Extract optional sidecar lookup kwargs from a scanned patch dict."""
    if not patch:
        return {}
    path = patch.get("path")
    return {
        "patch_path": str(path) if path else None,
        "stable_key": patch.get("stable_key"),
    }


def patch_refs_match(a: PatchRef, b: PatchRef) -> bool:
    """True when two patch refs refer to the same sidecar entry."""
    if a.stable_key and b.stable_key:
        return a.stable_key == b.stable_key
    if a.path and b.path:
        try:
            return Path(a.path).resolve() == Path(b.path).resolve()
        except OSError:
            pass
    return stem_from_name(a.name) == stem_from_name(b.name)
