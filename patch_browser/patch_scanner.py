"""Patch scanning and favorites helpers (no GPIO / OLED dependencies)."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

from patch_browser.patch_normalization import log_missing_normalization_summary
from patch_browser.patch_identity import (
    build_folder_tree,
    category_and_inner_segments,
    make_patch_entry,
    patch_root_label,
    stable_key_for_relative_path,
)
from patch_browser.patch_metadata import PatchMetadataIndex


@dataclass
class ScannerConfig:
    favorites_name: str = field(
        default_factory=lambda: os.environ.get("MPE_FAVORITES_NAME", "!Quick Access")
    )


SCANNER_CONFIG = ScannerConfig()
FAVORITES_NAME = SCANNER_CONFIG.favorites_name
LAST_PATCH_FILE = Path.home() / ".patch_browser_last_patch.json"


def resolve_user_patches_dir() -> Path:
    """User patch library — env override, then Documents (PC), then Linux Surge default."""
    surge_docs = os.environ.get("MPE_SURGE_DOCS", "").strip()
    if surge_docs:
        candidate = Path(surge_docs) / "Patches"
        if candidate.is_dir():
            return candidate

    documents = Path.home() / "Documents" / "Surge XT" / "Patches"
    if documents.is_dir():
        return documents

    linux_default = Path.home() / ".Surge Synth Team" / "Surge XT" / "Patches"
    return linux_default


SURGE_PATCH_DIRS = [
    Path.home() / "surge" / "resources" / "data" / "patches_factory",
    Path.home() / "surge" / "resources" / "data" / "patches_3rdparty",
    resolve_user_patches_dir(),
]


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

    skip = {"patches", "user-data", "binaries"}
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
    """True if on-disk folder/category name matches MPE_FAVORITES_NAME (with or without !)."""
    return name.lstrip("!").lower() == FAVORITES_NAME.lstrip("!").lower()


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

        for patch_dir in self.patch_dirs:
            if not patch_dir.exists():
                print(f"Warning: Patch directory not found: {patch_dir}")
                continue

            root_label = patch_root_label(patch_dir)

            for root, _dirs, files in os.walk(patch_dir):
                folder_rel = Path(root).relative_to(patch_dir)
                category, _inner = category_and_inner_segments(folder_rel)
                if favorites_folder_matches(category):
                    category = favorites_display_name(category)

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
                    total_patches += 1

        def sort_key(item):
            category_name = item[0]
            if category_name == favorites_display_name():
                return ("", category_name)
            return (category_name, category_name)

        sorted_patches = {
            k: sorted(v, key=lambda x: (x["name"].casefold(), x["path"]))
            for k, v in sorted(grouped.items(), key=sort_key)
        }
        folder_tree = build_folder_tree(sorted_patches)

        with self.scan_lock:
            self.patches = sorted_patches
            self.patches_by_stable_key = patches_by_key
            self.patches_by_path = patches_by_path
            self.folder_tree = folder_tree

        self.metadata_index.reload()
        self.metadata_index.enrich_all(patches_by_key)

        print(f"Found {total_patches} patches in {len(self.patches)} categories")
        cat_names = list(self.patches.keys())
        print(f"First 3 categories: {cat_names[:3]}")
        return self.patches

    def scan_patches_background(self):
        """Start background thread to scan patches."""

        def _scan_worker():
            try:
                self.scan_patches()
                patch_names = []
                with self.scan_lock:
                    for patches in self.patches.values():
                        patch_names.extend(p["name"] for p in patches)
                log_missing_normalization_summary(patch_names)
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
        if favorites_folder_matches(category_name):
            category_name = favorites_display_name(category_name)

        for fxp_file in sorted(category_path.rglob("*.fxp")):
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
        return sorted(node.get("children", {}).keys(), key=str.casefold)

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

    def is_in_favorites_folder(self, patch_path):
        patch_path_obj = Path(patch_path)
        for patch_dir in self.patch_dirs:
            favorites_folder = patch_dir / FAVORITES_NAME.lstrip("!")
            if favorites_folder.exists() and patch_path_obj.is_relative_to(favorites_folder):
                return True
        return False

    def is_patch_in_favorites(self, patch):
        """True if patch dict is stored in the Quick Select favorites folder."""
        if not patch:
            return False
        patch_path = patch.get("path")
        if patch_path and self.is_in_favorites_folder(patch_path):
            return True
        name = patch.get("name")
        if name and self.get_favorites_patch_path(name) is not None:
            return True
        return False

    def remove_patch_from_favorites(self, patch):
        """Remove patch copy from favorites; returns False if nothing to remove."""
        if not patch:
            return False
        fav_path = None
        patch_path = patch.get("path")
        if patch_path and self.is_in_favorites_folder(patch_path):
            fav_path = Path(patch_path)
        else:
            name = patch.get("name")
            if name:
                fav_path = self.get_favorites_patch_path(name)
        if not fav_path or not fav_path.exists():
            return False
        try:
            fav_path.unlink()
            self.scan_patches()
            return True
        except OSError as exc:
            print(f"Error removing patch from favorites folder: {exc}")
            return False

    def get_favorites_folder_path(self):
        user_patches_dir = resolve_user_patches_dir()
        favorites_folder = user_patches_dir / FAVORITES_NAME.lstrip("!")
        favorites_folder.mkdir(parents=True, exist_ok=True)
        return favorites_folder

    def copy_patch_to_favorites(self, patch_path):
        try:
            source_path = Path(patch_path)
            if not source_path.exists():
                print(f"Error: Source patch not found: {patch_path}")
                return False

            favorites_folder = self.get_favorites_folder_path()
            dest_path = favorites_folder / source_path.name

            if dest_path.exists():
                print(f"Patch already exists in favorites folder: {source_path.name}")
                return False

            import shutil

            shutil.copy2(source_path, dest_path)
            print(f"Copied patch to favorites folder: {source_path.name}")
            self.scan_patches()
            return True
        except Exception as e:
            print(f"Error copying patch to favorites folder: {e}")
            return False

    def get_favorites_patch_path(self, patch_name):
        """Return the .fxp path in the favorites folder for a patch name, if present."""
        favorites_folder = self.get_favorites_folder_path()
        if not favorites_folder.exists():
            return None
        target = patch_name.lower()
        for fxp_path in favorites_folder.rglob("*.fxp"):
            if fxp_path.stem.lower() == target:
                return fxp_path
        return None
