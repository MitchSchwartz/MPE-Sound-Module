#!/usr/bin/env python3
"""Build data/patch_metadata_baseline.json from the local Surge patch library."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from patch_browser.patch_metadata import (  # noqa: E402
    build_baseline_document,
    default_baseline_path,
    write_metadata_file,
)
from patch_browser.patch_scanner import (  # noqa: E402
    PatchScanner,
    resolve_library_patch_dirs,
    resolve_patch_scan_dirs,
    resolve_personal_repo,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=default_baseline_path(),
        help="Baseline JSON path (default: data/patch_metadata_baseline.json)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print counts only; do not write file",
    )
    parser.add_argument(
        "--library-only",
        action="store_true",
        help="Require MPE-Library sibling repo; do not fall back to ~/surge paths",
    )
    args = parser.parse_args()

    personal = resolve_personal_repo(REPO_ROOT)
    if personal:
        print(f"MPE-Library: {personal}")

    patch_dirs = (
        resolve_library_patch_dirs(REPO_ROOT)
        if args.library_only
        else resolve_patch_scan_dirs(REPO_ROOT)
    )
    if not patch_dirs:
        print("No patch directories found.", file=sys.stderr)
        return 1

    print("Scan roots:")
    for path in patch_dirs:
        print(f"  {path}")

    scanner = PatchScanner(patch_dirs)
    scanner.scan_patches()
    document = build_baseline_document(scanner.patches_by_stable_key)

    patch_count = len(document.get("patches", {}))
    print(f"Classified {patch_count} patches")

    if patch_count:
        primary_counts: Counter[str] = Counter()
        for row in document["patches"].values():
            instruments = row.get("instruments") or ["other"]
            primary_counts[instruments[0]] += 1
        print("Primary instrument counts:")
        for instrument, count in primary_counts.most_common():
            print(f"  {instrument}: {count}")

        sample_keys = list(document["patches"])[:5]
        for key in sample_keys:
            row = document["patches"][key]
            print(f"  {key}: {row.get('instruments')}")

    if args.dry_run:
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_metadata_file(args.output, document)
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
