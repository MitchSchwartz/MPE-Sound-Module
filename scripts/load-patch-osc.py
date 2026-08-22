#!/usr/bin/env python3
"""Load a Surge patch via PatchLoader (OSC). Usage: load-patch-osc.py /path/to/Patch.fxp"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from patch_browser.patch_loader import PatchLoader  # noqa: E402


def main() -> None:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} PATCH.fxp", file=sys.stderr)
        sys.exit(2)
    path = Path(sys.argv[1]).resolve()
    if not path.is_file():
        print(f"not found: {path}", file=sys.stderr)
        sys.exit(1)
    loader = PatchLoader()
    if not loader.load_patch(path, apply_normalization=False):
        sys.exit(1)
    print(f"loaded {path.name}", flush=True)


if __name__ == "__main__":
    main()
