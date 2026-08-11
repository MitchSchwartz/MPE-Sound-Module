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
    beat_in_bar: int,
    beats_per_bar: int,
    beat_phase: float,
    ticks_per_beat: int = 2,
) -> list[int]:
    """Discrete fill per beat segment: 0 empty, 1 half, 2 full (1/8-bar ticks in 4/4)."""
    beats = max(1, int(beats_per_bar))
    beat = max(1, min(beats, int(beat_in_bar)))
    phase = max(0.0, min(1.0, float(beat_phase)))
    ticks = max(1, int(ticks_per_beat))
    tick_in_beat = min(ticks, int(phase * ticks))
    filled = (beat - 1) * ticks + tick_in_beat
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
