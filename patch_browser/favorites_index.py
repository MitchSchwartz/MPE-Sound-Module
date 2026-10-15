"""Favorites index (v2): a list of pointers to patches, never copies of them.

A favorite is a stable key plus a folder and an optional order. The patch it
names is wherever the library keeps it; nothing is copied anywhere when a patch
is hearted, so a favorite plays the file the library holds now and shares every
per-patch setting with that file (the settings are keyed by the same stable key).

File shape::

    {"version": 2,
     "folders": ["Rhythms"],
     "entries": {"thirdparty:Dan/Rhythms/Chime": {"folder": "Rhythms", "order": 3, "added_at": "..."}},
     "imported": ["user:Quick Access/Pc Saved"]}

``imported`` records the files from the favorites folder on disk that the scan
already favorited once (the PC workflow: save a patch into that folder in Surge
on a computer). Without it, removing such a favorite would bring it straight back
on the next scan.

A version-1 file (``dest_path`` copies) still loads: each entry's key already
names the source patch, so it reads as a pointer, and the copy path is ignored.
Such an index is ``legacy``. It is saved back as version 1 and the scan does not
auto-favorite from the folder, because until the migration runs that folder still
holds the old copies. ``scripts/migrate-favorites-pointers.py`` upgrades it.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from patch_browser.json_store import atomic_write_json, read_json_dict

QA_ROOT_FOLDER = ""
LEGACY_LIKED_FOLDER = "Liked"
DEFAULT_FAVORITES_FOLDER = QA_ROOT_FOLDER
INDEX_VERSION = 2

#: Third-party patches used to sit one level deeper; keys written then still
#: appear in old indexes and settings stores.
NESTED_THIRDPARTY_PREFIX = "thirdparty:patches_3rdparty/"


def normalize_key(key: str) -> str:
    """Map a legacy key shape onto today's (``thirdparty:patches_3rdparty/X`` -> ``thirdparty:X``)."""
    if key.startswith(NESTED_THIRDPARTY_PREFIX) and len(key) > len(NESTED_THIRDPARTY_PREFIX):
        return "thirdparty:" + key[len(NESTED_THIRDPARTY_PREFIX) :]
    return key


def is_legacy_liked_folder(name: str) -> bool:
    """True for the deprecated Liked bucket (Quick Select root replaces it)."""
    return name.strip().casefold() == LEGACY_LIKED_FOLDER.casefold()


def is_protected_qa_folder(folder_key: str) -> bool:
    """Folders that cannot be renamed or deleted from the touch UI."""
    key = folder_key.strip()
    if not key:
        return True
    first = key.split("/", 1)[0]
    return is_legacy_liked_folder(first)


def qa_folder_key_from_target_inner(inner_segments: tuple[str, ...]) -> str:
    return "/".join(inner_segments)


def qa_folder_segments(folder_key: str) -> tuple[str, ...]:
    folder_key = folder_key.strip()
    if not folder_key:
        return ()
    return tuple(part for part in folder_key.split("/") if part)


def qa_folder_key_for_library(category: str, inner_segments: tuple[str, ...]) -> str:
    """Quick Select folder path for a library long-press target."""
    from patch_browser.patch_scanner import favorites_folder_matches

    if inner_segments:
        return "/".join(inner_segments)
    if category and not favorites_folder_matches(category):
        return category.lstrip("!")
    return ""


def default_favorites_index_path() -> Path:
    env = os.environ.get("MPE_FAVORITES_INDEX_FILE", "").strip()
    if env:
        return Path(env).expanduser()
    return Path.home() / ".patch_browser_favorites.json"


def _liked_to_root(folder: str) -> str:
    prefix = f"{LEGACY_LIKED_FOLDER}/"
    if folder == LEGACY_LIKED_FOLDER:
        return QA_ROOT_FOLDER
    if folder.startswith(prefix):
        return folder[len(prefix) :]
    return folder


