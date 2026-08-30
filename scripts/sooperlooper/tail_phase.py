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

#: How far the peak must fall FROM ITS OWN MAXIMUM before the tail is over.
#: 0.032 is -30 dB.
#:
#: MEASURED, 2026-08-30, seven takes on the appliance. At the first value tried
#: (-20 dB) Mitch reported "possible the decay is a bit steep". He was right and
#: it is not subjective: -20 dB cuts the ring-out while it is still at a TENTH
#: of its peak, which is a truncation rather than an ending. The measured decay
#: half-life across those takes was 0.25-0.48 s, so each extra 10 dB costs only
#: 0.35-0.85 s, and six of the seven still finish well inside their cap at
#: -30 dB. The seventh has a 1.34 s loop, where hitting the cap is correct.
#:
#: This used to be an absolute level (MPE_SL_TAIL_THRESH, 0.02) inherited from
#: the seam-weld work. The first trace off the appliance (2026-08-29) showed
#: why that cannot work: the whole ring-out peaked at 0.0487, so 0.02 was not
#: "quiet", it was 40% of the signal. The tail was cut at 0.0172 while still
#: audibly decaying. Worse, a quieter patch that never crosses 0.02 would never
#: arm the detector at all and would run silently to the cap — which is also
#: exactly what a dead peak meter looks like.
#:
#: Relative to the tail's own peak, the same number works for a loud pluck and
#: a quiet pad with nothing for the player to tune.
TAIL_RATIO = float(os.environ.get("MPE_SL_TAIL_RATIO", "0.032"))

#: Absolute noise floor. Below this is silence regardless of ratio — a decay
#: that asymptotes above its own -20 dB would otherwise never finish. Also what
#: decides whether a tail contained any signal at all.
TAIL_FLOOR = float(os.environ.get("MPE_SL_TAIL_FLOOR", "0.002"))
#: How long it must stay quiet before the tail is called done. Short enough to
#: feel immediate, long enough not to trip on a gap between two plucks.
TAIL_HOLD_S = float(os.environ.get("MPE_SL_TAIL_HOLD_MS", "80")) / 1000.0
#: Fallback bar length when no grid is established yet.
TAIL_FALLBACK_CAP_S = float(os.environ.get("MPE_SL_TAIL_CAP_MS", "2000")) / 1000.0

#: Where to append the raw peak series, one CSV row per sample. Unset = off,
#: and off costs one `is not None` per peak — on a stream that only exists
#: during a ring-out at all. Nothing is written until the tail ends, so the
#: audio thread never waits on a file.
TAIL_TRACE_PATH = os.environ.get("MPE_SL_TAIL_TRACE", "")

EXIT_DECAY = "decay"
#: The input was silent for the whole tail. Distinct from `decay` because it
#: means there was nothing to capture — worth seeing in the log rather than
#: hiding inside a generic ending.
EXIT_SILENT = "silent"
EXIT_CAP = "cap"
EXIT_WRAP = "wrap"
EXIT_ABANDONED = "abandoned"

#: How long a tail may stay below the noise floor before it is called silent.
#: Long enough that a slow attack or a late first meter report is not mistaken
#: for silence; far short of the cap, which would mean a bar of recorded room.
SILENT_GRACE_S = float(os.environ.get("MPE_SL_TAIL_SILENT_MS", "400")) / 1000.0

BEATS_PER_BAR = 4


def cap_for(bpm: float | None, *, loop_len: float = 0.0) -> tuple[float, str]:
    """The tail cap, and WHERE IT CAME FROM.

    The caller logs this. It used to log "capped at 4.078s (one bar)" while
    actually using the loop length, because no grid was established — a bar at
    120 BPM is 2.0s. A log line that names the wrong source is worse than no
    log line: the first real trace off the appliance had to be decoded against
    a number the log had misattributed.
    """
    if bpm and bpm > 0.0:
        return (60.0 / bpm) * BEATS_PER_BAR, "one bar"
    if loop_len > 0.0:
        return loop_len, "loop length, no grid established"
    return TAIL_FALLBACK_CAP_S, "fallback, no grid and no loop length"


