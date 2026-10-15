"""Patch scanning and favorites helpers (no GPIO / OLED dependencies)."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

from patch_browser.patch_normalization import log_missing_normalization_summary
from patch_browser.patch_sidecar_migrate import migrate_loader_sidecars
from patch_browser.patch_identity import (
    build_folder_tree,
    category_and_inner_segments,
    make_patch_entry,
    patch_root_label,
    stable_key_for_relative_path,
)
from patch_browser.favorites_sections import OTHER_GROUP, favorite_group, favorite_row_key
from patch_browser.patch_metadata import PatchMetadataIndex
from patch_browser.favorites_index import (
    DEFAULT_FAVORITES_FOLDER,
    FavoritesIndex,
    is_legacy_liked_folder,
)


@dataclass
class ScannerConfig:
    favorites_name: str = field(
        default_factory=lambda: os.environ.get("MPE_FAVORITES_NAME", "!Quick Access")
    )


SCANNER_CONFIG = ScannerConfig()
FAVORITES_NAME = SCANNER_CONFIG.favorites_name
LAST_PATCH_FILE = Path.home() / ".patch_browser_last_patch.json"


def _is_readable_dir(path: Path) -> bool:
    """Probe a candidate directory without raising.

    Path.is_dir() propagates PermissionError when a parent is unreadable, so a
    plain `if candidate.is_dir()` crashes for any user who cannot traverse the
    path — which is every user except the appliance owner. Probing for a
    directory must treat "cannot tell" as "not there"; only the owner ever saw
    this work.
    """
    try:
        return path.is_dir()
    except OSError:
        return False


def resolve_user_patches_dir() -> Path:
    """User patch library — env override, then Documents (PC), then Linux Surge default."""
    surge_docs = os.environ.get("MPE_SURGE_DOCS", "").strip()
    if surge_docs:
        candidate = Path(surge_docs) / "Patches"
        if _is_readable_dir(candidate):
            return candidate

    documents = Path.home() / "Documents" / "Surge XT" / "Patches"
    if _is_readable_dir(documents):
        return documents

    linux_default = Path.home() / ".Surge Synth Team" / "Surge XT" / "Patches"
    return linux_default


SURGE_PATCH_DIRS = [
    Path.home() / "surge" / "resources" / "data" / "patches_factory",
    Path.home() / "surge" / "resources" / "data" / "patches_3rdparty",
    resolve_user_patches_dir(),
]


# Folders that are never patch roots. ``appliance-state`` holds device captures,
# whose favorites backups contain hundreds of .fxp copies; ``state`` is where the
# personal repo keeps them now. Neither is ever a library of patches.
NON_PATCH_ASSET_DIRS = frozenset(
    {"patches", "user-data", "binaries", "appliance-state", "state"}
)
# Directory names pruned wherever the scan meets them, in any patch root.
NON_PATCH_DIR_NAMES = frozenset({"appliance-state"})


def resolve_personal_repo(module_repo: Path | None = None) -> Path | None:
    """Sibling MPE-Library / MPE-Personal assets repo, or ``MPE_PERSONAL_REPO`` env."""
    override = os.environ.get("MPE_PERSONAL_REPO", "").strip()
    if override:
        candidate = Path(override)
        return candidate if candidate.is_dir() else None

    root = module_repo or Path(__file__).resolve().parents[1]
    for name in ("MPE-Library", "MPE-Personal", "mpe-assets"):
        candidate = root.parent / name
        if candidate.is_dir():
            return candidate
    return None


def resolve_library_patch_dirs(module_repo: Path | None = None) -> list[Path]:
    """
    Patch scan roots from the private MPE-Library assets repo (PC / backup layout).

    Includes factory, third-party, user Patches, and CC0 collection folders under
    ``assets/`` that contain ``.fxp`` files.
    """
    personal = resolve_personal_repo(module_repo)
    if personal is None:
        return []

    assets = personal / "assets"
    if not assets.is_dir():
        return []

    dirs: list[Path] = []
    for rel in (
        "patches/patches_factory",
        "patches/third-party/patches_3rdparty",
        "user-data/Patches",
    ):
        candidate = assets / rel
        if candidate.is_dir():
            dirs.append(candidate)

    skip = set(NON_PATCH_ASSET_DIRS)
    for child in sorted(assets.iterdir()):
        if not child.is_dir() or child.name in skip:
            continue
        if any(child.rglob("*.fxp")):
            dirs.append(child)

    return dirs


def resolve_patch_scan_dirs(module_repo: Path | None = None) -> list[Path]:
    """Library assets repo when present, else live Surge install paths."""
    library_dirs = resolve_library_patch_dirs(module_repo)
    if library_dirs:
        return library_dirs
    return [p for p in SURGE_PATCH_DIRS if p.exists()]


def favorites_display_name(name=None):
    """Browser category label — leading ! sorts first. Idempotent if name already has !."""
    n = name if name is not None else FAVORITES_NAME
    return n if n.startswith("!") else f"!{n}"


def favorites_folder_matches(name):
    """True for the virtual favorites category (the label with its leading !).

    The category is built from the favorites index and has no folder on disk. A
    real folder of the same name under the user patch root is only the place a
    patch saved on a computer lands (``is_import_folder_name``); it is an ordinary
    category, so the two never collide.
    """
    return name.casefold() == favorites_display_name().casefold()


def is_import_folder_name(name):
    """True for the on-disk folder whose patches are favorited on scan (PC workflow)."""
    return name.lstrip("!").casefold() == FAVORITES_NAME.lstrip("!").casefold()


class PatchScanner:
    """Scans and organizes Surge patches by category."""

    def __init__(self, patch_dirs, last_patch_file=LAST_PATCH_FILE):
        self.patch_dirs = patch_dirs
        self.last_patch_file = last_patch_file
        self.patches = {}
        self.patches_by_stable_key: dict[str, dict] = {}
        self.patches_by_path: dict[str, dict] = {}
        self.folder_tree: dict[str, dict] = {}
        self.metadata_index = PatchMetadataIndex()
        self.favorites_index = FavoritesIndex()
        self._sidecar_loader = None
        self.scan_complete = threading.Event()
        self.scan_lock = threading.Lock()
        self.scan_thread = None

    def scan_patches(self):
        """Scan all patch directories and organize by category."""
        print("Scanning Surge patches...")
        total_patches = 0
        patches_by_key: dict[str, dict] = {}
        patches_by_path: dict[str, dict] = {}
        grouped: dict[str, list[dict]] = {}
        import_candidates: list[dict] = []

        for patch_dir in self.patch_dirs:
            if not patch_dir.exists():
                print(f"Warning: Patch directory not found: {patch_dir}")
                continue

            root_label = patch_root_label(patch_dir)

            for root, _dirs, files in os.walk(patch_dir):
                _dirs[:] = [d for d in _dirs if d not in NON_PATCH_DIR_NAMES]
                folder_rel = Path(root).relative_to(patch_dir)
                category, _inner = category_and_inner_segments(folder_rel)
                import_folder = root_label == "user" and is_import_folder_name(category)
                if import_folder:
                    # Not the virtual category: the label's "!" is what tells them apart.
                    category = category.lstrip("!")

                fxp_files = [f for f in files if f.lower().endswith(".fxp")]
                if not fxp_files:
                    continue

                if category not in grouped:
                    grouped[category] = []

                for fxp_file in fxp_files:
                    patch_path = Path(root) / fxp_file
                    path_key = str(patch_path.resolve())
                    if path_key in patches_by_path:
                        continue

                    patch_name = fxp_file.replace(".fxp", "").replace(".FXP", "")
                    entry = make_patch_entry(
                        name=patch_name,
                        path=patch_path,
                        patch_dir=patch_dir,
                        root_label=root_label,
                        category_override=category,
                    )
                    patches_by_path[path_key] = entry
                    patches_by_key[entry["stable_key"]] = entry
                    grouped[category].append(entry)
                    if import_folder:
                        import_candidates.append(entry)
                    total_patches += 1

        sorted_patches = {
            k: sorted(v, key=lambda x: (x["name"].casefold(), x["path"]))
            for k, v in sorted(grouped.items(), key=lambda item: (item[0], item[0]))
        }

        self.metadata_index.reload()
        self.metadata_index.enrich_all(patches_by_key)
        self._import_favorites_folder(import_candidates)
        fav_rows = self._favorite_rows(patches_by_key)
        sorted_patches = {favorites_display_name(): fav_rows, **sorted_patches}
        folder_tree = self._tree_with_favorite_folders(build_folder_tree(sorted_patches))

        with self.scan_lock:
            self.patches = sorted_patches
            self.patches_by_stable_key = patches_by_key
            self.patches_by_path = patches_by_path
            self.folder_tree = folder_tree

        missing = sum(1 for row in fav_rows if row.get("missing"))
        if missing:
            print(f"Favorites: {missing} point at patches that are not in the library")

        print(f"Found {total_patches} patches in {len(self.patches)} categories")
        cat_names = list(self.patches.keys())
        print(f"First 3 categories: {cat_names[:3]}")
        return self.patches

    def bind_sidecar_loader(self, loader) -> None:
        """Attach loader so post-scan sidecar migration can run."""
        self._sidecar_loader = loader
        for store in (loader.normalization, loader.hold, loader.pressure, loader.fx_store):
            store.set_patch_dirs(self.patch_dirs)

    def scan_patches_background(self):
        """Start background thread to scan patches."""

        def _scan_worker():
            try:
                self.scan_patches()
                all_patches: list[dict] = []
                with self.scan_lock:
                    for patches in self.patches.values():
                        # Favorites rows are views of patches listed elsewhere.
                        all_patches.extend(pt for pt in patches if not pt.get("favorite_view"))
                log_missing_normalization_summary(all_patches)
                if self._sidecar_loader is not None:
                    warnings = migrate_loader_sidecars(
                        self._sidecar_loader,
                        all_patches,
                        self.patch_dirs,
                    )
                    for msg in warnings:
                        print(f"Sidecar migration: {msg}")
                self.scan_complete.set()
                print("Background patch scan complete")
            except Exception as e:
                print(f"Error during background scan: {e}")
                with self.scan_lock:
                    if not self.patches:
                        self.patches = {
                            "Error": [{"name": "Scan failed", "path": "", "category": "Error"}]
                        }
                self.scan_complete.set()

        self.scan_thread = threading.Thread(target=_scan_worker, daemon=True, name="PatchScanner")
        self.scan_thread.start()
        print("Started background patch scanning...")

    def wait_for_scan(self, timeout=None):
        return self.scan_complete.wait(timeout=timeout)

    def quick_scan_category(self, category_path):
        patches = []
        if not category_path.exists():
            return patches

        patch_dir = self._patch_dir_for_path(category_path)
        root_label = patch_root_label(patch_dir) if patch_dir else "user"
        folder_rel = (
            category_path.relative_to(patch_dir)
            if patch_dir is not None
            else Path(category_path.name)
        )
        category_name, _inner = category_and_inner_segments(folder_rel)
        if is_import_folder_name(category_name):
            category_name = category_name.lstrip("!")

        for fxp_file in sorted(category_path.rglob("*.fxp")):
            if NON_PATCH_DIR_NAMES.intersection(fxp_file.relative_to(category_path).parts):
                continue
            patch_name = fxp_file.stem
            if patch_dir is not None:
                entry = make_patch_entry(
                    name=patch_name,
                    path=fxp_file,
                    patch_dir=patch_dir,
                    root_label=root_label,
                    category_override=category_name,
                )
            else:
                entry = {
                    "name": patch_name,
                    "path": str(fxp_file),
                    "category": category_name,
                    "folder_segments": (),
                    "inner_segments": (),
                    "relative_path": fxp_file.name,
                    "patch_root": root_label,
                    "stable_key": stable_key_for_relative_path(
                        root_label, Path(patch_name)
                    ),
                }
            patches.append(entry)

        for patch in patches:
            self.metadata_index.enrich_patch(patch)

        return sorted(patches, key=lambda x: (x["name"].casefold(), x["path"]))

    def save_last_patch(self, category, patch_path):
        try:
            state = {"category": category, "patch_path": str(patch_path)}
            with open(self.last_patch_file, "w") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            print(f"Warning: Could not save last patch state: {e}")

    def load_last_patch(self):
        if self.last_patch_file.exists():
            try:
                with open(self.last_patch_file, "r") as f:
                    state = json.load(f)
                    patch_path = Path(state["patch_path"])
                    if patch_path.exists():
                        return state
                    print(f"Last patch no longer exists: {patch_path}")
            except Exception as e:
                print(f"Warning: Could not load last patch state: {e}")
        return None

    def get_categories(self):
        with self.scan_lock:
            return list(self.patches.keys())

    def get_patches_in_category(self, category):
        with self.scan_lock:
            return list(self.patches.get(category, []))

    def get_patch_by_stable_key(self, stable_key: str) -> dict | None:
        with self.scan_lock:
            return self.patches_by_stable_key.get(stable_key)

    def get_patch_by_path(self, path: str | Path) -> dict | None:
        with self.scan_lock:
            try:
                return self.patches_by_path.get(str(Path(path).resolve()))
            except OSError:
                return self.patches_by_path.get(str(path))

    def _folder_tree_node(self, category: str, inner_segments: tuple[str, ...] = ()) -> dict | None:
        with self.scan_lock:
            node = self.folder_tree.get(category)
            if node is None:
                return None
            for segment in inner_segments:
                node = node.get("children", {}).get(segment)
                if node is None:
                    return None
            return node

    def get_subfolders(
        self, category: str, inner_segments: tuple[str, ...] = ()
    ) -> list[str]:
        node = self._folder_tree_node(category, inner_segments)
        if not node:
            return []
        subs = sorted(node.get("children", {}).keys(), key=str.casefold)
        if favorites_folder_matches(category):
            subs = [name for name in subs if not is_legacy_liked_folder(name)]
        return subs

    def get_patches_in_folder(
        self, category: str, inner_segments: tuple[str, ...] = ()
    ) -> list[dict]:
        node = self._folder_tree_node(category, inner_segments)
        if not node:
            return []
        return list(node.get("patches", []))

    def _patch_dir_for_path(self, path: Path) -> Path | None:
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        for patch_dir in self.patch_dirs:
            if not patch_dir.exists():
                continue
            try:
                resolved.relative_to(patch_dir.resolve())
                return patch_dir
            except ValueError:
                continue
        return None

    # ------------------------------------------------------------- favorites
    #
    # A favorite is a pointer (favorites_index). Its row in the favorites category
    # is a *view* of the source patch: a copy of the source's dict that keeps the
    # source's stable_key and path, so loading it and every per-patch setting read
    # and write go to the source with no special case, and that carries the
    # favorites label and the entry's folder as its category and inner segments.
    # Views are never put in patches_by_path or patches_by_stable_key.

    def _stable_key_for_path(self, patch_path: str | Path | None) -> str | None:
        if not patch_path:
            return None
        entry = self.get_patch_by_path(patch_path)
        if entry and entry.get("stable_key"):
            return str(entry["stable_key"])
        return None

    def favorite_stable_key_for_patch(self, patch: dict | None) -> str | None:
        """The key a patch dict (library row or favorites view) is favorited under."""
        if not patch:
            return None
        stable_key = patch.get("stable_key")
        if stable_key:
            return str(stable_key)
        return self._stable_key_for_path(patch.get("path"))

    def _favorite_view(self, key: str, entry: dict, source: dict | None) -> dict:
        segments = tuple(part for part in str(entry.get("folder") or "").split("/") if part)
        label = favorites_display_name()
        if source is not None:
            view = dict(source)
            view["missing"] = False
        else:
            root, _sep, rest = key.partition(":")
            view = {
                "name": Path(rest or key).name,
                "path": "",
                "relative_path": "",
                "patch_root": root,
                "stable_key": key,
                "instrument_primary": "other",
                "missing": True,
            }
        view["category"] = label
        view["folder_segments"] = segments
        view["inner_segments"] = segments
        view["favorite_view"] = True
        return view

    def _favorite_rows(self, patches_by_key: dict[str, dict]) -> list[dict]:
        """Views for every index entry: ordered ones first, then A-Z by name."""
        rows: list[tuple[tuple, dict]] = []
        for key, entry in self.favorites_index.entries.items():
            view = self._favorite_view(key, entry, patches_by_key.get(key))
            order = entry.get("order")
            has_order = isinstance(order, int) and not isinstance(order, bool)
            rows.append(((0, order, "", key) if has_order else (1, 0, view["name"].casefold(), key), view))
        rows.sort(key=lambda item: item[0])
        return [view for _sort, view in rows]

    def _tree_with_favorite_folders(self, tree: dict[str, dict]) -> dict[str, dict]:
        """Folders created in the index show even while they hold no favorite."""
        label = favorites_display_name()
        for folder in self.favorites_index.folders:
            node = tree.setdefault(label, {"patches": [], "children": {}})
            for segment in (part for part in folder.split("/") if part):
                node = node["children"].setdefault(segment, {"patches": [], "children": {}})
        return tree

    def _import_favorites_folder(self, candidates: list[dict]) -> int:
        """Favorite, once, each patch found in the favorites folder on disk.

        The PC workflow: a patch saved into that folder from Surge on a computer is a
        user patch and becomes a favorite on the next scan. ``imported`` remembers it,
        so removing the favorite is not undone by the following scan. A legacy index
        is skipped: until the migration runs, that folder still holds the old copies.
        """
        index = self.favorites_index
        if index.legacy or not candidates:
            return 0
        added = 0
        for entry in candidates:
            key = entry["stable_key"]
            if index.was_imported(key):
                continue
            index.mark_imported(key)
            if not index.is_favorited(key):
                index.add(key, folder="/".join(entry.get("inner_segments") or ()))
            added += 1
        if added:
            index.save()
            print(f"Favorited {added} patch(es) saved into the {FAVORITES_NAME.lstrip('!')} folder")
        return added

    def invalidate_favorite_cache(self) -> None:
        """Re-read what the favorites views copy from their sources (an instrument
        set on a patch, say). Any favorites change or rescan lands here."""
        self.rescan_favorites_category()

    def favorite_sections(self, patches: list[dict]) -> dict[str, str]:
        """Section group for each favorites row, keyed by its stable_key.

        The group is ``favorite_group``'s decision; this method only gathers its
        inputs, all dict lookups.
        """
        out: dict[str, str] = {}
        for patch in patches:
            key = favorite_row_key(patch)
            if patch.get("missing"):
                out[key] = OTHER_GROUP
                continue
            source = self.get_patch_by_stable_key(key) or patch
            out[key] = favorite_group(
                user_instrument=self.metadata_index.user_instrument(key),
                primary=source.get("instrument_primary"),
            )
        return out

    def is_patch_in_favorites(self, patch) -> bool:
        """True when the patch is in the favorites index. A dict lookup, safe to call
        per visible row per frame."""
        if not patch:
            return False
        key = patch.get("stable_key")
        if not key:
            key = self._stable_key_for_path(patch.get("path"))
        return self.favorites_index.is_favorited(key)

    def rescan_favorites_category(self) -> list[dict]:
        """Rebuild the favorites category from the index. No disk, no library scan."""
        with self.scan_lock:
            rows = self._favorite_rows(self.patches_by_stable_key)
            rest = {k: v for k, v in self.patches.items() if k != favorites_display_name()}
            self.patches = {favorites_display_name(): rows, **rest}
            self.folder_tree = self._tree_with_favorite_folders(build_folder_tree(self.patches))
        return rows

    def get_favorites_folder_path(self) -> Path:
        """The folder under the user patch root that the PC workflow saves into.

        Only the scan reads it. Nothing is copied into it, and it is not created."""
        return resolve_user_patches_dir() / FAVORITES_NAME.lstrip("!")

    def _source_exists(self, patch: dict, key: str) -> bool:
        if key in self.patches_by_stable_key:
            return True
        path = patch.get("path")
        return bool(path) and Path(path).exists()

    def add_patch_to_favorites(
        self,
        patch: dict,
        *,
        folder: str = DEFAULT_FAVORITES_FOLDER,
        save_and_rescan: bool = True,
    ) -> bool:
        """Add one index entry for the patch. Nothing is copied."""
        if not patch or patch.get("missing"):
            return False
        stable_key = self.favorite_stable_key_for_patch(patch)
        if not stable_key:
            print(f"Error: cannot resolve stable_key for {patch.get('name')}")
            return False
        if not self._source_exists(patch, stable_key):
            print(f"Error: Source patch not found: {patch.get('path')}")
            return False
        if self.favorites_index.is_favorited(stable_key):
            print(f"Patch already in favorites index: {stable_key}")
            return False
        self.favorites_index.add(stable_key, folder=folder.strip())
        if save_and_rescan:
            self.favorites_index.save()
            self.invalidate_favorite_cache()
        return True

    def remove_patch_from_favorites(self, patch) -> bool:
        """Drop the index entry. The patch file is never touched."""
        stable_key = self.favorite_stable_key_for_patch(patch)
        if not stable_key or not self.favorites_index.is_favorited(stable_key):
            return False
        self.favorites_index.remove(stable_key)
        self.favorites_index.save()
        self.invalidate_favorite_cache()
        return True

    def move_patch_to_favorites_folder(self, patch: dict, folder: str) -> bool:
        """Change which favorites folder the entry is listed in."""
        stable_key = self.favorite_stable_key_for_patch(patch)
        if not stable_key or not self.favorites_index.move(stable_key, folder):
            return False
        self.favorites_index.save()
        self.invalidate_favorite_cache()
        return True

    def add_patches_to_favorites(
        self,
        patches: list[dict],
        *,
        folder: str = DEFAULT_FAVORITES_FOLDER,
    ) -> tuple[int, int]:
        """Bulk add. Returns (added, skipped). Only the index file is backed up first."""
        if patches:
            from patch_browser.favorites_backup import snapshot_index

            snapshot_index(self.favorites_index.path, label="pre-bulk-add")
        added = 0
        skipped = 0
        for patch in patches:
            if self.add_patch_to_favorites(patch, folder=folder, save_and_rescan=False):
                added += 1
            else:
                skipped += 1
        if added:
            self.favorites_index.save()
            self.invalidate_favorite_cache()
        return added, skipped

    def remove_patches_from_favorites_bulk(self, patches: list[dict]) -> int:
        """Remove many index entries; rebuild once at the end."""
        removed = 0
        for patch in patches:
            stable_key = self.favorite_stable_key_for_patch(patch)
            if stable_key and self.favorites_index.remove(stable_key) is not None:
                removed += 1
        if removed:
            self.favorites_index.save()
            self.invalidate_favorite_cache()
        return removed

    def copy_patch_to_favorites(self, patch_path, *, folder: str = DEFAULT_FAVORITES_FOLDER):
        """Favorite the patch at ``patch_path`` (the name predates pointers)."""
        entry = self.get_patch_by_path(patch_path)
        if entry:
            return self.add_patch_to_favorites(entry, folder=folder)
        patch = {"name": Path(patch_path).stem, "path": str(patch_path), "category": ""}
        stable_key = self._stable_key_for_path(patch_path)
        if stable_key:
            patch["stable_key"] = stable_key
        return self.add_patch_to_favorites(patch, folder=folder)
