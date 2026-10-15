#!/usr/bin/env python3
"""Restore the favorites index from a snapshot made by backup-quick-select.py."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from patch_browser.favorites_backup import restore_index  # noqa: E402
from patch_browser.favorites_index import FavoritesIndex, default_favorites_index_path  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot_dir", type=Path, help="Directory holding patch_browser_favorites.json")
    parser.add_argument("--index", type=Path, default=None, help="Live index path (default ~/.patch_browser_favorites.json)")
    parser.add_argument("--no-backup", action="store_true", help="Skip the pre-restore snapshot of the live index")
    parser.add_argument(
        "--allow-older-version",
        action="store_true",
        help="Restore a version-1 snapshot over a version-2 index (favorites are pointers there; "
        "a v1 snapshot does not carry them back, and its entries mean copies)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print plan only")
    args = parser.parse_args()

    snapshot_dir = args.snapshot_dir.expanduser().resolve()
    if not snapshot_dir.is_dir():
        print(f"Snapshot not found: {snapshot_dir}", file=sys.stderr)
        return 1
    index_path = args.index or default_favorites_index_path()
    source = snapshot_dir / index_path.name
    if not source.is_file():
        print(f"No {index_path.name} in {snapshot_dir}", file=sys.stderr)
        return 1
    snapshot, live = FavoritesIndex(source), FavoritesIndex(index_path)
    if snapshot.legacy and not live.legacy and not args.allow_older_version:
        print(
            f"Refusing: {source} is a version-1 index and {index_path} is version 2 (pointers). "
            "Restoring it would bring back entries that stand for copies the migration removed. "
            "To go back to copies, undo the migration run instead; to restore anyway, pass "
            "--allow-older-version.",
            file=sys.stderr,
        )
        return 1
    print(f"Restore {source} ({len(FavoritesIndex(source).entries)} favorites) over {index_path}")
    if args.dry_run:
        print("Dry run - re-run without --dry-run to apply.")
        return 0
    pre = restore_index(index_path, snapshot_dir, backup_before=not args.no_backup)
    if pre is not None:
        print(f"Pre-restore backup: {pre}")
    print("Restart touch-patch-browser if it is running.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
