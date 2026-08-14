"""Apply SooperLooper grid-sync defaults — JACK transport is the clock.

All loops quantize to JACK transport cycle boundaries (sync_source = -1).
Canon: https://sonosaurus.com/sooperlooper/doc_sync.html
"""

from __future__ import annotations

import os
import sys
from typing import Callable

DEFAULT_FADE_SAMPLES = int(os.environ.get("MPE_SL_FADE_SAMPLES", "128"))


def apply_grid_sync(
    send: Callable[[str, list], None],
    *,
    num_loops: int = 16,
    eighth_per_cycle: int = 8,
    fade_samples: int = DEFAULT_FADE_SAMPLES,
) -> None:
    """Configure SL: JACK transport master; every loop quantizes to cycle."""
    send("/set", ["sync_source", -1.0])
    send("/set", ["eighth_per_cycle", float(eighth_per_cycle)])
    send("/set", ["fade_samples", float(fade_samples)])

    for loop in range(num_loops):
        prefix = f"/sl/{loop}/set"
        send(prefix, ["quantize", 1.0])  # cycle
        send(prefix, ["sync", 1.0])
        send(prefix, ["relative_sync", 0.0])
        send(prefix, ["round", 0.0])
        send(prefix, ["playback_sync", 1.0])


def apply_freeform(
    send: Callable[[str, list], None],
    *,
    num_loops: int = 16,
) -> None:
    """Eval free-form mode — no sync/quantize (B2 bench)."""
    send("/set", ["sync_source", 0.0])
    for loop in range(num_loops):
        prefix = f"/sl/{loop}/set"
        send(prefix, ["quantize", 0.0])
        send(prefix, ["sync", 0.0])
        send(prefix, ["relative_sync", 0.0])
        send(prefix, ["round", 0.0])


def main() -> int:
    host = os.environ.get("MPE_SL_OSC_HOST", "127.0.0.1")
    port = int(os.environ.get("MPE_SL_OSC_PORT", "9951"))
    num_loops = int(os.environ.get("MPE_SL_LOOPS", "16"))
    mode = os.environ.get("MPE_SL_SYNC_MODE", "grid").strip().lower()

    try:
        from pythonosc import udp_client
    except ImportError as exc:
        print(f"sl-grid-sync: {exc}", file=sys.stderr)
        return 1

    client = udp_client.SimpleUDPClient(host, port)

    def send(path: str, args: list) -> None:
        client.send_message(path, args)

    if mode in ("free", "freeform", "0", "off"):
        apply_freeform(send, num_loops=num_loops)
        print(f"sl-grid-sync: free-form ({num_loops} loops)", flush=True)
    else:
        apply_grid_sync(send, num_loops=num_loops)
        print(
            f"sl-grid-sync: JACK transport grid — 4/4 ({num_loops} loops quantize to cycle, "
            f"fade_samples={DEFAULT_FADE_SAMPLES})",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
