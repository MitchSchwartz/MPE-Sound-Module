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

from sl_limits import MAX_USABLE_LOOPS, resolve_num_loops

import os
import sys
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sl_grid_state import GridState

DEFAULT_FADE_SAMPLES = int(os.environ.get("MPE_SL_FADE_SAMPLES", "256"))
DEFAULT_BPM = float(os.environ.get("MPE_LOOPER_BPM", "120"))
DEFAULT_CLOCK = os.environ.get("MPE_SL_GRID_CLOCK", "internal").strip().lower()

# Ring-out capture: the pad press that closes a take sends `overdub` instead of
# `record`, so SooperLooper closes the loop and starts overdubbing at the same
# sample and the release of the cut-off notes lands in the loop head. The
# overdub ends itself one pass later.
#
# This replaced an offline pipeline (parallel scratch capture + merge + buffer
# reload) that could not work: measured 65-139 ms of OSC arm latency after the
# wrap, and +4.05 dB of summed level across the loop head. See SR&ED evidence
# §3 U11. Kill switch, falls back to a plain stop: MPE_SL_RING_OUT=0.
RING_OUT_ENABLED = os.environ.get("MPE_SL_RING_OUT", "1").strip().lower() not in (
    "0",
    "off",
    "false",
    "",
)
# Start seam overdub when playhead enters the last fraction of the loop (wrap weld).
TAIL_SEAM_RATIO = float(os.environ.get("MPE_SL_TAIL_SEAM_RATIO", "0.85"))
# After release falls quiet, wait for playhead near the wrap before overdub off (reduces pop).
TAIL_SEAM_END_MAX_S = float(os.environ.get("MPE_SL_TAIL_SEAM_END_MS", "500")) / 1000.0
# Minimum time in overdub so a very short peak still reaches the seam on brief loops.
TAIL_MIN_OVERDUB_S = float(os.environ.get("MPE_SL_TAIL_MIN_OVERDUB_MS", "150")) / 1000.0
# Tail weld mix — lower incoming gain + longer loop crossfade while overdub is active.
TAIL_WELD_INPUT_GAIN = float(os.environ.get("MPE_SL_TAIL_INPUT_GAIN", "0.35"))
TAIL_WELD_FADE_SAMPLES = int(os.environ.get("MPE_SL_TAIL_FADE_SAMPLES", "512"))
TAIL_WELD_RESTORE_INPUT_GAIN = float(os.environ.get("MPE_SL_TAIL_RESTORE_INPUT_GAIN", "1.0"))

# Phase re-anchor: when PLAYING arrives mid-bar, defer a second set_tempo until
# loop_pos is near the defining take's wrap (set_tempo zeroes phase). Grid
# establishment itself is immediate — this only realigns phase for clip 2+.
GRID_ANCHOR_MAX_S = float(os.environ.get("MPE_SL_GRID_ANCHOR_MAX_S", "0.015"))
GRID_ANCHOR_WRAP_HIGH_RATIO = float(os.environ.get("MPE_SL_GRID_ANCHOR_WRAP_HIGH", "0.85"))
GRID_ANCHOR_FALLBACK_CYCLES = float(os.environ.get("MPE_SL_GRID_ANCHOR_FALLBACK_CYCLES", "1.1"))


def should_defer_phase_anchor(
    loop_pos: float,
    loop_len: float,
    *,
    loop_pos_seen: bool,
    anchor_max_s: float = GRID_ANCHOR_MAX_S,
) -> bool:
    """True while we should wait for a better phase-alignment moment."""
    if not loop_pos_seen:
        return True
    if loop_len <= 0.0:
        return True
    return loop_pos > anchor_max_s


