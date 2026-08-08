"""Favorites v2 index — stable_key → Quick Access folder copy."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from patch_browser.json_store import atomic_write_json, read_json_dict

QA_ROOT_FOLDER = ""
LEGACY_LIKED_FOLDER = "Liked"
DEFAULT_FAVORITES_FOLDER = QA_ROOT_FOLDER
INDEX_VERSION = 1


def qa_folder_segments(folder_key: str) -> tuple[str, ...]:
    folder_key = folder_key.strip()
    if not folder_key:
        return ()
    return tuple(part for part in folder_key.split("/") if part)


def qa_folder_dest_dir(qa_root: Path, folder_key: str) -> Path:
    segments = qa_folder_segments(folder_key)
    return qa_root.joinpath(*segments) if segments else qa_root


def qa_folder_key_for_library(category: str, inner_segments: tuple[str, ...]) -> str:
    """Mirror a library browse folder as a Quick Select subfolder path."""
    from patch_browser.patch_scanner import favorites_folder_matches

    parts: list[str] = []
    if category and not favorites_folder_matches(category):
        parts.append(category.lstrip("!"))
    parts.extend(inner_segments)
    return "/".join(parts)


def default_favorites_index_path() -> Path:
    env = os.environ.get("MPE_FAVORITES_INDEX_FILE", "").strip()
    if env:
        return Path(env).expanduser()
    return Path.home() / ".patch_browser_favorites.json"


@dataclass
class MigrationPlanItem:
    source_path: Path
    dest_path: Path
    stable_key: str
    folder: str = LEGACY_LIKED_FOLDER


@dataclass
class MigrationPlan:
    items: list[MigrationPlanItem] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def move_count(self) -> int:
        return len(self.items)


class FavoritesIndex:
    """In-memory index backed by ~/.patch_browser_favorites.json."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_favorites_index_path()
        self._folders: list[str] = []
        self._entries: dict[str, dict[str, Any]] = {}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        raw = read_json_dict(self.path, label="favorites index")
        folders = raw.get("folders")
        if isinstance(folders, list):
            self._folders = [str(f) for f in folders if str(f).strip()]
        else:
            self._folders = []
        entries = raw.get("entries")
        self._entries = {}
        if isinstance(entries, dict):
            for key, entry in entries.items():
                if isinstance(entry, dict):
                    self._entries[str(key)] = dict(entry)

    def save(self) -> None:
        payload = {
            "version": INDEX_VERSION,
            "folders": list(self._folders),
            "entries": self._entries,
        }
        atomic_write_json(self.path, payload)

    @property
    def folders(self) -> list[str]:
        return list(self._folders)

    @property
    def entries(self) -> dict[str, dict[str, Any]]:
        return dict(self._entries)

    def get_entry(self, stable_key: str) -> dict[str, Any] | None:
        entry = self._entries.get(stable_key)
        return dict(entry) if isinstance(entry, dict) else None

    def is_favorited(self, stable_key: str | None) -> bool:
        return bool(stable_key and stable_key in self._entries)

    def dest_path_for(self, stable_key: str) -> Path | None:
        entry = self.get_entry(stable_key)
        if not entry:
            return None
        dest = entry.get("dest_path")
        return Path(str(dest)) if dest else None

    def folder_for(self, stable_key: str) -> str | None:
        entry = self.get_entry(stable_key)
        if not entry:
            return None
        folder = entry.get("folder")
        return str(folder) if folder else None

    def add(
        self,
        stable_key: str,
        *,
        folder: str = DEFAULT_FAVORITES_FOLDER,
        dest_path: str | Path,
        added_at: str | None = None,
    ) -> None:
        folder_name = folder.strip()
        if folder_name:
            self.ensure_folder(folder_name)
        self._entries[stable_key] = {
            "folder": folder_name,
            "dest_path": str(dest_path),
            "added_at": added_at or datetime.now(timezone.utc).isoformat(),
        }

    def remove(self, stable_key: str) -> dict[str, Any] | None:
        entry = self._entries.pop(stable_key, None)
        return dict(entry) if isinstance(entry, dict) else None

    def find_stable_key_by_dest(self, dest_path: str | Path) -> str | None:
        try:
            target = Path(dest_path).resolve()
        except OSError:
            target = Path(dest_path)
        for key, entry in self._entries.items():
            raw = entry.get("dest_path")
            if not raw:
                continue
            try:
                if Path(raw).resolve() == target:
                    return key
            except OSError:
                if str(raw) == str(dest_path):
                    return key
        return None

    def ensure_folder(self, name: str) -> None:
        folder = name.strip()
        if not folder:
            return
        if folder not in self._folders:
            self._folders.append(folder)

    def create_folder(self, name: str, *, qa_root: Path) -> Path:
        folder = name.strip()
        if not folder:
            raise ValueError("folder name required")
        self.ensure_folder(folder)
        path = qa_folder_dest_dir(qa_root, folder)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def rename_folder(self, old_name: str, new_name: str, *, qa_root: Path) -> None:
        old = old_name.strip()
        new = new_name.strip()
        if not old or not new:
            raise ValueError("folder names required")
        if old not in self._folders:
            raise ValueError(f"unknown folder {old!r}")
        src = qa_folder_dest_dir(qa_root, old)
        dst = qa_folder_dest_dir(qa_root, new)
        if dst.exists() and src.exists() and not src.samefile(dst):
            raise ValueError(f"destination folder already exists: {new!r}")
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            src.rename(dst)
        idx = self._folders.index(old)
        self._folders[idx] = new
        old_prefix = f"{old}/"
        for entry in self._entries.values():
            entry_folder = str(entry.get("folder") or "")
            if entry_folder == old:
                entry["folder"] = new
            elif entry_folder.startswith(old_prefix):
                entry["folder"] = new + entry_folder[len(old) :]
            dest = entry.get("dest_path")
            if isinstance(dest, str):
                normalized = dest.replace("\\", "/")
                old_seg = f"/{old}/"
                if old_seg in normalized:
                    entry["dest_path"] = normalized.replace(old_seg, f"/{new}/", 1)

    def delete_folder(self, name: str, *, qa_root: Path) -> None:
        folder = name.strip()
        if not folder:
            raise ValueError("cannot delete Quick Select root")
        if any(str(entry.get("folder") or "") == folder for entry in self._entries.values()):
            raise ValueError(f"folder {folder!r} is not empty")
        path = qa_folder_dest_dir(qa_root, folder)
        if path.exists():
            if any(path.iterdir()):
                raise ValueError(f"folder {folder!r} is not empty on disk")
            path.rmdir()
        if folder in self._folders:
            self._folders.remove(folder)

    def plan_flat_root_migration(
        self,
        qa_root: Path,
        stem_to_stable_keys: dict[str, list[str]],
        *,
        target_folder: str = LEGACY_LIKED_FOLDER,
    ) -> MigrationPlan:
        """Plan moving flat Quick Access root copies into target_folder."""
        plan = MigrationPlan()
        if not qa_root.is_dir():
            plan.errors.append(f"Quick Access root not found: {qa_root}")
            return plan

        dest_dir = qa_root / target_folder
        seen_stems: set[str] = set()
        for fxp in sorted(qa_root.glob("*.fxp")):
            stem = fxp.stem
            if stem.lower() in seen_stems:
                plan.errors.append(f"duplicate flat copy stem {stem!r}")
                continue
            seen_stems.add(stem.lower())
            candidates = stem_to_stable_keys.get(stem, [])
            if not candidates:
                plan.errors.append(f"no stable_key for flat copy {fxp.name!r}")
                continue
            if len(candidates) > 1:
                plan.errors.append(
                    f"ambiguous stable_key for {fxp.name!r}: {', '.join(candidates[:3])}"
                    + ("…" if len(candidates) > 3 else "")
                )
                continue
            stable_key = candidates[0]
            if self.is_favorited(stable_key):
                plan.skipped.append(f"{fxp.name} already indexed as {stable_key}")
                continue
            dest = dest_dir / fxp.name
            if dest.exists():
                plan.errors.append(f"destination already exists: {dest}")
                continue
            plan.items.append(
                MigrationPlanItem(
                    source_path=fxp,
                    dest_path=dest,
                    stable_key=stable_key,
                    folder=target_folder,
                )
            )
        return plan

    def apply_migration_plan(
        self,
        plan: MigrationPlan,
        *,
        qa_root: Path,
        backup_dir: Path | None = None,
    ) -> None:
        if not plan.ok:
            raise ValueError("migration plan has errors — fix before apply")
        if backup_dir is not None:
            backup_dir.mkdir(parents=True, exist_ok=True)
            if self.path.exists():
                shutil.copy2(self.path, backup_dir / self.path.name)
            if qa_root.is_dir():
                shutil.copytree(qa_root, backup_dir / qa_root.name, dirs_exist_ok=True)
        (qa_root / LEGACY_LIKED_FOLDER).mkdir(parents=True, exist_ok=True)
        for item in plan.items:
            item.source_path.rename(item.dest_path)
            self.add(
                item.stable_key,
                folder=item.folder,
                dest_path=item.dest_path,
            )
        self.save()
