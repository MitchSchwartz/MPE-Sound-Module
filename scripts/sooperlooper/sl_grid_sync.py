"""Apply SooperLooper grid-sync defaults.

Grid mode needs a **clock**. Two sources, selected by MPE_SL_GRID_CLOCK:

  internal   (default) — SL generates its own boundaries from `tempo`
                         (sync_source = -3). No extra process. Works standalone.
  transport            — JACK transport BBT (sync_source = -1). Requires a
                         rolling timebase master; spec Task 0 / D.1 only.

Canon: https://sonosaurus.com/sooperlooper/doc_sync.html

**Never apply a sync_source whose clock is not running.** SL parks in WaitStart
forever waiting for a boundary that never arrives, the pad stops responding, and
nothing in the UI says why. That failure mode cost an evening on 2026-08-14 —
see Documents/specs/looper-transport-clock-spec.md §J.
"""

from __future__ import annotations

import os
import sys
from typing import Callable

DEFAULT_FADE_SAMPLES = int(os.environ.get("MPE_SL_FADE_SAMPLES", "128"))
DEFAULT_BPM = float(os.environ.get("MPE_LOOPER_BPM", "120"))
DEFAULT_CLOCK = os.environ.get("MPE_SL_GRID_CLOCK", "internal").strip().lower()

SYNC_SOURCE_INTERNAL = -3.0
SYNC_SOURCE_JACK = -1.0
SYNC_SOURCE_NONE = 0.0


def _quantize_all(send: Callable[[str, list], None], num_loops: int) -> None:
    for loop in range(num_loops):
        prefix = f"/sl/{loop}/set"
        send(prefix, ["quantize", 1.0])  # cycle
        send(prefix, ["sync", 1.0])
        send(prefix, ["relative_sync", 0.0])
        send(prefix, ["round", 0.0])
        send(prefix, ["playback_sync", 1.0])


def apply_grid_sync(
    send: Callable[[str, list], None],
    *,
    num_loops: int = 16,
    eighth_per_cycle: int = 8,
    fade_samples: int = DEFAULT_FADE_SAMPLES,
    clock: str = DEFAULT_CLOCK,
    bpm: float = DEFAULT_BPM,
) -> None:
    """Configure SL grid. Every loop quantizes to cycle; no loop is the clock."""
    if clock == "transport":
        send("/set", ["sync_source", SYNC_SOURCE_JACK])
    else:
        # Internal: SL owns the pulse, so boundaries exist with no other process.
        send("/set", ["sync_source", SYNC_SOURCE_INTERNAL])
        send("/set", ["tempo", float(bpm)])

    send("/set", ["eighth_per_cycle", float(eighth_per_cycle)])
    send("/set", ["fade_samples", float(fade_samples)])
    _quantize_all(send, num_loops)


def anchor_phase(send: Callable[[str, list], None]) -> None:
    """Declare 'the downbeat is now' for internal sync (spec §D.0, unverified)."""
    send("/set", ["tap_tempo", 1.0])


def apply_freeform(
    send: Callable[[str, list], None],
    *,
    num_loops: int = 16,
) -> None:
    """Eval free-form mode — no sync/quantize (B2 bench)."""
    send("/set", ["sync_source", SYNC_SOURCE_NONE])
    for loop in range(num_loops):
        prefix = f"/sl/{loop}/set"
        send(prefix, ["quantize", 0.0])
        send(prefix, ["sync", 0.0])
        send(prefix, ["relative_sync", 0.0])
        send(prefix, ["round", 0.0])
        send(prefix, ["playback_sync", 0.0])


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
        clock = "JACK transport" if DEFAULT_CLOCK == "transport" else f"internal {DEFAULT_BPM:.1f} BPM"
        print(
            f"sl-grid-sync: grid on {clock} — 4/4, {num_loops} loops quantize to "
            f"cycle, fade_samples={DEFAULT_FADE_SAMPLES}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
