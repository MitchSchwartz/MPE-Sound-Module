#!/usr/bin/env python3
"""Build data/patch_metadata_baseline.json from the local Surge patch library."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from patch_browser.patch_metadata import (  # noqa: E402
    build_baseline_document,
    default_baseline_path,
    write_metadata_file,
)
from patch_browser.patch_scanner import SURGE_PATCH_DIRS, PatchScanner  # noqa: E402


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
    args = parser.parse_args()

    scanner = PatchScanner(SURGE_PATCH_DIRS)
    scanner.scan_patches()
    document = build_baseline_document(scanner.patches_by_stable_key)

    patch_count = len(document.get("patches", {}))
    print(f"Classified {patch_count} patches")

    if patch_count:
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
