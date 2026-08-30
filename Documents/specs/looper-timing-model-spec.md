# The looper's timing model

Status: Intended behaviour, agreed with Mitch 2026-08-30. Implemented.

This file exists because these rules have been re-derived, misremembered and
re-broken repeatedly, each time from a plausible-sounding assumption that
nobody had written down. Every section below names the assumption it replaces
and the symptom that assumption produced.

If you are about to change timing behaviour, this is the file that says what it
is supposed to be. If reality and this file disagree, one of them is a bug —
find out which before writing code.

---

## 1. The three quantities, which are NOT the same thing

The single largest source of error here. They get conflated because for a
one-bar first take they happen to coincide.

| | what it is | who uses it |
|---|---|---|
| **cycle** | the first take's own length | the quantize unit — every boundary |
| **bar count** | how many 4/4 bars we *call* that cycle | display, and `eighth_per_cycle` |
| **BPM** | beats per minute implied by the two | display, and the engine's tempo |

**The cycle is the first take. It is never derived from the BPM.**

Nearly shipped broken on 2026-08-30: a 6.939 s take reads as 4 bars at 138 BPM,
so one *bar* is 1.735 s. Computing the boundary from the bar would have let
clips join four times inside the loop the player thinks of as one unit. Mitch
caught it in review:

> "A six second clip reading as 138 BPM in four bars — I don't know why it's
> inherently four bars. If it's my first clip, it should still be one bar. I
> just want to make sure we're not misaligning again."

`GridState.cycle_s` is the quantize unit. `GridState.bar_s` exists for display
and must never be used to place a boundary.

The engine has to agree, or it will quantize to its own idea of a cycle while
the bench quantizes to another, silently. SooperLooper computes
`cycle = eighth_per_cycle * 30 / bpm`, so `eighth_per_cycle` scales with the
bar count (`GridState.eighth_per_cycle`). Fixed at 8 with a rising tempo, the
engine's cycle shrinks below the take.

**Invariant, tested:** for any first take, the bench boundary and the engine
cycle both equal the take length exactly.

## 2. Establishing the grid

The first take records free-form: no count-in, no quantize, no rounding. There
is nothing to sync to yet. Its length becomes the cycle.

The bar count and tempo are then fitted: score each candidate bar count
(1, 2, 4, 8) by log-distance from ~100 BPM and take the closest.

Before 2026-08-30 the take was called one bar unconditionally, with a fallback
outside 20-300 BPM so wide it never fired. Real consequences, measured on the
appliance: four consecutive takes produced 73.7, 34.6, 54.9 and 179.3 BPM, and
every value under 60 BPM pushes SooperLooper into doubling `eighth_cycle` on
its own (`engine.cpp`), which desynchronises the HUD from the sync boundaries.

The tempo is EXACT, never rounded — rounding makes the grid bar differ from the
recorded audio and the defining take walks away from every later clip. Round
for display only (`display_bpm`).

## 3. What clears the grid: exactly one thing

**Track reset (Shift+StopAll, held).** Nothing else. Not Stop All, not clearing
every clip one at a time, not an empty session.

Mitch, 2026-08-30:

> "Even if we stop all clips, and even if the second track is two bars compared
> to the established base unit length that the first recorded clip establishes,
> we still need to reinitialize with those original settings. They should never
> be cleared away."

This replaces a rule called "no clips, no grid", which dropped the whole grid
when the last clip was cleared. That is the direct cause of the walking tempo
in section 2: each take cleared the pads, dropped the base unit, and redefined
it from whatever was played next.

**Accepted consequence:** the take after you clear everything is counted in and
length-quantized rather than free-form. Redefining tempo takes an explicit
track reset. This is a deliberate trade, not an oversight.

## 4. When a clip starts

Three cases. The question is always **whether the session is sounding**, never
whether the pressed track is.

| situation | behaviour |
|---|---|
| nothing playing anywhere | start **immediately**; this clip becomes the phase reference |
| something playing, this track too | wait for that track's wrap (sample-accurate) |
| something playing, this track silent | wait for the **grid's** next cycle boundary |

Row 1, in Mitch's words:

> "When I've stopped all and I start a clip, we've reset the phase to zero —
> quantized start could and probably is most simply left as true, but it should
> also mean that start happens immediately."

A downbeat is where the music starts; you cannot be late for something that has
not begun. Waiting for the "next" boundary here sits in silence for a whole
cycle before the first note.

**Starting into silence moves the PHASE, never the LENGTH.** `mark_phase_zero`
sets where boundaries fall in time. The cycle is still the first take's, and
still is after any number of stops and starts.

Row 2 vs row 3 is why the grid needs a clock of its own. A silent track has no
wrap to wait for, so before the grid could name its own boundary there was
nothing to fire it.

Row 3 is where the old code asked only about the pressed track: launching a
stopped track while other tracks played fired instantly, landing off the beat
of the music it was joining.

**Open, deliberately:** a clip may join at any cycle boundary, not only at the
phrase boundary of a longer clip. Most loopers behave this way. If joining a
4-cycle clip should only be allowed at its top, that is a one-line change here
and a real musical decision — not an oversight.

## 5. Stop All

Not quantized. Per-clip stop waits for the boundary because it is a musical
edit; Stop All is a transport action — you want silence now, not at the end of
the bar. `mute_quantized` is lifted for the duration and restored after.

It pauses every loop, resets the grid phase to zero, and **keeps the grid**.
Any queued launches are abandoned rather than fired late.

## 6. The ring-out (tail)

A take closes with `overdub` rather than `record`, which suppresses
SooperLooper's right-edge fade so the note still ringing lands in the loop
head. That overdub ends on the FIRST of:

- **decay** — the input has fallen to `MPE_SL_TAIL_RATIO` (0.01, -40 dB) of
  *this tail's own peak*, held for `MPE_SL_TAIL_HOLD_MS`
- **cap** — one cycle; a ring-out longer than that is not a ring-out
- **wrap** — the playhead came round
- **silent** — never rose above the noise floor at all (400 ms)

The threshold is relative because an absolute one cannot work: measured
2026-08-29, a real ring-out peaked at 0.0487, so the old fixed 0.02 was 40% of
the signal, not "quiet". Worse, a take peaking below it never armed the
detector and ran to the cap in silence — indistinguishable from a dead peak
meter.

`MPE_SL_TAIL_TRACE` writes every peak sample of every ring-out to CSV. That is
how the numbers above stopped being guesses, and how the next ones should be
chosen.

## 7. The rule behind all of it

Every bug in this file came from one shape: **a timing reference inferred from
something that happens to be true right now**, rather than held explicitly.

- the boundary inferred from a playing loop's wrap — so silence meant no time
- the grid inferred from clips existing — so an empty session had no tempo
- the cycle inferred from the bar count — so a fitted tempo shrank the unit
- the session's state inferred from one track's state — so joining fired early

When adding timing behaviour, ask what it is inferring, and whether that thing
can be false while the timing is still supposed to hold.
