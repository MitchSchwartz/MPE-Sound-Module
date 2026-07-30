"""Patch scanning and favorites helpers (no GPIO / OLED dependencies)."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

from patch_browser.patch_normalization import log_missing_normalization_summary


@dataclass
class ScannerConfig:
    favorites_name: str = field(
        default_factory=lambda: os.environ.get("MPE_FAVORITES_NAME", "!Quick Access")
    )


SCANNER_CONFIG = ScannerConfig()
FAVORITES_NAME = SCANNER_CONFIG.favorites_name
LAST_PATCH_FILE = Path.home() / ".patch_browser_last_patch.json"

SURGE_PATCH_DIRS = [
    Path.home() / "surge" / "resources" / "data" / "patches_factory",
    Path.home() / "surge" / "resources" / "data" / "patches_3rdparty",
    Path.home() / "Documents" / "Surge XT" / "Patches",
]


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

        self.scan_complete = threading.Event()
        self.scan_lock = threading.Lock()
        self.scan_thread = None

    def scan_patches(self):
        """Scan all patch directories and organize by category."""
        print("Scanning Surge patches...")
        total_patches = 0

        with self.scan_lock:
            self.patches = {}

        for patch_dir in self.patch_dirs:
            if not patch_dir.exists():
                print(f"Warning: Patch directory not found: {patch_dir}")
                continue

            for root, dirs, files in os.walk(patch_dir):
                rel_path = Path(root).relative_to(patch_dir)
                category = str(rel_path.parts[0]) if rel_path.parts else "Uncategorized"

                if favorites_folder_matches(category):
                    category = favorites_display_name(category)

                fxp_files = [f for f in files if f.lower().endswith(".fxp")]

                if fxp_files:
                    if category not in self.patches:
                        self.patches[category] = []

                    for fxp_file in fxp_files:
                        patch_path = Path(root) / fxp_file
                        patch_name = fxp_file.replace(".fxp", "").replace(".FXP", "")

                        existing_patch = next(
                            (p for p in self.patches[category] if p["name"] == patch_name),
                            None,
                        )
                        if existing_patch:
                            if category == favorites_display_name() and favorites_folder_matches(
                                str(patch_path)
                            ):
                                existing_patch["path"] = str(patch_path)
                            continue

                        self.patches[category].append(
                            {
                                "name": patch_name,
                                "path": str(patch_path),
                                "category": category,
                            }
                        )
                        total_patches += 1

        def sort_key(item):
            category_name = item[0]
            if category_name == favorites_display_name():
                return ("", category_name)
            return (category_name, category_name)

        with self.scan_lock:
            self.patches = {
                k: sorted(v, key=lambda x: x["name"])
                for k, v in sorted(self.patches.items(), key=sort_key)
            }

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

        for fxp_file in category_path.glob("*.fxp"):
            patch_name = fxp_file.stem
            category_name = category_path.name
            if favorites_folder_matches(category_name):
                category_name = favorites_display_name(category_name)
            patches.append(
                {
                    "name": patch_name,
                    "path": str(fxp_file),
                    "category": category_name,
                }
            )
        return sorted(patches, key=lambda x: x["name"])

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
            return self.patches.get(category, [])

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
        user_patches_dir = Path.home() / "Documents" / "Surge XT" / "Patches"
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
        for fxp_path in favorites_folder.glob("*.fxp"):
            if fxp_path.stem.lower() == target:
                return fxp_path
        return None
