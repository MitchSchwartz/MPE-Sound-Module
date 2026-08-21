#!/usr/bin/env python3
"""
Merge duplicate rows in ~/.patch_browser_normalization.json.

Duplicates = different JSON keys for the same .fxp bytes (SHA-256), plus
legacy stem keys that resolve to the same file. Keeps the entry with the newest
calibrated_at; canonical key is the winner's key (prefers stable_key over stem).

Usage:
  python3 scripts/dedupe-patch-normalization.py --dry-run
  python3 scripts/dedupe-patch-normalization.py
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from patch_browser.favorites_index import FavoritesIndex  # noqa: E402
from patch_browser.json_store import atomic_write_json, read_json_dict  # noqa: E402
from patch_browser.patch_normalization import default_normalization_path  # noqa: E402
from patch_browser.patch_scanner import (  # noqa: E402
    PatchScanner,
    SURGE_PATCH_DIRS,
    resolve_patch_scan_dirs,
)
from patch_browser.patch_sidecar_key import (  # noqa: E402
    is_stable_key,
    lookup_entry,
    stem_from_name,
    stable_key_from_absolute_path,
)

_GLOBAL = "_global"


def parse_calibrated_at(entry: dict) -> datetime:
    raw = entry.get("calibrated_at")
    if not isinstance(raw, str) or not raw.strip():
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def file_sha256(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return hashlib.sha256(data).hexdigest()


def resolve_key_to_path(
    key: str,
    data: dict,
    scanner: PatchScanner,
    favorites: FavoritesIndex,
) -> Path | None:
    """Best-effort absolute .fxp path for a normalization JSON key."""
    if is_stable_key(key):
        patch = scanner.get_patch_by_stable_key(key)
        if patch and patch.get("path"):
            return Path(str(patch["path"]))
        entry = favorites.get_entry(key)
        if entry and entry.get("dest_path"):
            return Path(str(entry["dest_path"]))
        return None

    stem = key
    entry, matched = lookup_entry(
        data,
        stem,
        patch_dirs=SURGE_PATCH_DIRS,
    )
    if matched and is_stable_key(matched):
        return resolve_key_to_path(matched, data, scanner, favorites)

    matches: list[Path] = []
    for patch in scanner.patches_by_stable_key.values():
        if stem_from_name(str(patch.get("name", ""))) != stem:
            continue
        raw = patch.get("path")
        if raw:
            matches.append(Path(str(raw)))
    if len(matches) == 1:
        return matches[0]
    return None


def pick_winner(keys: list[str], data: dict) -> str:
    def sort_key(k: str) -> tuple:
        entry = data[k]
        ts = parse_calibrated_at(entry)
        stable_rank = 0 if is_stable_key(k) else 1
        return (-ts.timestamp(), stable_rank, k)

    return sorted(keys, key=sort_key)[0]


def dedupe(
    data: dict,
    scanner: PatchScanner,
    favorites: FavoritesIndex,
) -> tuple[dict, list[str]]:
    """Return (new_data, log lines)."""
    reserved = {_GLOBAL}
    patch_keys = [k for k in data if k not in reserved and isinstance(data[k], dict)]

    hash_groups: dict[str, list[str]] = defaultdict(list)
    orphan_keys: list[str] = []

    for key in patch_keys:
        path = resolve_key_to_path(key, data, scanner, favorites)
        if path is None or not path.is_file():
            orphan_keys.append(key)
            continue
        digest = file_sha256(path)
        if digest is None:
            orphan_keys.append(key)
            continue
        hash_groups[digest].append(key)

    new_data: dict = {}
    if _GLOBAL in data:
        new_data[_GLOBAL] = data[_GLOBAL]

    log: list[str] = []
    merged_keys: set[str] = set()

    for digest, keys in sorted(hash_groups.items(), key=lambda kv: kv[0]):
        unique = sorted(set(keys))
        if len(unique) == 1:
            k = unique[0]
            new_data[k] = dict(data[k])
            continue

        winner = pick_winner(unique, data)
        merged = dict(data[winner])
        losers = [k for k in unique if k != winner]
        for loser in losers:
            for field in ("user_trim_db", "enabled"):
                if field in data.get(loser, {}) and field not in merged:
                    merged[field] = data[loser][field]
            merged_keys.add(loser)

        ts = parse_calibrated_at(merged).isoformat()
        log.append(
            f"merge sha256:{digest[:12]}… → keep {winner!r} ({ts}), "
            f"drop {', '.join(repr(k) for k in losers)}"
        )
        new_data[winner] = merged

    for key in orphan_keys:
        if key in merged_keys:
            continue
        if key not in new_data:
            new_data[key] = dict(data[key])
            log.append(f"keep orphan (unresolved path) {key!r}")

    return new_data, log


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--path",
        type=Path,
        default=None,
        help=f"Normalization JSON (default: {default_normalization_path()})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print merges only; do not write",
    )
    args = parser.parse_args()

    path = args.path or default_normalization_path()
    if not path.is_file():
        print(f"Error: not found: {path}", file=sys.stderr)
        return 1

    data = read_json_dict(path, label="normalization")
    before = len([k for k in data if k != _GLOBAL])

    scanner = PatchScanner(resolve_patch_scan_dirs())
    scanner.scan_patches()
    favorites = FavoritesIndex()

    new_data, log = dedupe(data, scanner, favorites)
    after = len([k for k in new_data if k != _GLOBAL])

    print(f"File: {path}")
    print(f"Rows: {before} → {after} ({before - after} removed)")
    if not log:
        print("No duplicate groups found.")
    else:
        print("Actions:")
        for line in log:
            print(f"  {line}")

    if args.dry_run:
        print("Dry run — no changes written.")
        return 0

    if new_data == data:
        print("Nothing to write.")
        return 0

    backup = path.with_suffix(path.suffix + f".bak-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(path, backup)
    atomic_write_json(path, new_data)
    print(f"Backup: {backup}")
    print(f"Wrote: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
