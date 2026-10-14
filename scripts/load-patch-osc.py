#!/usr/bin/env python3
"""Load a Surge patch the way the touch UI does. Usage: load-patch-osc.py PATCH.fxp

Measurement harnesses load patches through here (scripts/lib/measure-run-as-user.sh
mpe_load_patch_osc). Surge's level goes through the UI's volume authority before
the load: device mode keeps DEVICE_MODE_SURGE_TRIM_DB, trim mode uses the saved
fader. A fresh PatchLoader would otherwise send unity, 6 dB above the UI in
device mode (2026-10-12 capacity run). The DAC is read, never written.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from patch_browser.measure_patch import load_patch_like_ui  # noqa: E402


def main() -> None:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} PATCH.fxp", file=sys.stderr)
        sys.exit(2)
    path = Path(sys.argv[1]).resolve()
    if not path.is_file():
        print(f"not found: {path}", file=sys.stderr)
        sys.exit(1)
    from patch_browser.patch_loader import PatchLoader

    if not load_patch_like_ui(PatchLoader(), path):
        sys.exit(1)
    print(f"loaded {path.name}", flush=True)


if __name__ == "__main__":
    main()
