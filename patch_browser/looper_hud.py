"""Boss-style looper HUD — continuous bar sweep + bar counter."""

from __future__ import annotations

import time

from patch_browser.looper_timing_state import read_timing_state

TICKS_PER_BAR_DEFAULT = 8

# Publishes are rate-capped (see looper_timing_publisher); the draw path extrapolates
# across the gap. Cap the extrapolation so a stalled writer freezes the HUD instead of
# letting it run away.
MAX_EXTRAPOLATION_S = 0.100


def merge_looper_hud_snapshot(pedal_snapshot: dict) -> dict:
    """Attach ``internal_timing`` and unified ``looper_active`` to a pedal snapshot."""
    merged = dict(pedal_snapshot)
    internal = read_timing_state()
    merged["internal_timing"] = internal
    if internal.get("active"):
        merged["looper_active"] = True
        merged["running"] = True
        if internal.get("bpm") is not None:
            merged["bpm"] = internal.get("bpm")
    else:
        merged["looper_active"] = bool(
            merged.get("connected")
            and (
                (merged.get("running") and merged.get("bpm") is not None)
                or merged.get("bpm") is not None
            )
        )
    return merged


def looper_hud_is_visible(snapshot: dict, *, user_enabled: bool = True) -> bool:
    if not user_enabled:
        return False
    if snapshot.get("looper_active"):
        return True
    internal = snapshot.get("internal_timing") or {}
    if internal.get("active"):
        return True
    return bool(snapshot.get("connected") and snapshot.get("bpm") is not None)


def looper_hud_internal(snapshot: dict) -> dict:
    return snapshot.get("internal_timing") or read_timing_state()


def looper_hud_interpolated_frames(internal: dict, *, now: float | None = None) -> int:
    """Transport frames estimated forward from the last publish.

    ``updated_at`` is a ``time.monotonic()`` stamp written by the looper process. On
    Linux that is CLOCK_MONOTONIC, which shares an origin across processes — the same
    premise the staleness check in ``read_timing_state`` already rests on.
    """
    total = internal.get("total_frames")
    if total is None:
        return 0
    total = int(total)
    if not internal.get("active"):
        return total

    sample_rate = internal.get("sample_rate")
    updated_at = internal.get("updated_at")
    if not sample_rate or updated_at is None:
        return total

    now = time.monotonic() if now is None else now
    elapsed = float(now) - float(updated_at)
    if elapsed <= 0.0:
        return total
    return total + int(min(elapsed, MAX_EXTRAPOLATION_S) * float(sample_rate))


def looper_hud_bar_in_loop(
    *,
    total_frames: int,
    frames_per_beat: int,
    beats_per_bar: int,
    bars_per_loop: int,
) -> int:
    """1-based bar within the loop for a given transport position."""
    fpbar = max(1, int(frames_per_beat)) * max(1, int(beats_per_bar))
    bars = max(1, int(bars_per_loop))
    return (max(0, int(total_frames)) // fpbar) % bars + 1


def looper_hud_bar_fraction(snapshot: dict, *, now: float | None = None) -> str:
    internal = looper_hud_internal(snapshot)
    if internal.get("active"):
        total_bars = internal.get("bars_per_loop")
        fpb = internal.get("frames_per_beat")
        if total_bars is not None and fpb:
            bar = looper_hud_bar_in_loop(
                total_frames=looper_hud_interpolated_frames(internal, now=now),
                frames_per_beat=int(fpb),
                beats_per_bar=int(internal.get("beats_per_bar") or 4),
                bars_per_loop=int(total_bars),
            )
            return f"{bar}/{total_bars}"
        bar = internal.get("bar_in_loop")
        if bar is not None and total_bars is not None:
            return f"{bar}/{total_bars}"
    bpm = snapshot.get("bpm")
    if bpm is not None:
        return str(int(bpm))
    return ""


def looper_hud_beat_segment_count(snapshot: dict) -> int:
    internal = looper_hud_internal(snapshot)
    if internal.get("active"):
        return max(1, int(internal.get("beats_per_bar") or 4))
    return 4


def looper_hud_tick_in_bar(
    *,
    total_frames: int,
    frames_per_beat: int,
    beats_per_bar: int,
    ticks_per_bar: int = TICKS_PER_BAR_DEFAULT,
) -> int:
    """0-based eighth-note index within the current bar (0 .. ticks_per_bar-1)."""
    fpb = max(1, int(frames_per_beat))
    beats = max(1, int(beats_per_bar))
    fpbar = fpb * beats
    ticks = max(1, int(ticks_per_bar))
    pos = int(total_frames) % fpbar
    return min(ticks - 1, (pos * ticks) // fpbar)


def looper_hud_eighth_index(
    *,
    total_frames: int,
    frames_per_beat: int,
    beats_per_bar: int,
    ticks_per_bar: int = TICKS_PER_BAR_DEFAULT,
) -> int:
    """Monotonic eighth-note counter (publish dedupe — survives loop wrap)."""
    fpb = max(1, int(frames_per_beat))
    beats = max(1, int(beats_per_bar))
    fpbar = fpb * beats
    ticks = max(1, int(ticks_per_bar))
    return int(total_frames) * ticks // fpbar


def looper_hud_tick_from_internal(internal: dict, *, now: float | None = None) -> int:
    """Frame-accurate eighth tick for HUD draw (interpolated frames beat stale ticks)."""
    total = internal.get("total_frames")
    fpb = internal.get("frames_per_beat")
    beats = max(1, int(internal.get("beats_per_bar") or 4))
    if total is not None and fpb:
        return looper_hud_tick_in_bar(
            total_frames=looper_hud_interpolated_frames(internal, now=now),
            frames_per_beat=int(fpb),
            beats_per_bar=beats,
        )
    return max(0, int(internal.get("tick_in_bar") or 0))


def looper_hud_bar_progress(
    *,
    total_frames: int,
    frames_per_beat: int,
    beats_per_bar: int,
) -> float:
    """Continuous 0.0 … 1.0 position within the current bar.

    One sweep per bar rather than a fill per beat: the header leaves the HUD
    only a few dozen pixels, and spending all of them on a single travelling
    edge gives the motion `beats_per_bar` times more pixels to move through.
    Wraps to 0.0 on each bar line.
    """
    fpb = max(1, int(frames_per_beat))
    beats = max(1, int(beats_per_bar))
    fpbar = fpb * beats
    return (max(0, int(total_frames)) % fpbar) / fpbar


def looper_hud_min_width_px(*, frac_label: str = "8/8") -> int:
    """Minimum HUD gap width (bar counter + one beat segment)."""
    from patch_browser.touch_ui_constants import (
        LOOPER_HUD_BEAT_GAP,
        LOOPER_HUD_COUNTER_GAP,
        LOOPER_HUD_MIN_W,
        LOOPER_HUD_PAD_X,
    )

    frac_w = len(frac_label) * 13
    return max(
        LOOPER_HUD_MIN_W,
        LOOPER_HUD_PAD_X * 2 + frac_w + LOOPER_HUD_COUNTER_GAP + 4 + LOOPER_HUD_BEAT_GAP,
    )
