#!/usr/bin/env python3
"""Manual spike: enable/disable Surge global Conditioner limiter via OSC."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from patch_browser.surge_output_limiter import (  # noqa: E402
    apply_output_limiter,
    disable_output_limiter,
    limiter_fx_slot,
    limiter_threshold_db,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--in-port", type=int, default=53280)
    parser.add_argument("--off", action="store_true", help="Bypass limiter slot")
    parser.add_argument("--threshold-db", type=float, default=None)
    args = parser.parse_args()

    try:
        from pythonosc import udp_client
    except ImportError:
        print("python-osc required", file=sys.stderr)
        return 1

    client = udp_client.SimpleUDPClient(args.host, args.in_port)
    slot = limiter_fx_slot()
    threshold = limiter_threshold_db() if args.threshold_db is None else args.threshold_db
    if args.off:
        ok = disable_output_limiter(client)
        print(f"Disable global/{slot}: {'ok' if ok else 'failed'}")
    else:
        ok = apply_output_limiter(client, threshold_db=threshold)
        print(f"Apply Conditioner on global/{slot} @ {threshold} dB: {'ok' if ok else 'failed'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
