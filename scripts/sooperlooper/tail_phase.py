"""The ring-out phase: how a take's decay gets into the loop, and when it stops.

Closing a take with `overdub` instead of `record` makes SooperLooper suppress
the right-edge fade, so the note still ringing when the pad was hit lands in the
loop head instead of being cut off. That is the whole feature. What was never
modelled is when the overdub should END — it was inferred from
`sl_state == OVERDUBBING` and ended at the next wrap, which is one full pass of
whatever length the loop happens to be. On a four-bar loop that is four bars of
live input recorded on top of the take.

This phase owns the answer. It ends on the FIRST of:

  decay  the input has actually fallen quiet — the musical answer, and the one
         that was measured working on 2026-08-26 before the seam-weld pipeline
         was removed and took the detector with it
  cap    one bar. A ring-out longer than a bar is not a ring-out
  wrap   the playhead came round; one pass is the hard ceiling either way
  abandon  the engine left OVERDUBBING for some other reason

`saw_loud` is why the decay exit needs a settle: at the instant the overdub
starts the meter has not yet reported anything, so "below threshold" is true
before the tail has begun. Exiting on that would cut the ring-out to nothing —
the exact artefact the feature exists to remove. Quiet only counts once the
phase has heard something loud.
"""

from __future__ import annotations

import os

#: Input peak below this counts as quiet. Measured default from the seam-weld
#: work; overridable because room noise floors differ.
TAIL_THRESH = float(os.environ.get("MPE_SL_TAIL_THRESH", "0.02"))
#: How long it must stay quiet before the tail is called done. Short enough to
#: feel immediate, long enough not to trip on a gap between two plucks.
TAIL_HOLD_S = float(os.environ.get("MPE_SL_TAIL_HOLD_MS", "80")) / 1000.0
#: Fallback bar length when no grid is established yet.
TAIL_FALLBACK_CAP_S = float(os.environ.get("MPE_SL_TAIL_CAP_MS", "2000")) / 1000.0

EXIT_DECAY = "decay"
EXIT_CAP = "cap"
EXIT_WRAP = "wrap"
EXIT_ABANDONED = "abandoned"

BEATS_PER_BAR = 4


def bar_seconds(bpm: float | None, *, loop_len: float = 0.0) -> float:
    """One bar, from the grid if there is one.

    Falls back to the loop's own length — on a one-bar loop those are the same
    number, and on a longer one the wrap exit covers it anyway.
    """
    if bpm and bpm > 0.0:
        return (60.0 / bpm) * BEATS_PER_BAR
    if loop_len > 0.0:
        return loop_len
    return TAIL_FALLBACK_CAP_S


class TailPhase:
    """One ring-out. Pure decision logic — it sends nothing and knows no OSC."""

    def __init__(
        self,
        *,
        started_at: float,
        cap_s: float,
        thresh: float = TAIL_THRESH,
        hold_s: float = TAIL_HOLD_S,
    ) -> None:
        self.started_at = started_at
        self.cap_s = cap_s
        self._thresh = thresh
        self._hold_s = hold_s
        #: The settle. Quiet does not count until something loud has happened.
        self.saw_loud = False
        self._quiet_since: float | None = None

    def peak(self, value: float, now: float) -> str | None:
        """Feed one input peak. Returns an exit reason, or None to continue."""
        if value >= self._thresh:
            self.saw_loud = True
            self._quiet_since = None
            return None
        if not self.saw_loud:
            return None
        if self._quiet_since is None:
            self._quiet_since = now
            return None
        if now - self._quiet_since >= self._hold_s:
            return EXIT_DECAY
        return None

    def tick(self, now: float) -> str | None:
        """The cap. Checked from the idle loop, so it holds with no meter at
        all — a peak feed that never arrives must not mean an endless overdub."""
        if now - self.started_at >= self.cap_s:
            return EXIT_CAP
        return None

    def elapsed(self, now: float) -> float:
        return now - self.started_at
