"""One-time stem → stable_key migration for sidecar JSON stores."""

from __future__ import annotations

from pathlib import Path

from patch_browser.patch_hold import PatchHoldStore
from patch_browser.patch_loader import PatchLoader
from patch_browser.patch_normalization import PatchNormalizationStore
from patch_browser.patch_pressure import PatchPressureStore


def migrate_sidecar_stores(
    *,
    normalization: PatchNormalizationStore | None = None,
    hold: PatchHoldStore | None = None,
    pressure: PatchPressureStore | None = None,
    patches: list[dict],
    patch_dirs: list[Path] | tuple[Path, ...],
) -> list[str]:
    """
    Migrate legacy stem keys to path-based stable_key where unambiguous.

    Returns collision / ambiguity warnings (no silent merge).
    """
    stores = [
        store
        for store in (
            normalization,
            hold,
            pressure,
        )
        if store is not None
    ]
    for store in stores:
        store.set_patch_dirs(patch_dirs)

    warnings: list[str] = []
    for store in stores:
        label = type(store).__name__
        for msg in store.migrate_stem_keys(patches):
            warnings.append(f"{label}: {msg}")
    return warnings


def migrate_loader_sidecars(
    loader: PatchLoader,
    patches: list[dict],
    patch_dirs: list[Path] | tuple[Path, ...],
) -> list[str]:
    return migrate_sidecar_stores(
        normalization=loader.normalization,
        hold=loader.hold,
        pressure=loader.pressure,
        patches=patches,
        patch_dirs=patch_dirs,
    )
