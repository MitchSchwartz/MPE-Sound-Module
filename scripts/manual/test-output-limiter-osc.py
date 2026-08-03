#!/usr/bin/env python3
"""Manual spike: enable/disable Surge global Conditioner limiter via OSC."""

from __future__ import annotations

import argparse
import socket
import struct
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from patch_browser.surge_output_limiter import (  # noqa: E402
    FX_BYPASS_OSC,
    apply_output_limiter,
    disable_output_limiter,
    limiter_fx_slot,
    limiter_threshold_db,
)


def _query_float(host: str, in_port: int, out_port: int, path: str, timeout_s: float = 0.15):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout_s)
    try:
        sock.bind(("0.0.0.0", 0))
        query = f"/q{path}".encode("utf-8")
        pad = b"\x00" * ((4 - (len(query) % 4)) % 4)
        sock.sendto(query + pad + struct.pack(">i", 0), (host, in_port))
        data, _ = sock.recvfrom(4096)
        if len(data) < 8:
            return None
        tag = data[-4:]
        if tag == b",f\x00\x00":
            return struct.unpack(">f", data[-8:-4])[0]
    except OSError:
        return None
    finally:
        sock.close()
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--in-port", type=int, default=53280)
    parser.add_argument("--out-port", type=int, default=53270)
    parser.add_argument("--off", action="store_true", help="Bypass limiter slot")
    parser.add_argument("--threshold-db", type=float, default=None)
    parser.add_argument("--query", action="store_true", help="Query fx_bypass and limiter gain after apply")
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
    if ok and args.query:
        time.sleep(0.1)
        bypass = _query_float(args.host, args.in_port, args.out_port, FX_BYPASS_OSC)
        gain = _query_float(args.host, args.in_port, args.out_port, f"/param/fx/global/{slot}/param8")
        deact = _query_float(args.host, args.in_port, args.out_port, f"/param/fx/global/{slot}/deactivate")
        print(f"  fx_bypass={bypass} (0=All FX), param8(gain)={gain}, deactivate={deact}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
