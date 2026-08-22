# V1 verdict — the fixed-cost model was wrong, and W1's ladder is confounded

**2026-08-21.** Reading of `v1-fixed-cost-2026-08-21.md` (V0 + V1 + V2, ~10 min Pi time).

## Retracted: the ~1.1 ms fixed per-callback cost

`W1-VERDICT-compute-bound-2026-08-21.md` proposed **a = 1.10 ms fixed per callback**, fitted
across three cells and used to retrodict the T11 ladder.

**V1 silence test, outcome row 3 — not flat, scales with buffer:**

| buffer | median ms (silence) | ~% of deadline |
|---|---|---|
| 1024 | 1.29 | ~6% |
| 512 | 0.74 | ~7% |
| 256 | 0.42 | ~8% |

Fitting `a + b*N` on these: **a = 0.13 ms** — roughly **8x smaller** than the claimed 1.10 ms,
and small enough that percent-of-deadline is nearly flat at silence.

**The model is withdrawn.** The three-point regression over `dsp_p99` was noise. This was the
stated weakness when it was proposed (`PLAN-V-fixed-callback-cost.md`: *"a least-squares fit
over three points, n=1 each, using a statistic W1 itself showed is wrong"*), and V1 existed
to kill it in ten minutes rather than after a profiling session. **V4 is gated off — do not
profile for a fixed constant that does not exist.**

## Dead: the single-client architecture refactor

**Surge ON minus OFF at 1024, silence: ~35 us.** JACK graph traversal and inter-client
context switching are **noise**. The ~1.3 ms measured at 1024 is baseline graph plus
instrumentation, not Surge.

**Hosting the looper inside Surge's process would buy nothing.** A substantial refactor
avoided for ten minutes of measurement — the clearest win of Plan V.

## Dead: the `ondemand` explanation for the pops

V0 found the box **already `performance` @ 1800 MHz with `arm_boost=1`**.

- **V5 and V6 are moot** — we were already at pinned max clock. W1 likely ran at full clock
  throughout.
- **The governor-ramp explanation for Mitch's "quick pops on chords from silence" is
  withdrawn.** There is no ramp; the clock was pinned.

## New confound found — W1's DSP ladder is not usable

**The poly governor was active with an unset env during W1 and every prior cell.**

It is a **feedback controller that reduces polyphony in response to CPU load** — the exact
quantity being measured. W1's ladder (58.9 / 62.4 / 76.1%) was therefore recorded while voice
count was being silently varied by load. **Those figures cannot carry the weight placed on
them.** If poly was cut harder at 256, the true per-voice cost there is *worse* than 76.1%
suggests.

### What survives untouched

**The graph-overrun finding stands.** `JackEngine::XRun: Surge XT was not finished`, **zero**
`ALSA: xrun of at least N msecs` lines, fill flat at ~83%. That is a presence/absence result,
not a rate, and no controller affects it.

**The appliance is compute-bound. Only the ladder numbers are confounded.**

## What the data now suggests — to be tested, not assumed

Subtracting silence from W1's (confounded) loaded figures:

| buffer | loaded | silence | voice work | absolute | per frame |
|---|---|---|---|---|---|
| 1024 | 58.9% | ~6% | ~53% | 11.3 ms | **11.0 us/frame** |
| 256 | 76.1% | ~8% | ~68% | 3.62 ms | **14.1 us/frame** |

Indicatively **~28% worse per frame at 256** — i.e. the inefficiency is in the **voice path at
small buffers**, not in fixed overhead. **This arithmetic uses confounded inputs and is a
hypothesis only.**

## Next

| # | cell | Pi time | why |
|---|---|---|---|
| **V7** | **Loaded ladder 1024/512/256, poly pinned 16, poly governor OFF, strict mode** | ~15 min | **the valid version of W1's ladder.** Everything we believe about "how bad is 256" rests on confounded numbers |
| **V3** | `1024 x 2` at n >= 3 | ~15 min | independent free latency win; unaffected by any of this |
| **EAR** | Mitch plays chords from silence on heavy patches, poly governor now OFF | 0 | see below |

**V7 before V3** — V3's value does not depend on V7, but V7 repairs the foundation.

### The pop hypothesis, now testable for free

With `ondemand` excluded, a better candidate: **the poly governor itself.** It cuts polyphony
when CPU rises; from silence, a chord on a heavy patch spikes CPU, the controller drops poly,
and **voices are stolen mid-note** — brief, load-dependent, and audible exactly as described.

**It is now disabled.** The cheapest test in the project is Mitch playing the same thing and
reporting whether the pops persist. **If they vanish, the pops were our own controller.**

Report instruments as required by `PROMPT-V-fixed-callback-cost.md`: absolute **ms** as well
as percent, **p99.9 / p99.99 / max** rather than p99, and the jackd journal per window.
