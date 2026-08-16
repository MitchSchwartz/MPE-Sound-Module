#!/usr/bin/env python3
"""Move legacy Quick Access/Liked/ into Quick Select root + fix favorites index."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from patch_browser.favorites_index import FavoritesIndex, LEGACY_LIKED_FOLDER  # noqa: E402
from patch_browser.patch_scanner import (  # noqa: E402
    PatchScanner,
    favorites_display_name,
    resolve_patch_scan_dirs,
)


def scan_dirs_for_migration(repo_root: Path) -> list[Path]:
    surge_docs = __import__("os").environ.get("MPE_SURGE_DOCS", "").strip()
    if surge_docs:
        base = Path(surge_docs)
        dirs: list[Path] = []
        for rel in ("patches_factory", "patches_3rdparty"):
            candidate = base / rel
            if candidate.is_dir():
                dirs.append(candidate)
        patches = base / "Patches"
        if patches.is_dir():
            dirs.append(patches)
        if dirs:
            return dirs
    return resolve_patch_scan_dirs(repo_root)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--index",
        type=Path,
        default=None,
        help="Favorites index path (default ~/.patch_browser_favorites.json)",
    )
    args = parser.parse_args()

    patch_dirs = scan_dirs_for_migration(REPO_ROOT)
    scanner = PatchScanner(patch_dirs)
    scanner.scan_patches()
    qa_root = scanner.get_favorites_folder_path()
    index = FavoritesIndex(args.index)
    scanner.favorites_index = index

    liked = qa_root / LEGACY_LIKED_FOLDER
    print(f"Quick Access: {qa_root}")
    print(f"Display category: {favorites_display_name()}")
    print(f"Legacy folder: {liked} ({'present' if liked.is_dir() else 'absent'})")

    moved = index.migrate_legacy_liked_to_root(qa_root)
    if moved:
        scanner.rescan_favorites_category()
        print(f"Migrated {moved} patch file(s); index saved to {index.path}")
    else:
        print("Nothing to migrate (no Liked/ copies or index rows).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
