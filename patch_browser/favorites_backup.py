"""Favorites index backup and restore.

The index is the whole favorites state: pointers hold no patch files, so there is
no tree to snapshot. Snapshots default to a directory beside the index file.
"""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

BACKUP_DIR_NAME = ".patch_browser_favorites_backups"


def default_backup_root(index_path: Path) -> Path:
    return index_path.parent / BACKUP_DIR_NAME


def snapshot_index(
    index_path: Path,
    *,
    dest_dir: Path | None = None,
    label: str = "",
) -> Path:
    """Copy the favorites index JSON into a timestamped directory; return it."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    suffix = f"-{label}" if label else ""
    out = dest_dir or (default_backup_root(index_path) / f"{stamp}{suffix}")
    out.mkdir(parents=True, exist_ok=True)
    if index_path.is_file():
        shutil.copy2(index_path, out / index_path.name)
    (out / "README.txt").write_text(
        f"Favorites index snapshot {stamp}\nindex={index_path.name}\n", encoding="utf-8"
    )
    return out


def restore_index(index_path: Path, snapshot_dir: Path, *, backup_before: bool = True) -> Path | None:
    """Put a snapshot's index back. Returns the pre-restore snapshot, if one was made."""
    source = snapshot_dir / index_path.name
    if not source.is_file():
        raise FileNotFoundError(f"no {index_path.name} in {snapshot_dir}")
    pre = snapshot_index(index_path, label="pre-restore") if backup_before and index_path.is_file() else None
    index_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = index_path.with_name(f".{index_path.name}.restore.tmp")
    shutil.copy2(source, tmp)
    tmp.replace(index_path)
    return pre