def detect_loop_wrap(
    prev_pos: float,
    loop_pos: float,
    loop_len: float,
    *,
    anchor_max_s: float = GRID_ANCHOR_MAX_S,
    wrap_high_ratio: float = GRID_ANCHOR_WRAP_HIGH_RATIO,
) -> bool:
    """True when playback position jumped from near end back toward start."""
    if loop_len <= 0.0:
        return False
    return prev_pos >= loop_len * wrap_high_ratio and loop_pos <= anchor_max_s * 3.0

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

# Quantize unit is always one 4/4 bar (8 eighth notes). Fixed deliberately —
# see DECISIONS.md 2026-08-15 correction. Not sized to multi-bar first takes.
EIGHTH_PER_CYCLE = int(os.environ.get("MPE_LOOPER_EIGHTH_PER_CYCLE", "8"))


def set_grid_active(
    send: Callable[[str, list], None], *, num_loops: int = MAX_USABLE_LOOPS, active: bool
) -> None:
    """The two grid states, applied to the engine.

    active=False — NO GRID YET. Every loop is genuinely free-form: the take
        that will define the grid must not be quantized, rounded or synced to
        anything, because there is nothing to sync to. Leaving quantize/round
        on here made SL stretch a short first take up to the end of a cycle
        derived from the PREVIOUS session's tempo — an imaginary bar.

    active=True — GRID ESTABLISHED. Clips count in to the next bar (sync) and
        their length snaps to one cycle (quantize=QUANT_CYCLE). round stays off:
        the stop is already quantized, and rounding on top extends the take by
        another cycle.
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



def apply_loop_latency(
    send: Callable[[str, list], None], *, num_loops: int = MAX_USABLE_LOOPS
) -> None:
    """Align record/stop boundaries with JACK pipeline delay (Tier 1).

    Prefer SooperLooper autoset from JACK port latency. Override with
    MPE_SL_INPUT_LATENCY (samples) when ear calibration needs a fixed value.
    """
    override = os.environ.get("MPE_SL_INPUT_LATENCY", "").strip()
    autoset_disabled = os.environ.get("MPE_SL_AUTOSET_LATENCY", "1").strip().lower() in (
        "0",
        "off",
        "false",
        "",
    )
    if override:
        val = float(override)
        for loop in range(num_loops):
            prefix = f"/sl/{loop}/set"
            send(prefix, ["autoset_latency", 0.0])
            send(prefix, ["input_latency", val])
            send(prefix, ["trigger_latency", 0.0])
    elif not autoset_disabled:
        for loop in range(num_loops):
            prefix = f"/sl/{loop}/set"
            send(prefix, ["autoset_latency", 1.0])
            send(prefix, ["trigger_latency", 0.0])


def apply_grid_sync(
    send: Callable[[str, list], None],
    *,
    num_loops: int = MAX_USABLE_LOOPS,
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
    apply_loop_latency(send, num_loops=num_loops)
    # No grid until a take defines one, so start free-form.
    set_grid_active(send, num_loops=num_loops, active=False)


def set_count_in(
    send: Callable[[str, list], None], *, num_loops: int = MAX_USABLE_LOOPS, count_in: bool
) -> None:
    """Deprecated alias — the grid has two states, not a count-in toggle."""
    set_grid_active(send, num_loops=num_loops, active=count_in)


def establish_grid_clock(
    send: Callable[[str, list], None], bpm: float, *, bars: int = 1
) -> None:
    """Lock the engine's cycle to the FIRST TAKE, disable smart_eighths, reset phase.

    `bars` is how many 4/4 bars the take was read as. It must be passed, because
    SL computes cycle = eighth_per_cycle * 30 / bpm: leave eighth_per_cycle at 8
    while the derived tempo rises and the engine's cycle shrinks to a fraction
    of the take. A 6.939 s first loop read as 4 bars at 138 BPM would give the
    engine a 1.735 s cycle, and clips would join four times inside the loop the
    player thinks of as one bar.

    A grid needs tempo, unit, and phase. Sending tempo alone was not enough:
    with `smart_eighths` left at SooperLooper's default (ON), any tempo under
    60 BPM silently doubles `_eighth_cycle` — sync boundaries arrive every
    **two** bars while the HUD still counts one-bar bars. Tap near the end of
    bar 0 then waits through all of bar 1 and arms at bar 2.

    Order: smart_eighths off, eighth_per_cycle, then tempo (phase reset via
    Engine::set_tempo — verified in engine.cpp).
    """
    send("/set", ["smart_eighths", 0.0])
    send("/set", ["eighth_per_cycle", float(EIGHTH_PER_CYCLE * max(1, bars))])
    send("/set", ["tempo", float(bpm)])


def apply_established_grid(
    send: Callable[[str, list], None],
    grid: GridState,
    *,
    num_loops: int = MAX_USABLE_LOOPS,
    now: float,
    arm_loops: bool,
) -> None:
    """**The one way the engine is ever told what the grid is.**

    `GridState` owns tempo, unit and phase. This is its only seam to the
    engine, and `establish_grid_clock` has no other caller — enforced by
    `tests/test_clock_tail_ownership.py`, which fails naming the file and line
    if a second one appears.

    It exists because the same few lines were hand-assembled at **six** sites,
    of which exactly one was right:

    * `bench.on_grid_established` — clock, `mark_phase_zero`, arm. Correct.
    * `bench.on_phase_reanchor` — sent a freshly re-derived bpm with the STORED
      bar count: a tempo from one reading and a unit from another, giving the
      engine a cycle belonging to neither. Latent; it bites when `loop_len`
      moves between establishment and the wrap.
    * `bench.on_looper_engine_started` — clock and arm, **and no
      `mark_phase_zero`.** `Engine::set_tempo` zeroes `_quarter_counter` /
      `_tempo_counter` (engine.cpp:2174-2178), so after an engine restart the
      engine's downbeat moved and the bench's did not. `next_boundary` then
      hands `SlotRuntime` a bar line the engine does not agree with, and a
      quantized launch lands off the beat with the surface vouching for it.
    * `looper_songs.load_song` — clock at the default `bars=1`, so any song
      whose first take read as 2, 4 or 8 bars came back with the engine's cycle
      at a fraction of the take.
    * `track_gesture.stop_all_loops` — a raw `/set tempo` with the phase mark
      beside it, and no `smart_eighths` / `eighth_per_cycle`. Correct only
      while those happen to still hold from establishment.
    * `looper_songs.stop_playback` — a raw `/set tempo` of the value it had
      just read back, unexplained. It is a phase reset, and now says so.

    Six near-copies, four distinct defects. Pairing the phase mark with the
    tempo send in ONE function is what stops them recurring: you cannot reset
    the engine's phase through this module without saying where the bench's
    downbeat now is.

    `arm_loops` is deliberately required. True re-sends the per-loop quantize /
    sync / mute_quantized settings (grid establishment, engine restart, song
    load); False is phase-only (the re-anchor at the defining take's wrap),
    where those loops are already armed and re-sending ~90 messages into live
    playback buys nothing.
    """
    if not grid.established or not grid.bpm or grid.bpm <= 0.0:
        raise ValueError(
            "apply_established_grid called with no established grid — "
            "there is no tempo to send, and sending one anyway would zero the "
            "engine's phase against a bar line nobody has agreed on"
        )
    establish_grid_clock(send, grid.bpm, bars=grid.bars or 1)
    # Immediately after, and unconditionally: the tempo send above WAS the
    # phase reset, so this is not bookkeeping, it is the other half of it.
    grid.mark_phase_zero(now)
    if arm_loops:
        set_grid_active(send, num_loops=num_loops, active=True)


def apply_freeform(
    send: Callable[[str, list], None],
    *,
    num_loops: int = MAX_USABLE_LOOPS,
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
    num_loops = resolve_num_loops()
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
        apply_loop_latency(send, num_loops=num_loops)
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
