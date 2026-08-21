# T12's design was confounded — alignment is not refuted (2026-08-21)

Correction to `t12-stream-sample-2026-08-21.md`. **The design error is mine**, in the
prompt that specified the comparison.

## What was run, and why it cannot answer the question

| config | period | USB frames | aligned | total @ n3 |
|---|---:|---:|---|---:|
| 192 x 3 | 4.00 ms | **4.00** | **yes** | 12.0 ms |
| 256 x 3 | 5.33 ms | 5.33 | no | 16.0 ms |

**192 is a 25% smaller period and carries 33% less total buffer than 256.** The comparison
changed *two* variables — alignment **and** period size.

T13 already established that **period size binds** (128x6 vs 256x3, identical total buffer,
466x apart). So "the smaller period was worse" is the outcome T13 predicts on period size
alone. The experiment cannot separate that from alignment, and this is the same failure as
E1 — two variables at once — in an experiment written while enforcing the one-variable rule
on everyone else.

**Alignment is not refuted. It is untested.**

## What the data does support

Read as two separate effects, the result is coherent and interesting:

| config | between-stream shape | mean |
|---|---|---:|
| 256 x 3 (misaligned) | **bimodal** — 6 streams at 2-5, 2 streams at 18-22 | 7.1 |
| 192 x 3 (aligned) | **unimodal** — every stream 10.7-17 | 14.0 |

Alignment made the exact prediction that held: **an aligned period has no phase freedom
against the 1 ms frame grid, so there is no start-phase lottery.** 192 produced no bad
draws. It removed the bimodality.

It also raised the level — which period size explains.

**Working model: period size sets the rate; frame alignment sets the between-stream
variance.** Two independent terms, consistent with everything measured so far.

That model makes a testable prediction: **an aligned period *larger* than 256 should be
both low-rate and unimodal.**

## The test that isolates alignment

At 48 kHz a full-speed frame carries 48 samples, so aligned periods are multiples of 48:
**192, 240, 288, 336, 384, 480**. None of the powers of two align — 256 = 5.33 frames,
512 = 10.67, 1024 = 21.33. **The entire ladder measured to date has been on the misaligned
grid.**

**Run 240 x 3 against 256 x 3.** 15.0 ms total against 16.0 ms — **6.25% apart**, versus
the 33% gap that confounded T12. Ten streams each.

- If 240 is unimodal at a rate near 256's *good* mode, alignment is confirmed as the
  variance mechanism.
- If 240 is bimodal too, alignment is genuinely refuted and the lottery is the ADAPTIVE
  endpoint's clock lock, not frame phase.

Either answer closes the question. The first one is also immediately useful.

## If alignment is confirmed

**384 x 3 becomes the shipping candidate.** 8 frames exactly, 24 ms total — against the
current 512 x 3's 32 ms. Aligned, so no bad draws, at a period between the two we know
best. **That is a 25% latency reduction with better determinism**, on the dongle, with no
hardware change.

Worth measuring 288 x 3 (18 ms) as well if 384 holds.

## The commercial framing stands

The write-up's central point is correct and is the most important sentence in it:

> **The commercial problem on 256 x 3 is two products at power-on (~4 vs ~20/min), not a
> noisy mean.**

A user gets one stream per power-on and cannot re-roll it. A configuration that is usually
fine and occasionally four times worse is not shippable, whatever its mean. **Report
between-stream spread, not just the mean, for every configuration from here on.**

## Also confirmed

Within-stream tightness holds (4/4/4 on 256 stream 09). `meter_live=1` on every window.
Harness fixes landed: stream logs cleared before each restart, duplicate-tag guard uses
`exit` not `return`.
