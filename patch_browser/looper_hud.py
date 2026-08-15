"""Looper HUD — continuous bar sweep + beat counter.

Resuscitated from `yolo/looper-phase0` (f069648, "Render looper HUD as one
sub-pixel bar sweep instead of four 4px boxes") and re-pointed at the
SooperLooper HUD state published by `scripts/sooperlooper/sl_hud_monitor.py`.

Why one sweep per bar rather than a fill per beat: the header affords the HUD
only a few dozen pixels. Spending all of them on a single travelling edge gives
the motion `beats_per_bar` times more pixels to move through.
"""

from __future__ import annotations

import time

DEFAULT_BEATS_PER_BAR = 4


def bar_seconds(bpm: float, *, beats_per_bar: int = DEFAULT_BEATS_PER_BAR) -> float | None:
    if not bpm or bpm <= 0.0 or beats_per_bar <= 0:
        return None
    return beats_per_bar * 60.0 / float(bpm)


def interpolated_pos(sl: dict, *, now: float | None = None) -> float | None:
    """Loop position advanced by wall time since the snapshot was written.

    The HUD file is rewritten a couple of times a second; drawing straight from
    it would step the sweep visibly. Advancing by elapsed time between updates
    is what makes the edge move smoothly at 60 fps.
    """
    pos = sl.get("loop_pos")
    updated = sl.get("updated_at")
    if pos is None or not updated:
        return None
    now = time.time() if now is None else now
    return max(0.0, float(pos) + max(0.0, now - float(updated)))


def bar_progress(sl: dict, *, now: float | None = None,
                 beats_per_bar: int = DEFAULT_BEATS_PER_BAR) -> float | None:
    """Continuous 0.0 … 1.0 position within the current bar, or None."""
    span = bar_seconds(sl.get("bpm"), beats_per_bar=beats_per_bar)
    if span is None:
        return None
    pos = interpolated_pos(sl, now=now)
    if pos is None:
        return None
    return (pos % span) / span


def beat_label(sl: dict, *, beats_per_bar: int = DEFAULT_BEATS_PER_BAR) -> str:
    beat = sl.get("beat")
    if beat is None:
        return ""
    return f"{int(beat)}/{beats_per_bar}"


def should_show(sl: dict, *, user_enabled: bool = True) -> bool:
    """Show whenever a grid exists — the counter is useful before playback too."""
    if not user_enabled or not sl:
        return False
    return bool(sl.get("bpm"))


def is_running(sl: dict) -> bool:
    return bool(sl.get("active") or sl.get("playing"))
