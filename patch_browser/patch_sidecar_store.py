"""Shared sidecar-store helpers (stable_key lookup + migration)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from patch_browser.patch_sidecar_key import (
    PatchRef,
    lookup_entry,
    migrate_sidecar_data,
    resolve_storage_key,
    stem_from_name,
)


class SidecarKeyMixin:
    """Mixin for JSON sidecar stores keyed by patch identity."""

    _data: dict[str, dict[str, Any]]
    path: Path

    _patch_dirs: tuple[Path, ...] | None = None
    _reserved_keys: frozenset[str] = frozenset()

    def set_patch_dirs(self, patch_dirs: list[Path] | tuple[Path, ...]) -> None:
        self._patch_dirs = tuple(patch_dirs)

    @staticmethod
    def patch_key(
        patch_name: str,
        *,
        patch_path: str | None = None,
        stable_key: str | None = None,
    ) -> str:
        return stem_from_name(patch_name)

    def _storage_key(
        self,
        patch_name: str,
        *,
        patch_path: str | None = None,
        stable_key: str | None = None,
    ) -> str:
        return resolve_storage_key(
            patch_name,
            patch_path=patch_path,
            stable_key=stable_key,
            patch_dirs=self._patch_dirs,
        )

    def _lookup(
        self,
        patch_name: str,
        *,
        patch_path: str | None = None,
        stable_key: str | None = None,
    ) -> tuple[dict[str, Any] | None, str | None]:
        return lookup_entry(
            self._data,
            patch_name,
            patch_path=patch_path,
            stable_key=stable_key,
            patch_dirs=self._patch_dirs,
        )

    def migrate_stem_keys(
        self,
        patches: list[dict],
        *,
        persist: bool = True,
    ) -> list[str]:
        """Migrate legacy stem keys to stable_key where unambiguous."""
        from patch_browser.patch_sidecar_key import build_stem_to_stable_keys

        stem_map = build_stem_to_stable_keys(patches)
        new_data, warnings, changed = migrate_sidecar_data(
            self._data,
            stem_map,
            reserved_keys=self._reserved_keys,
        )
        if changed:
            self._data = new_data
            if persist:
                self.save()
        return warnings

    @staticmethod
    def refs_match(
        loaded: dict | None,
        target: dict | PatchRef,
    ) -> bool:
        if not loaded:
            return False
        from patch_browser.patch_sidecar_key import patch_refs_match

        return patch_refs_match(
            PatchRef.from_patch_dict(loaded),
            target if isinstance(target, PatchRef) else PatchRef.from_patch_dict(target),
        )
