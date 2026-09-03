# Loop-over-loop alignment, and what the MIDI output offset actually does

**Date:** 2026-09-03 · **Branch:** `dev` · **Harness:** `scripts/measure-loop-alignment.py`
**Graph:** jackd period 96 × 2 @ 48 kHz, Scarlett 4i4 · Surge XT CLI · SooperLooper 15 loops

## The question

"Are we doing the correct loop↔MIDI input offset so that I'm recording with the
timing I think I'm recording?"

## Answer

**For grid-fired notes, the output offset does not affect loop-over-loop
alignment at all, and the appliance's alignment is already ~0 ms.**

The offset displaces every grid-fired note equally. When one pass is recorded
over another, both passes carry the same displacement and it cancels exactly.
This is measured, not argued: changing the offset over a 40 ms range moved the
alignment by **0.0 ms**, while injecting 20 ms into *one* pass moved it by
**20.2 ms**.

| offset applied | median error (ms) |
|---|---|
| auto (−3.25) | −1.1, 0.2, 0.1, 0.1 |
| 0 | 0.3 |
| +20 | 0.3 |
| −20 | −1.1, 0.1 |
| **+20 into pass 2 only — CONTROL** | **20.2** |

n = 8 measurement cells + 1 control. Within-run sd 1.3–1.6 ms; between-cell
spread [−1.1, +0.3]. Every cell agrees with zero within one within-run sd.

**Where the offset does still matter:** aligning grid-fired notes against
anything *not* shifted by it — you playing by hand, or a loop you recorded by
hand. There the offset is real and the DAC latency enters too, because you play
to what you hear. That leg is not measured here.

## Pre-registration

- **Question:** does the MIDI output offset change whether an overdub lands on the take?
- **Claim class:** rate (a fixed displacement), n = 8 cells + 1 control
- **Prediction (written before the offset sweep ran):** all offsets read ≈ −1.1 ms,
  because a displacement common to both passes cancels.
- **Falsifier:** a reading that tracks the offset.
- **Impossible if:** onsets ≠ notes played; loop length not a whole number of beats.
  Both asserted in the harness.
- **Conformance:** positive control (+20 ms into pass 2) → 20.2 ms. Negative
  controls: silence halts, single pass halts, blind detector halts. 27 tests in
  `tests/test_loop_alignment_analysis.py`.

## Method

Record a take on the beats, overdub at 0.3 of a beat, require every consecutive
onset interval to be 150 ms or 350 ms. The loop's own start phase is common to
both passes and cancels; what remains is the flam a player would hear.

## Three ways this instrument read clean while blind

Each was caught by a control or an assertion, not by inspection. All three
produced *tight, plausible* numbers.

1. **Measuring the loop's start phase instead of the alignment.** The first
   design used "onset position mod beat". It reported five onsets agreeing to
   sub-millisecond, all 145.5 ms off the grid — a real, precise measurement of
   where SooperLooper started the loop, which is not the question. Fixed by
   making the measurement differential.
2. **Sign cancellation at a half-beat shift.** With the overdub at half a beat
   the two interval directions are (half + d) and (half − d); the same
   displacement arrives with both signs and the median lands on whichever
   direction was counted once more. A 20 ms control read 18.3 ms — right
   magnitude, by an accident of parity. The synthetic tests passed for the same
   reason, so they were never controls for it. Fixed by shifting to 0.3 of a
   beat, where both directions yield +d.
3. **Counting note-off releases as notes.** 16 and 17 onsets for 12 notes. A
   release sits a *fixed* distance after its note-on, so the spurious intervals
   were consistent and tight: the harness reported −39.6 ms with sd 1.07, and
   the identical −39.6 ms with a 20 ms control injected, because no injection
   could move the events dominating the median. Fixed by shortening the note and
   asserting one onset per note played.

**The lesson worth keeping: precision is not evidence.** Failures 2 and 3 both
produced sub-millisecond spreads while measuring the wrong population. The only
thing that separated them from a result was a control that changed something and
required the number to move.

## Two appliance faults found along the way

- **The looper was deaf.** `mpe-looper:loop0_in_1` was connected to nothing;
  `Surge XT:out_1` went only to `system:playback_1` and `mpe-peak-meter:in_1`.
  The graph teardowns during the 2026-09-02 loopback work left the looper's
  audio inputs unwired and nothing put them back. Recording captured digital
  silence — for real, not only under the harness. Repaired with
  `bash scripts/sooperlooper/wire-jack-graph.sh connect` (0 failures).
  The harness now proves the looper can hear Surge before recording anything.
- **`sl-watchdog.py` is not installed as a unit.** It exists to detect and repair
  exactly that fault. `systemctl is-active mpe-sl-watchdog` → inactive; the only
  watchdog unit on the Pi is `surge-watchdog.service`. Not fixed here.

Also noted: `meter.state` reported `looper_client=1 looper_playback=1` throughout
the outage. Those fields did not cover the input side.
