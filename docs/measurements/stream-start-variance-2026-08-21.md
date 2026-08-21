# The 256 spread is not noise — the rate is set at stream start (2026-08-21)

## The observation

`256x3` condition A has now been measured three times at n=15, and produced three different
answers: **12.07, 1.53, 7.80**. The third was taken post-hygiene with separate harness
invocations, which removed run order as the explanation. It did not converge.

The instinct is "noisy measurement, take more samples". **That is wrong, and the per-run
data shows why.**

## Per-run values

```
T11  256 (10:08)   10 16  8 15 20 12  8  6 12 20  4 10 14 12 14
T13  256 (12:07)    4  0  2  2  0  0  1  0  0  2  2  4  2  4  0
hyg  256 (post)    16  4  6 10  4  4 12  4 12 10  6  6  8  6  9
hyg  512 (post)     0  0  0  0  0  0  0  0  0  0  0  0  0  0  2
```

| cell | mean | sd | SE | min | max |
|---|---:|---:|---:|---:|---:|
| T11 256 | 12.07 | 4.64 | 1.20 | 4 | 20 |
| T13 256 | 1.53 | 1.55 | 0.40 | 0 | 4 |
| post-hygiene 256 | 7.80 | 3.63 | 0.94 | 4 | 16 |
| post-hygiene 512 | 0.13 | 0.52 | 0.13 | 0 | 2 |

**Each cell is internally consistent around its own mean.** T13's cell never exceeds 4;
the post-hygiene cell never drops below 4. They do not overlap.

Separation between cells:

| comparison | difference | SE | sigma |
|---|---:|---:|---:|
| T11 vs T13 | 10.53 | 1.26 | **8.3** |
| T13 vs post-hygiene | 6.27 | 1.02 | **6.1** |
| T11 vs post-hygiene | 4.27 | 1.52 | 2.8 |

These are not draws from one distribution. **They are three different rates.**

## What this means

**The xrun rate is established when the audio stream starts, and then holds for the life of
that stream.** Within a stream it is stable enough to measure to within ~1/min. Between
streams it varies by an order of magnitude.

Each harness invocation sets the buffer, restarts jackd, and opens **one** stream. All 15
runs then sample that single stream.

### The methodological consequence

**n=15 runs inside one stream is not n=15 independent samples.** It is 15 correlated
observations of one draw. Every confidence interval computed this way — across the entire
effort — is a *within-stream* interval. It says nothing about reproducibility across stream
starts, and stream-start variance is an order of magnitude larger.

This is the same failure shape the project keeps meeting: a number that looks authoritative
because it has a tight error bar, where the error bar is measuring the wrong axis.

### Why 512 looked stable

512 was not immune — its rate is simply near zero (0.13/min), so a stream-to-stream
difference of the same *proportional* size is invisible. The identical pre- and
post-hygiene result at 512 (0.13, 14/15 both times) is therefore **weak** evidence that
nothing changed, not strong evidence.

## Candidate mechanisms

Both are properties fixed at stream open, which is what the data demands:

1. **ADAPTIVE endpoint lock.** The Sound Blaster has no feedback endpoint; the host guesses
   the rate and the device adapts. The quality of that lock is established at stream start
   and persists.
2. **USB frame phase.** 256 frames = **5.33 USB frames**, so period boundaries and the 1 ms
   frame grid sit at a fixed phase offset determined by when the stream opened. A different
   start instant gives a different standing phase relationship for the whole stream.

**Mechanism 2 makes a sharp prediction**, and it is the one worth testing: a period that
divides evenly into 1 ms frames has **no phase freedom**. If stream-start variance vanishes
at an aligned period, alignment is the mechanism.

This promotes T12 from a curiosity to the primary experiment.

## Required protocol change

**Sample stream starts, not minutes.** Replace `1 stream x 15 runs` with `N streams x k
runs`, restarting jackd between streams. Report both variances separately:

- **within-stream sd** — what the old protocol measured
- **between-stream sd** — what actually determines whether a configuration is shippable

A configuration whose *mean* is acceptable but whose stream-to-stream spread reaches 12/min
is not shippable, because the user gets one stream per power-on and cannot re-roll it.

`measure-latency-run.sh` already has `--restart-between`; it has not been used for this.

## What this does to Phase 0

**Phase 0 produced no measurable improvement, and could not have shown one.**

- 512 A: 0.13, 14/15 before and after — identical.
- 1024x3 D, 8 loops: 0.00, 15/15 before; **0.20, 12/15 after**. Not a regression, and not an
  improvement — both are single-stream draws.
- 256 A: 7.80, a third distinct draw.

Stream-start variance swamps whatever Phase 0 changed. **This does not mean the work was
wrong** — a service restarting 617 times, a broken pressure-remap, unpinned measurement
instruments and a saturated CPU0 were real defects and are correctly fixed. It means the
hygiene delta cannot be measured until the protocol controls for stream starts.

**The shipping claim stays withdrawn.** "0.00, 15/15" was one stream. So is "0.20, 12/15".
Neither is the number.

## Next

1. **Re-measure 1024x3 D 8 loops as 10 streams x 3 runs.** This is the shipping
   configuration and its real figure is the between-stream distribution.
2. **T12 alignment, as a stream-start experiment**: 192x3 (exactly 4 USB frames, no phase
   freedom) against 256x3 (5.33 frames), 10 streams each. If 192 has materially lower
   between-stream variance, alignment is the mechanism and the ladder has been on the wrong
   grid all along.
3. Everything in Phase 3 waits for a protocol that can detect its effect.
