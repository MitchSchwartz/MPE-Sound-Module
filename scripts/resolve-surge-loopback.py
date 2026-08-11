#!/usr/bin/env python3
"""Print Surge ``--audio-interface`` ID for snd-aloop Loopback playback."""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from patch_browser.calibration_loopback import (  # noqa: E402
    ensure_snd_aloop,
    resolve_surge_loopback_interface,
)


def main() -> int:
    if len(sys.argv) > 1:
        cli = Path(sys.argv[1])
    else:
        cli = Path(os.environ.get("SURGE_CLI", Path.home() / "surge/build/surge_xt_products/surge-xt-cli"))
    if not cli.is_file():
        print(f"Error: Surge CLI not found: {cli}", file=sys.stderr)
        return 1
    ensure_snd_aloop()
    print(resolve_surge_loopback_interface(cli))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
