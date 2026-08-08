#!/usr/bin/env python3
"""Migrate flat Quick Access root copies into Liked/ + favorites v2 JSON index."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from patch_browser.favorites_index import (  # noqa: E402
    LEGACY_LIKED_FOLDER,
    FavoritesIndex,
)
from patch_browser.patch_scanner import (  # noqa: E402
    PatchScanner,
    favorites_display_name,
    favorites_folder_matches,
    resolve_patch_scan_dirs,
)


def scan_dirs_for_migration(repo_root: Path) -> list[Path]:
    """Patch roots for migration — honor MPE_SURGE_DOCS test/layout overrides."""
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


def build_stem_to_stable_keys(scanner: PatchScanner) -> dict[str, list[str]]:
    from patch_browser.patch_sidecar_key import build_stem_to_stable_keys as _build

    patches: list[dict] = []
    with scanner.scan_lock:
        for category, group in scanner.patches.items():
            if favorites_folder_matches(category):
                continue
            patches.extend(group)
    return _build(patches)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print migration plan without moving files or writing JSON",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply migration (requires zero plan errors)",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=None,
        help="Optional backup directory for QA tree + index JSON before apply",
    )
    parser.add_argument(
        "--index",
        type=Path,
        default=None,
        help="Favorites index path (default ~/.patch_browser_favorites.json)",
    )
    args = parser.parse_args()

    if args.apply and args.dry_run:
        print("Use either --dry-run or --apply, not both", file=sys.stderr)
        return 2
    if not args.apply and not args.dry_run:
        args.dry_run = True

    patch_dirs = scan_dirs_for_migration(REPO_ROOT)
    scanner = PatchScanner(patch_dirs)
    scanner.scan_patches()
    qa_root = scanner.get_favorites_folder_path()
    index = FavoritesIndex(args.index)

    stem_map = build_stem_to_stable_keys(scanner)
    plan = index.plan_flat_root_migration(qa_root, stem_map)

    print(f"Quick Access: {qa_root}")
    print(f"Display category: {favorites_display_name()}")
    print(f"Target folder: {LEGACY_LIKED_FOLDER}/")
    print(f"Moves planned: {plan.move_count}")

    for item in plan.items:
        print(f"  {item.source_path.name} → {item.dest_path} ({item.stable_key})")

    for skip in plan.skipped:
        print(f"  skip: {skip}")

    for err in plan.errors:
        print(f"  ERROR: {err}", file=sys.stderr)

    if not plan.ok:
        print("Migration aborted — resolve errors first.", file=sys.stderr)
        return 1

    if plan.move_count == 0:
        print("Nothing to migrate.")
        return 0

    if args.dry_run:
        print("Dry-run complete — re-run with --apply to execute.")
        return 0

    backup_dir = args.backup_dir
    if backup_dir is None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_dir = qa_root.parent / f".favorites-migration-backup-{stamp}"
        print(f"Backup directory (default): {backup_dir}")

    index.apply_migration_plan(plan, qa_root=qa_root, backup_dir=backup_dir)
    scanner.rescan_favorites_category()
    print(f"Applied {plan.move_count} move(s); index saved to {index.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
