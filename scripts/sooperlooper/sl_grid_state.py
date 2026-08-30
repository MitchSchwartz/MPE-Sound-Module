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

import math
import os

BEATS_PER_BAR = int(os.environ.get("MPE_LOOPER_BEATS_PER_BAR", "4"))
BPM_MIN = float(os.environ.get("MPE_LOOPER_BPM_MIN", "20"))
BPM_MAX = float(os.environ.get("MPE_LOOPER_BPM_MAX", "300"))
MAX_BARS = int(os.environ.get("MPE_LOOPER_MAX_BARS", "8"))


#: Bar counts a first take may be read as. Powers of two because that is what
#: music does: nobody plays a three-bar phrase and then wonders why.
BAR_CANDIDATES = (1, 2, 4, 8)

#: The tempo we assume you meant, absent any other information. Every candidate
#: bar count is scored by how far its implied BPM lands from here, in log space
#: so that half-speed and double-speed are judged evenly.
#:
#: 100 rather than 120 keeps the derived bar close to the length actually
#: played: a 6 s take reads as 2 bars at 80 rather than 4 bars at 160.
BPM_TARGET = float(os.environ.get("MPE_LOOPER_BPM_TARGET", "100"))


def derive_tempo(
    loop_len: float,
    *,
    beats_per_bar: int = BEATS_PER_BAR,
    bpm_min: float = BPM_MIN,
    bpm_max: float = BPM_MAX,
    max_bars: int = MAX_BARS,
    target: float = BPM_TARGET,
) -> tuple[float, int] | None:
    """(bpm, bars) for a first take of `loop_len` seconds.

    THE TAKE IS THE BASE UNIT. Whatever this returns, `bars * beats_per_bar`
    beats occupy exactly `loop_len` seconds — the grid always reconstructs the
    audio you played. What is being chosen here is only how that span is
    DIVIDED, and therefore what "one bar" means for quantizing later clips.

    It used to call the take one bar unconditionally, falling back to more bars
    only outside 20-300 BPM — a band so wide it effectively never fired. So a
    6 s loop was 40 BPM and a 6.9 s take became 34.6 BPM. Mitch, 2026-08-30:

        "If we assume that the first bar is four beats, then that's probably
        wrong, because realistically a 6 s loop will mean a 40 BPM song, which
        is unlikely to be true... it might just be about setting up the math so
        that it always falls into a good range."

    Two things were wrong with those tempos beyond the label. The quantize unit
    is one bar, so a 6 s "one bar" meant clips could only ever join every 6 s.
    And every one of those derived tempos sits below 60 BPM, where SooperLooper
    doubles `eighth_cycle` on its own (engine.cpp) — we were driving the engine
    into its own odd corner on almost every session.

    So: score each candidate bar count by log-distance from `target` and take
    the closest. No bands, no special cases.

        2.0 s -> 120 BPM, 1 bar        6.0 s ->  80 BPM, 2 bars
        4.0 s -> 120 BPM, 2 bars       8.0 s -> 120 BPM, 4 bars

    The returned BPM is EXACT, never rounded. Rounding makes the grid bar
    differ from the recorded audio: 39.8672 -> 40 shortens the bar by 20 ms and
    the defining take walks away from every later clip by half a second inside
    twenty loops. Round for DISPLAY only (`display_bpm`).
    """
    if loop_len <= 0.0 or beats_per_bar <= 0:
        return None

    best: tuple[float, float, int] | None = None
    for bars in BAR_CANDIDATES:
        if bars > max_bars:
            continue
        bpm = (bars * beats_per_bar) * 60.0 / loop_len
        if not (bpm_min <= bpm <= bpm_max):
            continue
        score = abs(math.log(bpm / target))
        if best is None or score < best[0]:
            best = (score, bpm, bars)

    if best is None:
        # Nothing representable — a take of a fraction of a second, or minutes
        # long. Keep the one-bar reading rather than refusing: looping stays
        # correct and only the label is odd.
        return beats_per_bar * 60.0 / loop_len, 1
    return best[1], best[2]


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
        #: Monotonic time of the grid's last downbeat, or None if unknown.
        #:
        #: The grid used to have no clock of its own: the only boundary the
        #: bench could see was a playing loop's wrap. So after Stop All, with
        #: nothing playing, there was no boundary at all and every launch fired
        #: instantly — reported 2026-08-30 as "I stopped all the clips, then
        #: restarted the second track and it was not quantized". The tempo was
        #: known the whole time. Nothing could compute a bar line from it.
        self.phase_zero_at: float | None = None
        self.bpm: float | None = None
        self.bars: int | None = None
        #: THE QUANTIZE UNIT: the first take's own length, in seconds.
        #:
        #: This is Mitch's "base unit" and it is NOT derived from bpm. Deriving
        #: it was a bug I nearly shipped on 2026-08-30: a 6.939 s take reads as
        #: 138 BPM in 4 bars, so one BAR is 1.735 s, and a boundary every
        #: 1.735 s would let clips join four times inside the first loop. He
        #: caught it before it went out — "I don't know why it's inherently
        #: four bars. If it's my first clip, it should still be one bar."
        #:
        #: The bar count and the BPM are how the cycle is DESCRIBED. The cycle
        #: is what the grid COUNTS.
        self.cycle_s: float | None = None
        self.defined_by: int | None = None
        self._pending: int | None = None
        self._occupied: set[int] = set()

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
        self.cycle_s = loop_len
        self.established = True
        self.defined_by = loop
        self._pending = None
        return derived

    def note_loop_content(self, loop: int, occupied: bool) -> bool:
        """Track which loops hold audio. Always returns False — see below.

        This USED to drop the whole grid when the last clip was cleared, on the
        rationale "no clips, no grid": otherwise a fresh take gets quantized to
        the tempo of a grid whose clips are all gone.

        Mitch's call, 2026-08-30, and it is his instrument:

            "Even if we stop all clips, and even if the second track is two
            bars compared to the established base unit length that the first
            recorded clip establishes, we still need to reinitialize with those
            original settings. They should never be cleared away."

        The base unit is a property of the SESSION, not of whichever clips
        happen to exist right now. Losing it because the pads went empty is how
        a stable tempo turned into 73.7, then 34.6, then 54.9, then 179.3 BPM
        across four consecutive takes.

        The tradeoff, stated because it is real: the take after you clear
        everything is now counted in and length-quantized rather than
        free-form. Redefining the tempo takes an explicit track reset, which
        calls `reset()`. That is the only thing that clears a grid now.

        Occupancy is still tracked — callers use it, and it costs nothing.
        """
        if occupied:
            self._occupied.add(loop)
        else:
            self._occupied.discard(loop)
        return False

    def mark_phase_zero(self, now: float) -> None:
        """The grid's downbeat is NOW.

        Called wherever the engine's phase is zeroed — grid establishment,
        phase re-anchor, and Stop All — so the bench's idea of the bar line
        cannot drift away from the engine's.
        """
        self.phase_zero_at = now

    @property
    def bar_s(self) -> float | None:
        """One 4/4 bar in seconds — a DESCRIPTION, not the quantize unit.

        Kept for display. If you are about to quantize something, you want
        `cycle_s`. The two are equal only when the first take reads as one bar.
        """
        if not self.bpm or self.bpm <= 0.0:
            return None
        return BEATS_PER_BAR * 60.0 / self.bpm

    @property
    def beats_per_cycle(self) -> int:
        """THE ACTUAL FREE VARIABLE: how many beats the first take contains.

        Everything else in the tempo story is a way of NAMING this number.
        Mitch, 2026-08-30, on reading the spec:

            "There's a piece that isn't being mentioned, and that's whether
            we're in quarter notes, eighth notes, sixteenth notes. That's the
            slider that allows us to remain in one bar while adjusting
            different BPMs... it's possible we've already implicitly coded that
            and not named it."

        We had. `eighth_per_cycle = 8 * bars` IS the subdivision slider, wearing
        a bar count as a disguise. For a 6.939 s take these say the same thing:

            "four bars at 138 BPM"
            "one bar at 138 BPM, counted in sixteenths"

        His framing is the better one, because it keeps "one bar = my first
        clip" true, which is the invariant everything else here protects.

        Pick this number and the rest follows:

            bpm            = beats_per_cycle * 60 / cycle_s
            bars           = beats_per_cycle / BEATS_PER_BAR
            eighth_per_cycle = beats_per_cycle * 2    (beat = quarter note)

        NOT YET AN ABSTRACTION, deliberately. Today it can only take values
        implied by bar counts of 1/2/4/8, i.e. 4, 8, 16 or 32 beats. Making it
        the primitive would also allow 3, 6 or 12 for 3/4, and odd meters — a
        real feature, and not one to bolt on at 2 a.m. under a bug fix.
        """
        return (self.bars or 1) * BEATS_PER_BAR

    @property
    def eighth_per_cycle(self) -> int:
        """`beats_per_cycle` in the unit SooperLooper actually wants.

        SL computes cycle = eighth_per_cycle * 30 / bpm, so this has to track
        the subdivision. Left at a fixed 8 while the tempo rises, the engine's
        cycle shrinks below the take and it quantizes to a boundary the player
        never played — while the bench uses its own, and neither complains.
        """
        return self.beats_per_cycle * 2

    def next_boundary(self, now: float) -> float | None:
        """When the next bar line falls, or None if the grid cannot say.

        This is what makes a launch quantized with NOTHING playing. It answers
        from the tempo, which is what a grid is for, instead of from a wrap,
        which requires audio.
        """
        cycle = self.cycle_s
        if not self.established or not cycle or self.phase_zero_at is None:
            return None
        elapsed = now - self.phase_zero_at
        if elapsed < 0:
            return self.phase_zero_at
        return self.phase_zero_at + (math.floor(elapsed / cycle) + 1) * cycle

    def reset(self) -> None:
        """Track reset — back to no grid, so the next take defines it again.

        The ONLY thing that clears a grid. Not Stop All, not clearing clips.
        """
        self.established = False
        self.phase_zero_at = None
        self.cycle_s = None
        self.bpm = None
        self.bars = None
        self.defined_by = None
        self._pending = None
        self._occupied.clear()
