"""Boss-style looper HUD — eighth-note ticks (half box) + bar counter."""

from __future__ import annotations

from patch_browser.looper_timing_state import read_timing_state

TICKS_PER_BAR_DEFAULT = 8


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


def looper_hud_bar_fraction(snapshot: dict) -> str:
    internal = looper_hud_internal(snapshot)
    if internal.get("active"):
        bar = internal.get("bar_in_loop")
        total = internal.get("bars_per_loop")
        if bar is not None and total is not None:
            return f"{bar}/{total}"
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


def looper_hud_segment_halves(
    *,
    tick_in_bar: int,
    beats_per_bar: int = 4,
    ticks_per_beat: int = 2,
) -> list[int]:
    """Half-fill levels per beat segment: 0 empty, 1 half, 2 full (1/8 bar per tick)."""
    beats = max(1, int(beats_per_bar))
    ticks = max(1, int(ticks_per_beat))
    filled = max(0, min(beats * ticks, int(tick_in_bar)))
    out: list[int] = []
    for i in range(beats):
        seg_start = i * ticks
        out.append(max(0, min(ticks, filled - seg_start)))
    return out


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
