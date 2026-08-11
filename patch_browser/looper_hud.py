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


def looper_hud_width_px(*, bars_per_loop: int = 4, beats_per_bar: int = 4, show_bpm: bool = True) -> int:
    """Estimate HUD pill width from segment counts."""
    from patch_browser.touch_ui_constants import LOOPER_HUD_PAD_X

    beat_w = beats_per_bar * 7 + max(0, beats_per_bar - 1) * 2
    bar_w = bars_per_loop * 8 + max(0, bars_per_loop - 1) * 1
    text_w = 28 if bars_per_loop <= 4 else 34
    bpm_w = 26 if show_bpm else 0
    return LOOPER_HUD_PAD_X * 2 + beat_w + 6 + bar_w + 6 + text_w + bpm_w
