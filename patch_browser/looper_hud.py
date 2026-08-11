"""Merged looper HUD state for touch header (pedal clock + on-device timing file)."""

from __future__ import annotations

from patch_browser.looper_timing_state import read_timing_state


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


def looper_hud_segment_fill_halves(
    *,
    total_frames: int,
    frames_per_beat: int,
    beats_per_bar: int,
    ticks_per_beat: int = 2,
) -> list[int]:
    """Discrete fill per beat segment from sample clock (0 empty, 1 half, 2 full).

    One tick = 1/8 bar in 4/4 (half box). Uses rounded frame position in bar so
    each segment completes before the next beat downbeat — not on it.
    """
    beats = max(1, int(beats_per_bar))
    fpb = max(1, int(frames_per_beat))
    ticks = max(1, int(ticks_per_beat))
    frames_per_bar = fpb * beats
    ticks_per_bar = beats * ticks
    pos_in_bar = int(total_frames) % frames_per_bar
    filled_ticks = min(
        ticks_per_bar,
        (pos_in_bar * ticks_per_bar + frames_per_bar // 2) // frames_per_bar,
    )
    out: list[int] = []
    for i in range(beats):
        seg_start = i * ticks
        out.append(max(0, min(ticks, filled_ticks - seg_start)))
    return out


def looper_hud_bar_tick_index(
    *,
    total_frames: int,
    frames_per_beat: int,
    beats_per_bar: int,
    ticks_per_beat: int = 2,
) -> int:
    """0-based eighth-note tick index within the current bar (for publish dedupe)."""
    beats = max(1, int(beats_per_bar))
    fpb = max(1, int(frames_per_beat))
    ticks = max(1, int(ticks_per_beat))
    frames_per_bar = fpb * beats
    ticks_per_bar = beats * ticks
    pos_in_bar = int(total_frames) % frames_per_bar
    return min(
        ticks_per_bar - 1,
        (pos_in_bar * ticks_per_bar + frames_per_bar // 2) // frames_per_bar,
    )


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
