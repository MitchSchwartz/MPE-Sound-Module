"""Quick Select backup, restore, and index rebuild helpers."""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

from patch_browser.favorites_index import FavoritesIndex
from patch_browser.patch_sidecar_key import stable_key_from_absolute_path


def default_backup_root() -> Path:
    return Path.home() / ".patch_browser_favorites_backups"


def snapshot_favorites(
    qa_root: Path,
    index_path: Path,
    *,
    dest_dir: Path | None = None,
    label: str = "",
) -> Path:
    """
    Copy Quick Select tree + favorites index JSON to a timestamped directory.

    Returns the snapshot directory path.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    suffix = f"-{label}" if label else ""
    out = dest_dir or (default_backup_root() / f"{stamp}{suffix}")
    out.mkdir(parents=True, exist_ok=True)

    qa_name = qa_root.name
    if qa_root.is_dir():
        shutil.copytree(qa_root, out / qa_name, dirs_exist_ok=True)
    if index_path.is_file():
        shutil.copy2(index_path, out / index_path.name)

    meta = out / "README.txt"
    meta.write_text(
        f"Quick Select snapshot {stamp}\n"
        f"qa_root={qa_root}\n"
        f"index={index_path}\n",
        encoding="utf-8",
    )
    return out


def restore_favorites_tree(
    qa_root: Path,
    source_qa_dir: Path,
    *,
    index_path: Path | None = None,
    source_index: Path | None = None,
    backup_before: bool = True,
) -> Path | None:
    """
    Replace live Quick Select tree (and optional index) from a snapshot source.

    When ``source_index`` is omitted, callers should rebuild the index from the tree.
    Returns the pre-restore backup directory when ``backup_before`` is True.
    """
    if not source_qa_dir.is_dir():
        raise FileNotFoundError(f"Quick Select source not found: {source_qa_dir}")

    pre_backup: Path | None = None
    if backup_before and index_path is not None:
        pre_backup = snapshot_favorites(qa_root, index_path, label="pre-restore")

    if qa_root.exists():
        shutil.rmtree(qa_root)
    shutil.copytree(source_qa_dir, qa_root)

    if source_index is not None and source_index.is_file() and index_path is not None:
        index_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_index, index_path)

    return pre_backup


def rebuild_index_from_qa_tree(
    index: FavoritesIndex,
    qa_root: Path,
    stem_to_stable_keys: dict[str, list[str]],
    *,
    patch_dirs: list[Path] | tuple[Path, ...] | None = None,
) -> tuple[int, list[str]]:
    """Rebuild favorites v2 index entries from .fxp files under ``qa_root``."""
    new_entries: dict[str, dict] = {}
    new_folders: list[str] = []
    errors: list[str] = []

    for fxp in sorted(qa_root.rglob("*.fxp")):
        if not fxp.is_file():
            continue
        rel = fxp.relative_to(qa_root)
        folder_key = "/".join(rel.parent.parts) if rel.parent.parts else ""

        stable_key: str | None = None
        if patch_dirs:
            stable_key = stable_key_from_absolute_path(fxp, patch_dirs)
        if not stable_key:
            candidates = stem_to_stable_keys.get(fxp.stem, [])
            if len(candidates) == 1:
                stable_key = candidates[0]
            elif len(candidates) > 1:
                errors.append(f"ambiguous stem {fxp.stem}: {candidates}")
                continue
            else:
                errors.append(f"no stable_key for {fxp}")
                continue

        if folder_key and folder_key not in new_folders:
            new_folders.append(folder_key)
        new_entries[stable_key] = {
            "folder": folder_key,
            "dest_path": str(fxp.resolve()),
            "added_at": datetime.now(timezone.utc).isoformat(),
        }

    index._entries = new_entries
    index._folders = new_folders
    index.save()
    return len(new_entries), errors
