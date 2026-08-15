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

# Count-in: does hitting record wait for the next cycle boundary before it
# starts capturing? Default OFF.
#
# With sync=1 the engine arms and starts on the next boundary, so everything
# played between the tap and the boundary is LOST — you record a different
# window than you play, and a short take comes back as a sliver. `round` still
# rounds the END of the take up to a whole cycle, so the loop stays in time.
COUNT_IN = os.environ.get("MPE_SL_COUNT_IN", "0").strip().lower() not in ("0", "off", "false", "")

SYNC_SOURCE_INTERNAL = -3.0
SYNC_SOURCE_JACK = -1.0
SYNC_SOURCE_NONE = 0.0


def set_grid_active(
    send: Callable[[str, list], None], *, num_loops: int = 16, active: bool
) -> None:
    """The two grid states, applied to the engine.

    active=False — NO GRID YET. Every loop is genuinely free-form: the take
        that will define the grid must not be quantized, rounded or synced to
        anything, because there is nothing to sync to. Leaving quantize/round
        on here made SL stretch a short first take up to the end of a cycle
        derived from the PREVIOUS session's tempo — an imaginary bar.

    active=True — GRID ESTABLISHED. Clips count in to the boundary (sync) and
        their length snaps to whole cycles (quantize=QUANT_CYCLE, and the cycle
        is the defining take). round stays off: the stop is already quantized,
        and rounding on top of it extends the take by another cycle.
    """
    for loop in range(num_loops):
        prefix = f"/sl/{loop}/set"
        send(prefix, ["quantize", 1.0 if active else 0.0])  # 1 = QUANT_CYCLE
        send(prefix, ["sync", 1.0 if active else 0.0])
        send(prefix, ["round", 0.0])
        send(prefix, ["relative_sync", 0.0])
        # SL's own default (looper.cpp ports[PlaybackSync] = 0.0f). Forcing 1
        # made a fresh clip wait for the NEXT boundary after record-stop had
        # already landed on one.
        send(prefix, ["playback_sync", 0.0])
        # Quantize launch/stop to the cycle. This is what makes a clip start on
        # the bar: `trigger` cannot be used for launch because it does not lift
        # a pause (verified — a paused loop stays Paused through trigger), so
        # clips are stopped by MUTING and launched by unmuting, and SL defers
        # the unmute to the boundary.
        send(prefix, ["mute_quantized", 1.0 if active else 0.0])


# The control we watch to notice the engine went away and came back.
#
# `sync_source` is global, cheap to subscribe to, and — crucially — its value
# under our config (-3, internal) is not one SooperLooper ever chooses on its
# own. A fresh engine reports something else, so a mismatch is an unambiguous
# "this is not the engine we configured".
#
# Watching a per-loop control would not do: in the no-grid state we set
# quantize to 0 deliberately, which is also the engine default, so a restart in
# that state would be invisible.
RESTART_SENTINEL = "sync_source"


def expected_sentinel(clock: str = DEFAULT_CLOCK) -> float:
    """What `sync_source` must read back as while our config is in force."""
    return float(SYNC_SOURCE_JACK if clock == "transport" else SYNC_SOURCE_INTERNAL)


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
    # SooperLooper rewrites our cycle length behind us when the tempo leaves
    # 60..240 BPM (engine.cpp set_tempo: "_eighth_cycle *= 2" under 60, and it
    # pushes the new value to every loop). "First take = one bar" means a 6 s
    # take is 40 BPM and an 8 s take is 30, so we hit this EVERY time and the
    # cycle silently doubled — clips waited a bar and a half.
    send("/set", ["smart_eighths", 0.0])

    if clock == "transport":
        send("/set", ["sync_source", SYNC_SOURCE_JACK])
    else:
        # Internal: SL owns the pulse, so boundaries exist with no other process.
        send("/set", ["sync_source", SYNC_SOURCE_INTERNAL])
        send("/set", ["tempo", float(bpm)])

    send("/set", ["eighth_per_cycle", float(eighth_per_cycle)])
    send("/set", ["fade_samples", float(fade_samples)])
    # No grid until a take defines one, so start free-form.
    set_grid_active(send, num_loops=num_loops, active=False)


def set_count_in(
    send: Callable[[str, list], None], *, num_loops: int = 16, count_in: bool
) -> None:
    """Deprecated alias — the grid has two states, not a count-in toggle."""
    set_grid_active(send, num_loops=num_loops, active=count_in)


def anchor_phase(send: Callable[[str, list], None], bpm: float) -> None:
    """Declare 'the downbeat is NOW'.

    A grid needs three things: tempo, a unit, and a PHASE. We had the first two
    and never set the third, so SL's free-running internal clock put beat one
    at an arbitrary offset from the take — clips joined out of phase and
    record-stop landed on the wrong boundary.

    Engine::set_tempo zeroes _tempo_counter and _quarter_counter, so re-sending
    the tempo is the phase reset. Verified in engine.cpp, not inferred.
    """
    send("/set", ["tempo", float(bpm)])


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
