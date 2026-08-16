#!/usr/bin/env python3
"""Restore Quick Select from a snapshot; rebuild index when JSON is missing or stale."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Reload scanner favorites name after Pi appliance env is available.
for _env_file in (Path("/etc/mpe/mpe.env"), Path.home() / ".config/mpe/mpe.env"):
    if _env_file.is_file():
        for line in _env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key == "MPE_FAVORITES_NAME" and value:
                os.environ["MPE_FAVORITES_NAME"] = value
                break

from patch_browser.favorites_backup import (  # noqa: E402
    rebuild_index_from_qa_tree,
    restore_favorites_tree,
)
from patch_browser.favorites_index import FavoritesIndex, default_favorites_index_path  # noqa: E402
from patch_browser.patch_scanner import (  # noqa: E402
    PatchScanner,
    favorites_folder_matches,
    resolve_patch_scan_dirs,
)
from patch_browser.patch_sidecar_key import build_stem_to_stable_keys  # noqa: E402


def scan_dirs_for_restore(repo_root: Path) -> list[Path]:
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


def library_stem_map(scanner: PatchScanner) -> dict[str, list[str]]:
    patches: list[dict] = []
    with scanner.scan_lock:
        for category, group in scanner.patches.items():
            if favorites_folder_matches(category):
                continue
            patches.extend(group)
    return build_stem_to_stable_keys(patches)


def resolve_source_qa(snapshot_dir: Path, qa_folder_name: str) -> Path:
    direct = snapshot_dir / qa_folder_name
    if direct.is_dir():
        return direct
    nested = snapshot_dir / "Patches" / qa_folder_name
    if nested.is_dir():
        return nested
    if any(snapshot_dir.glob("*.fxp")):
        return snapshot_dir
    raise FileNotFoundError(
        f"No Quick Select folder under {snapshot_dir} (expected {qa_folder_name}/ or *.fxp)"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "snapshot_dir",
        type=Path,
        help="Directory containing Quick Select/ and optionally patch_browser_favorites.json",
    )
    parser.add_argument(
        "--index",
        type=Path,
        default=None,
        help="Live favorites index path (default ~/.patch_browser_favorites.json)",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip pre-restore snapshot of current Quick Select",
    )
    parser.add_argument(
        "--rebuild-index",
        action="store_true",
        help="Rebuild index from restored tree even when snapshot JSON exists",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print plan only",
    )
    args = parser.parse_args()

    snapshot_dir = args.snapshot_dir.expanduser().resolve()
    if not snapshot_dir.is_dir():
        print(f"Snapshot not found: {snapshot_dir}", file=sys.stderr)
        return 1

    patch_dirs = scan_dirs_for_restore(REPO_ROOT)
    # Pick up MPE_FAVORITES_NAME from env files loaded above.
    import patch_browser.patch_scanner as patch_scanner_mod

    patch_scanner_mod.SCANNER_CONFIG.favorites_name = os.environ.get(
        "MPE_FAVORITES_NAME", patch_scanner_mod.FAVORITES_NAME
    )
    patch_scanner_mod.FAVORITES_NAME = patch_scanner_mod.SCANNER_CONFIG.favorites_name

    scanner = PatchScanner(patch_dirs)
    scanner.scan_patches()
    qa_root = scanner.get_favorites_folder_path()
    qa_name = qa_root.name
    index_path = args.index or default_favorites_index_path()
    source_qa = resolve_source_qa(snapshot_dir, qa_name)
    source_index = snapshot_dir / index_path.name
    if not source_index.is_file():
        source_index = None

    live_count = sum(1 for _ in qa_root.rglob("*.fxp")) if qa_root.is_dir() else 0
    restore_count = sum(1 for _ in source_qa.rglob("*.fxp"))
    print(f"Live Quick Select: {qa_root} ({live_count} .fxp)")
    print(f"Restore from: {source_qa} ({restore_count} .fxp)")
    print(f"Index: {index_path}")
    if source_index:
        print(f"Snapshot index: {source_index}")
    else:
        print("Snapshot index: (missing — will rebuild from tree)")

    if args.dry_run:
        print("Dry run — re-run without --dry-run to apply.")
        return 0

    pre = restore_favorites_tree(
        qa_root,
        source_qa,
        index_path=index_path,
        source_index=source_index if not args.rebuild_index else None,
        backup_before=not args.no_backup,
    )
    if pre is not None:
        print(f"Pre-restore backup: {pre}")

    index = FavoritesIndex(index_path)
    if source_index is None or args.rebuild_index:
        stem_map = library_stem_map(scanner)
        count, errors = rebuild_index_from_qa_tree(
            index,
            qa_root,
            stem_map,
            patch_dirs=patch_dirs,
        )
        print(f"Rebuilt index: {count} entries")
        for err in errors:
            print(f"  WARN: {err}", file=sys.stderr)
    else:
        print(f"Restored index from snapshot ({len(index.entries)} entries)")

    scanner.favorites_index = index
    try:
        scanner.rescan_favorites_category()
    except ValueError as exc:
        print(f"WARN: rescan skipped ({exc}) — restart touch-patch-browser to reload.", file=sys.stderr)
    else:
        print("Rescan complete — restart touch-patch-browser if it is running.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
