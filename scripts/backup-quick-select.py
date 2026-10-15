#!/usr/bin/env python3
"""Snapshot the favorites index (the whole favorites state: pointers hold no patch files)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from patch_browser.favorites_backup import snapshot_index  # noqa: E402
from patch_browser.favorites_index import FavoritesIndex, default_favorites_index_path  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", type=Path, default=None, help="Output directory (default: beside the index)")
    parser.add_argument("--index", type=Path, default=None, help="Favorites index path (default ~/.patch_browser_favorites.json)")
    parser.add_argument("--label", default="", help="Optional suffix on snapshot folder name")
    args = parser.parse_args()

    index_path = args.index or default_favorites_index_path()
    out = snapshot_index(index_path, dest_dir=args.dest, label=args.label)
    print(f"Snapshot: {out}")
    print(f"Index: {index_path} ({len(FavoritesIndex(index_path).entries)} favorites)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
