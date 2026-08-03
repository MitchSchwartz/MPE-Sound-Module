#!/usr/bin/env python3
"""Manual spike: query/set Surge poly limit OSC path on the Pi."""

from __future__ import annotations

import argparse
import sys

from patch_browser.surge_playback import POLY_LIMIT_OSC, query_polylimit, send_polylimit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--in-port", type=int, default=53280)
    parser.add_argument("--out-port", type=int, default=53270)
    parser.add_argument("--set", type=int, default=None, help="Set poly limit to N voices")
    args = parser.parse_args()

    try:
        from pythonosc import udp_client
    except ImportError:
        print("python-osc required", file=sys.stderr)
        return 1

    client = udp_client.SimpleUDPClient(args.host, args.in_port)
    current = query_polylimit(
        client,
        osc_host=args.host,
        osc_out_port=args.out_port,
    )
    print(f"Current polylimit ({POLY_LIMIT_OSC}): {current}")

    if args.set is not None:
        ok = send_polylimit(client, args.set)
        print(f"Set to {args.set}: {'ok' if ok else 'failed'}")
        after = query_polylimit(
            client,
            osc_host=args.host,
            osc_out_port=args.out_port,
        )
        print(f"After query: {after}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