class FavoritesIndex:
    """In-memory index backed by ~/.patch_browser_favorites.json."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_favorites_index_path()
        self._folders: list[str] = []
        self._entries: dict[str, dict[str, Any]] = {}
        self._imported: set[str] = set()
        self.legacy = False
        self.load()

    # ------------------------------------------------------------------ disk

    def load(self) -> None:
        if not self.path.exists():
            return
        raw = read_json_dict(self.path, label="favorites index")
        try:
            version = int(raw.get("version", 1))
        except (TypeError, ValueError):
            version = 1
        self.legacy = version < INDEX_VERSION
        folders = raw.get("folders")
        self._folders = []
        if isinstance(folders, list):
            for f in folders:
                name = _liked_to_root(str(f).strip())
                if name and name not in self._folders:
                    self._folders.append(name)
        self._entries = {}
        entries = raw.get("entries")
        if isinstance(entries, dict):
            for key, entry in entries.items():
                if not isinstance(entry, dict):
                    continue
                row = dict(entry)
                row["folder"] = _liked_to_root(str(row.get("folder") or "").strip())
                if not self.legacy:
                    row.pop("dest_path", None)
                # A later key spelling never replaces an earlier one for the same patch.
                self._entries.setdefault(normalize_key(str(key)), row)
        for row in self._entries.values():
            folder = row["folder"]
            if folder and folder not in self._folders:
                self._folders.append(folder)
        imported = raw.get("imported")
        self._imported = {normalize_key(str(k)) for k in imported} if isinstance(imported, list) else set()

    def payload(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "version": 1 if self.legacy else INDEX_VERSION,
            "folders": list(self._folders),
            "entries": self._entries,
        }
        if self._imported:
            out["imported"] = sorted(self._imported)
        return out

    def upgrade(self) -> None:
        """Make this a version-2 index: entries are pointers, copy paths dropped."""
        self.legacy = False
        for row in self._entries.values():
            row.pop("dest_path", None)

    def save(self) -> None:
        atomic_write_json(self.path, self.payload())

    # ---------------------------------------------------------------- reading

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
        """A dict lookup. The heart is drawn per visible row per frame."""
        return bool(stable_key and stable_key in self._entries)

    def folder_for(self, stable_key: str) -> str | None:
        entry = self._entries.get(stable_key)
        if not entry:
            return None
        folder = entry.get("folder")
        return str(folder) if folder else None

    def was_imported(self, stable_key: str) -> bool:
        return stable_key in self._imported

    # --------------------------------------------------------------- mutation

    def add(
        self,
        stable_key: str,
        *,
        folder: str = DEFAULT_FAVORITES_FOLDER,
        added_at: str | None = None,
        order: int | None = None,
        derived_from: str | None = None,
    ) -> None:
        folder_name = folder.strip()
        if folder_name:
            self.ensure_folder(folder_name)
        row: dict[str, Any] = {
            "folder": folder_name,
            "added_at": added_at or datetime.now(timezone.utc).isoformat(),
        }
        if order is not None:
            row["order"] = int(order)
        if derived_from:
            row["derived_from"] = derived_from
        self._entries[stable_key] = row

    def remove(self, stable_key: str) -> dict[str, Any] | None:
        entry = self._entries.pop(stable_key, None)
        return dict(entry) if isinstance(entry, dict) else None

    def move(self, stable_key: str, folder: str) -> bool:
        """Change one favorite's folder. The patch file is not touched."""
        entry = self._entries.get(stable_key)
        if entry is None:
            return False
        name = folder.strip()
        if name:
            self.ensure_folder(name)
        entry["folder"] = name
        return True

    def set_order(self, stable_key: str, order: int | None) -> None:
        entry = self._entries.get(stable_key)
        if entry is None:
            return
        if order is None:
            entry.pop("order", None)
        else:
            entry["order"] = int(order)

    def mark_imported(self, stable_key: str) -> None:
        self._imported.add(stable_key)

    def ensure_folder(self, name: str) -> None:
        folder = name.strip()
        if not folder:
            return
        if folder not in self._folders:
            self._folders.append(folder)

    def create_folder(self, name: str) -> None:
        folder = name.strip()
        if not folder:
            raise ValueError("folder name required")
        self.ensure_folder(folder)

    def rename_folder(self, old_name: str, new_name: str) -> None:
        old = old_name.strip()
        new = new_name.strip()
        if not old or not new:
            raise ValueError("folder names required")
        if is_protected_qa_folder(old):
            raise ValueError(f"cannot rename {LEGACY_LIKED_FOLDER!r} — use Quick Select root")
        if old not in self._folders:
            raise ValueError(f"unknown folder {old!r}")
        if new in self._folders and new != old:
            raise ValueError(f"destination folder already exists: {new!r}")
        old_prefix = f"{old}/"
        self._folders = [
            new if f == old else (new + f[len(old) :] if f.startswith(old_prefix) else f)
            for f in self._folders
        ]
        for entry in self._entries.values():
            entry_folder = str(entry.get("folder") or "")
            if entry_folder == old:
                entry["folder"] = new
            elif entry_folder.startswith(old_prefix):
                entry["folder"] = new + entry_folder[len(old) :]

    def delete_folder(self, name: str) -> None:
        folder = name.strip()
        if not folder:
            raise ValueError("cannot delete Quick Select root")
        if is_protected_qa_folder(folder):
            raise ValueError(f"{LEGACY_LIKED_FOLDER} is legacy — hearts use Quick Select root")
        prefix = f"{folder}/"
        for entry in self._entries.values():
            entry_folder = str(entry.get("folder") or "")
            if entry_folder == folder or entry_folder.startswith(prefix):
                raise ValueError(f"folder {folder!r} is not empty")
        self._folders = [f for f in self._folders if f != folder and not f.startswith(prefix)]
