"""Apply SooperLooper grid-sync defaults (loop 0 = master length, others quantize to cycle).

Canon: https://sonosaurus.com/sooperlooper/doc_sync.html
Forum example (sync to loop 1, quantize cycle): viewtopic.php?t=126
"""

from __future__ import annotations

import os
import sys
from typing import Callable


def apply_grid_sync(
    send: Callable[[str, list], None],
    *,
    num_loops: int = 16,
    master_loop: int = 0,
    eighth_per_cycle: int = 8,
    enable_relative_sync: bool = False,
) -> None:
    """Configure SL: first loop free-form; others quantize to master cycle boundaries."""
    if master_loop < 0 or master_loop >= num_loops:
        raise ValueError(f"master_loop {master_loop} out of range 0..{num_loops - 1}")

    # sync_source is 1-indexed loop number (loop 0 → 1).
    send("/set", ["sync_source", float(master_loop + 1)])
    send("/set", ["eighth_per_cycle", float(eighth_per_cycle)])

    for loop in range(num_loops):
        prefix = f"/sl/{loop}/set"
        if loop == master_loop:
            send(prefix, ["quantize", 0.0])
            send(prefix, ["sync", 0.0])
            send(prefix, ["relative_sync", 0.0])
            send(prefix, ["round", 0.0])
        else:
            send(prefix, ["quantize", 1.0])  # cycle
            send(prefix, ["sync", 1.0])
            # relative_sync rounds length immediately (EDP SyncRecord) — not bar-wait.
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
            f"sl-grid-sync: grid — loop 0 master, cycles 4/4 (8 eighths), "
            f"loops 1..{num_loops - 1} quantize to cycle",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
