#!/usr/bin/env python3
"""Manual spike: query and set Surge AEG sustain/decay/release over OSC."""

from __future__ import annotations

import argparse
import re
import socket
import struct
import sys
import time

OSC_IN_PORT = 53280
OSC_OUT_PORT = 53270

PATHS = [
    "/param/a/aeg/sustain",
    "/param/a/aeg/decay",
    "/param/a/aeg/release",
    "/param/b/aeg/sustain",
    "/param/b/aeg/decay",
    "/param/b/aeg/release",
]


def parse_surge_param_query(data: bytes) -> float | None:
    if len(data) < 8 or data[0] != 0x2F:
        return None
    try:
        from pythonosc.osc_message import OscMessage

        for param in OscMessage(data).params:
            if isinstance(param, str):
                match = re.search(r"([\d.]+)\s*%", param)
                if match:
                    return max(0.0, min(1.0, float(match.group(1)) / 100.0))
                try:
                    value = float(param.strip())
                except ValueError:
                    continue
                if value <= 1.0:
                    return max(0.0, min(1.0, value))
                return max(0.0, min(1.0, value / 100.0))
            if isinstance(param, (int, float)):
                value = float(param)
                if 0.0 <= value <= 1.0:
                    return value
    except Exception:
        pass
    idx = data.find(b"\x00,\x00")
    while idx != -1:
        start = idx + 4
        if start + 4 <= len(data):
            value = struct.unpack(">f", data[start : start + 4])[0]
            if 0.0 <= value <= 1.0:
                return value
        idx = data.find(b"\x00,\x00", idx + 1)
    return None


def query_param(client, sock, path: str) -> float | None:
    for query in (f"/q{path}", f"/q{path.rstrip('/')}"):
        client.send_message(query, [])
        try:
            data, _addr = sock.recvfrom(4096)
        except socket.timeout:
            continue
        value = parse_surge_param_query(data)
        if value is not None:
            return value
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--in-port", type=int, default=OSC_IN_PORT)
    parser.add_argument("--out-port", type=int, default=OSC_OUT_PORT)
    parser.add_argument("--set-mult", type=float, default=None, help="Apply multiplier to all six params")
    args = parser.parse_args()

    try:
        from pythonosc import udp_client
    except ImportError:
        print("python-osc required", file=sys.stderr)
        return 1

    client = udp_client.SimpleUDPClient(args.host, args.in_port)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.host, args.out_port))
    sock.settimeout(0.1)

    print(f"Querying AEG params (in={args.in_port}, out={args.out_port})...")
    baselines: dict[str, float] = {}
    for path in PATHS:
        value = query_param(client, sock, path)
        baselines[path] = value if value is not None else float("nan")
        print(f"  {path}: {value}")

    if args.set_mult is not None:
        mult = args.set_mult
        print(f"\nSetting ×{mult}...")
        for path in PATHS:
            base = baselines.get(path)
            if base is None or base != base:  # NaN check
                continue
            effective = max(0.0, min(1.0, base * mult))
            client.send_message(path, effective)
            print(f"  {path} -> {effective:.3f}")

    sock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
