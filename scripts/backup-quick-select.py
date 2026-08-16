#!/usr/bin/env python3
"""Snapshot Quick Select + favorites index (local Pi or laptop with Surge paths)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from patch_browser.favorites_backup import snapshot_favorites  # noqa: E402
from patch_browser.favorites_index import FavoritesIndex, default_favorites_index_path  # noqa: E402
from patch_browser.patch_scanner import PatchScanner, resolve_patch_scan_dirs  # noqa: E402


def scan_dirs(repo_root: Path) -> list[Path]:
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
        "--dest",
        type=Path,
        default=None,
        help="Output directory (default ~/.patch_browser_favorites_backups/TIMESTAMP)",
    )
    parser.add_argument(
        "--index",
        type=Path,
        default=None,
        help="Favorites index path (default ~/.patch_browser_favorites.json)",
    )
    parser.add_argument("--label", default="", help="Optional suffix on snapshot folder name")
    args = parser.parse_args()

    scanner = PatchScanner(scan_dirs(REPO_ROOT))
    scanner.scan_patches()
    qa_root = scanner.get_favorites_folder_path()
    index_path = args.index or default_favorites_index_path()

    out = snapshot_favorites(qa_root, index_path, dest_dir=args.dest, label=args.label)
    count = sum(1 for _ in qa_root.rglob("*.fxp"))
    print(f"Snapshot: {out}")
    print(f"Quick Select: {qa_root} ({count} .fxp)")
    print(f"Index: {index_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
