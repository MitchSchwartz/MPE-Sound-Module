"""Grid establishment — the first take defines the tempo, then the grid is its own.

The standard live-looping workflow (Boss RC-20, DigiTech JamMan, Ableton Looper,
Loopy Pro): the first loop records free-form with **no count-in** — there is no
grid to count against yet — and its length sets the tempo. Every clip after it
counts in to the next bar and is length-quantized.

Two roles that are easy to conflate, and conflating them cost us two days:

  * first clip as the **clock**  — wrong. If the grid keeps depending on that
    clip, deleting it breaks everything, and the clip is not an ordinary clip.
  * first clip as the **tempo definer** — right, and standard. The tempo is
    *captured* once, not borrowed. After that the grid stands on its own and
    the defining clip is an ordinary clip you can delete like any other.

So the grid has exactly two states, and one transition: the first take landing.
"""

from __future__ import annotations

import os

BEATS_PER_BAR = int(os.environ.get("MPE_LOOPER_BEATS_PER_BAR", "4"))
BPM_MIN = float(os.environ.get("MPE_LOOPER_BPM_MIN", "20"))
BPM_MAX = float(os.environ.get("MPE_LOOPER_BPM_MAX", "300"))
MAX_BARS = int(os.environ.get("MPE_LOOPER_MAX_BARS", "8"))


def derive_tempo(
    loop_len: float,
    *,
    beats_per_bar: int = BEATS_PER_BAR,
    bpm_min: float = BPM_MIN,
    bpm_max: float = BPM_MAX,
    max_bars: int = MAX_BARS,
) -> tuple[float, int] | None:
    """(bpm, bars) for a first take of `loop_len` seconds.

    **The first take is one bar.** It is the base denomination, by definition
    rather than by inference — no tempo to set, nothing to guess, and the
    cycle already equals the take at the default 8 eighths per cycle.

    The returned BPM is EXACT, not rounded. Rounding the engine tempo would
    make the grid bar differ from the recorded audio: a 39.8672 -> 40 BPM
    round shortens the bar by 20 ms, so the defining take walks away from
    every later clip by half a second inside twenty loops. Round it for
    DISPLAY (see display_bpm); never for the engine.

    The only reason to use more than one bar is an absurd take: a 30 s first
    loop read as one bar implies ~8 BPM. Then fall back to the smallest bar
    count that lands in a representable range.
    """
    if loop_len <= 0.0 or beats_per_bar <= 0:
        return None

    one_bar_bpm = beats_per_bar * 60.0 / loop_len
    if bpm_min <= one_bar_bpm <= bpm_max:
        return one_bar_bpm, 1

    for bars in range(2, max_bars + 1):
        bpm = (bars * beats_per_bar) * 60.0 / loop_len
        if bpm_min <= bpm <= bpm_max:
            return bpm, bars

    # Still out of range (very short or very long): keep the one-bar reading
    # rather than refusing. Looping stays correct; only the label is odd.
    return one_bar_bpm, 1


def display_bpm(bpm: float) -> int:
    """What the HUD shows. Rounding belongs here and nowhere else."""
    return int(round(bpm))


class GridState:
    """Is a grid established, and which clip defined it?

    `defined_by` is recorded for logging only. Nothing reads it to decide
    behaviour — that is the whole point. Once the tempo is captured the
    defining clip has no special status and can be deleted like any other.
    """

    def __init__(self) -> None:
        self.established = False
        self.bpm: float | None = None
        self.bars: int | None = None
        self.defined_by: int | None = None
        self._pending: int | None = None

    def arm(self, loop: int) -> bool:
        """Mark `loop` as the take that will define the grid. True if accepted."""
        if self.established or self._pending is not None:
            return False
        self._pending = loop
        return True

    def is_pending(self, loop: int) -> bool:
        return self._pending == loop

    def cancel(self, loop: int) -> None:
        if self._pending == loop:
            self._pending = None

    def establish(self, loop: int, loop_len: float) -> tuple[float, int] | None:
        """Capture tempo from the defining take. Returns (bpm, bars) or None."""
        if self.established or self._pending != loop:
            return None
        derived = derive_tempo(loop_len)
        if derived is None:
            return None
        self.bpm, self.bars = derived
        self.established = True
        self.defined_by = loop
        self._pending = None
        return derived

    def reset(self) -> None:
        """Track reset — back to no grid, so the next take defines it again."""
        self.established = False
        self.bpm = None
        self.bars = None
        self.defined_by = None
        self._pending = None
