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


def phrase_seconds(sl: dict, *, beats_per_bar: int = DEFAULT_BEATS_PER_BAR) -> float | None:
    """Length of the display cycle: the longest clip, else one bar.

    A 1-bar clip and a 4-bar clip do not share a display cycle. The sweep spans
    the PHRASE — the longest loop — so the bar always fills completely and the
    counter tells you where you are in the music rather than in an arbitrary bar.
    """
    phrase = float(sl.get("phrase_len") or 0.0)
    if phrase > 0.0:
        return phrase
    return bar_seconds(sl.get("bpm"), beats_per_bar=beats_per_bar)


def bars_in_phrase(sl: dict) -> int:
    return max(1, int(sl.get("bars_in_phrase") or 1))


def bar_progress(sl: dict, *, now: float | None = None,
                 beats_per_bar: int = DEFAULT_BEATS_PER_BAR) -> float | None:
    """Continuous 0.0 … 1.0 position within the phrase, or None."""
    span = phrase_seconds(sl, beats_per_bar=beats_per_bar)
    if not span:
        return None
    pos = interpolated_pos(sl, now=now)
    if pos is None:
        return None
    return (pos % span) / span


def beat_label(sl: dict, *, beats_per_bar: int = DEFAULT_BEATS_PER_BAR) -> str:
    """Bar within the phrase — '2/4'. A single-bar phrase reads '1/1'."""
    bar = sl.get("bar")
    if bar is None:
        return ""
    return f"{int(bar)}/{bars_in_phrase(sl)}"


def current_beat_index(sl: dict, *, now: float | None = None,
                       beats_per_bar: int = DEFAULT_BEATS_PER_BAR) -> int | None:
    """Which beat segment of the phrase is live, for the discrete highlight.

    A smooth sweep alone does not tell you the exact moment a beat lands —
    that is a real cost of continuous motion, not an unavoidable one. Lighting
    the current segment changes discretely, so the eye gets both.
    """
    span = phrase_seconds(sl, beats_per_bar=beats_per_bar)
    pos = interpolated_pos(sl, now=now)
    if not span or pos is None:
        return None
    segments = max(1, bars_in_phrase(sl) * beats_per_bar)
    return min(segments - 1, int(((pos % span) / span) * segments))


def segment_count(sl: dict, *, beats_per_bar: int = DEFAULT_BEATS_PER_BAR) -> int:
    return max(1, bars_in_phrase(sl) * beats_per_bar)


def should_show(sl: dict, *, user_enabled: bool = True) -> bool:
    """Show whenever a grid exists — the counter is useful before playback too."""
    if not user_enabled or not sl:
        return False
    return bool(sl.get("bpm"))


def is_running(sl: dict) -> bool:
    return bool(sl.get("active") or sl.get("playing"))