def bar_seconds(bpm: float | None, *, loop_len: float = 0.0) -> float:
    """One bar, from the grid if there is one. See `cap_for` for the source."""
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
        ratio: float = TAIL_RATIO,
        floor: float = TAIL_FLOOR,
        hold_s: float = TAIL_HOLD_S,
        trace: bool = False,
    ) -> None:
        self.started_at = started_at
        self.cap_s = cap_s
        self._ratio = ratio
        self._floor = floor
        self._hold_s = hold_s
        #: The loudest sample this tail has seen. The exit level is derived
        #: from it, so the detector calibrates itself to each take.
        self.peak_max = 0.0
        #: The settle. Quiet does not count until something loud has happened —
        #: at the instant the overdub starts the meter has reported nothing,
        #: and treating that as "decayed" cuts the ring-out to zero.
        self.saw_loud = False
        self._quiet_since: float | None = None
        #: (t_rel, peak) for every sample fed in, when tracing. The thresholds
        #: above were inherited from the seam-weld work on a different signal
        #: path; this is how they stop being inherited.
        self.trace: list[tuple[float, float]] | None = [] if trace else None

    def peak(self, value: float, now: float) -> str | None:
        """Feed one input peak. Returns an exit reason, or None to continue."""
        if self.trace is not None:
            self.trace.append((now - self.started_at, value))
        if value > self.peak_max:
            self.peak_max = value
        if value >= self._floor:
            self.saw_loud = True
        if not self.saw_loud:
            # Nothing above the noise floor yet. Not silence-with-a-verdict:
            # the tail has simply not begun. `tick` decides when to give up.
            return None
        if value >= self.exit_level:
            self._quiet_since = None
            return None
        if self._quiet_since is None:
            self._quiet_since = now
            return None
        if now - self._quiet_since >= self._hold_s:
            return EXIT_DECAY
        return None

    @property
    def exit_level(self) -> float:
        """The level this tail counts as quiet, from its own peak.

        Floored, so a very quiet take cannot set an exit level below the noise
        floor and then wait forever for the room to get quieter than it is.
        """
        return max(self.peak_max * self._ratio, self._floor)

    def tick(self, now: float) -> str | None:
        """The cap. Checked from the idle loop, so it holds with no meter at
        all — a peak feed that never arrives must not mean an endless overdub."""
        if self.saw_loud:
            if now - self.started_at >= self.cap_s:
                return EXIT_CAP
            return None
        # Never got above the noise floor. Either the take ended in silence or
        # the meter is not feeding; both mean there is nothing to capture, and
        # holding a live overdub open for a full bar over silence records the
        # room. Give it a fair chance to start, then stop.
        if now - self.started_at >= SILENT_GRACE_S:
            return EXIT_SILENT
        return None

    def elapsed(self, now: float) -> float:
        return now - self.started_at

    @property
    def ratio(self) -> float:
        return self._ratio

    @property
    def floor(self) -> float:
        return self._floor

    @property
    def hold_s(self) -> float:
        return self._hold_s


#: Header written once, when the trace file is first created.
TRACE_HEADER = ("tail_id,loop,t_rel,peak,exit_reason,exit_elapsed,"
                "cap_s,peak_max,exit_level,ratio,floor,hold_s\n")


def append_trace(
    path: str,
    *,
    tail_id: int,
    loop: int,
    tail: TailPhase,
    reason: str,
    elapsed: float,
) -> str | None:
    """Append one finished ring-out's peak series. Never raises.

    Called once per tail, from the bench thread, after the overdub has already
    been closed — so a slow or full disk cannot delay the thing that actually
    matters. A trace that fails to write is not worth taking the bench down
    for, but it must not fail SILENTLY either: that is the exact shape of bug
    this file exists to measure. Returns the failure for the caller to log —
    this module cannot import the bench logger without a cycle.
    """
    if not path or tail.trace is None:
        return None
    rows = "".join(
        f"{tail_id},{loop},{t_rel:.4f},{peak:.5f},{reason},{elapsed:.4f},"
        f"{tail.cap_s:.4f},{tail.peak_max:.5f},{tail.exit_level:.5f},"
        f"{tail.ratio:.4f},{tail.floor:.5f},{tail.hold_s:.4f}\n"
        for t_rel, peak in tail.trace
    )
    try:
        new = not os.path.exists(path)
        with open(path, "a") as handle:
            if new:
                handle.write(TRACE_HEADER)
            handle.write(rows)
    except OSError as exc:  # pragma: no cover — disk-full / permissions
        return f"tail trace: could not append to {path}: {exc}"
    return None
