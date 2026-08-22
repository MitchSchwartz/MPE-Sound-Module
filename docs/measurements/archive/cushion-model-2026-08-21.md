# The cushion does not deplete — so what empties it? (2026-08-21)

## The arithmetic that forces this question

At `1024 x 3` the cushion is `(3-1) x 1024` = **2048 frames = 42.7 ms**.
The worst stall ever measured on this box is **429 us** (Step 2 cyclictest, under real load).

**That is a factor of 100.** Yet xruns occur at this config.

## Why "the cushion slowly wears down" is wrong

JACK writes a **full period on every wakeup, regardless of when that wakeup happens.** Let
`P` = period, `d_k` = how late the k-th write is.

Just before write *k*, the buffer has drained by `P + d_k - d_{k-1}`. The write adds `P`.
So:

```
fill_after(k) = fill_after(k-1) - d_k + d_{k-1}
              = fill_0 + d_0 - d_k
```

**The level depends only on the current delay, not on the history.** Lateness produces a
transient dip whose depth is `d_k`, and the level returns as soon as the write lands. There
is no accumulation and no ratchet. A late callback is a **phase shift, not a withdrawal.**

This also matches T5: index of dispersion **1.091** (`t5-soak-2026-08-21.md`). A ratcheting
reserve would step down on a cadence and produce *periodic* xruns — dispersion near 0. We
measured Poisson-random.

**Consequence:** to empty the buffer, a **single** stall must exceed the entire cushion —
42.7 ms at `1024 x 3`. Nothing we have measured is within two orders of magnitude of that.

**So the xruns at 1024 are not simple producer-lateness drain events.** Something else is
happening, and every test so far has assumed otherwise.

## Candidate models

| # | model | mechanism | predicted xrun rate vs `n` (period fixed) |
|---|---|---|---|
| **P1** | **rare giant stall** | some unmeasured stall exceeds the whole cushion | ~0 at every `n` — **already in trouble**, since we observe non-zero at n=3 and cyclictest saw no >1 ms event under load |
| **P2** | **constant clock mismatch** | device consumes faster than host refills; level drains linearly | **rate proportional to 1/(n-1)** — hyperbolic |
| **P2'** | **rate-matching noise / feedback hunting** | async feedback loop is noisy; fill level *random-walks* | **rate proportional to 1/(n-1)^2** — hitting time of a random walk against a barrier scales as distance squared |
| **P3** | **counter artifact** | the xrun counter increments on something that is not an underrun | **flat in `n`** — cushion size irrelevant |

**P2' is the one that fits what we already know.** Pure constant drift (P2) would give
near-periodic xruns and a dispersion near 0; we measured **1.091**, i.e. Poisson-random. A
random walk hitting a barrier produces approximately Poisson arrivals. It also explains why
the Scarlett (async, feedback endpoint, an actual control loop) behaved *worse* than the
Sound Blaster rather than better.

**P3 cannot be dismissed.** It is the [measurement-integrity](../../MEMORY.md) shape that has
bitten this project repeatedly: a reading that looks the same whether the system is fine or
broken.

## How the test shape must change

Every cell so far has varied the **period** (64/128/192/240/256/512/1024) at **fixed
nperiods=3**. That varies the *deadline* and the *cushion* together, and it has now been
run into the ground.

**Invert it: fix the period, sweep the cushion.**

At a fixed period of 1024 the compute deadline is constant at 21.3 ms — the load Surge
already meets with zero xruns. Only the cushion changes. The three models then give three
**distinguishable curves**, which is what no previous test has offered:

| `n` | cushion | latency | P2 predicts | P2' predicts | P3 predicts |
|---|---|---|---|---|---|
| 2 | 21.3 ms | 42.7 ms | 2.0x the n=3 rate | 4.0x | same |
| 3 | 42.7 ms | 64.0 ms | baseline | baseline | same |
| 4 | 64.0 ms | 85.3 ms | 0.67x | 0.44x | same |
| 6 | 106.7 ms | 128.0 ms | 0.40x | 0.16x | same |

**Linear vs quadratic vs flat.** That is a real discriminator, and it needs no reboot and no
kernel change — only `MPE_JACK_PERIODS`.

**n=2 has never been tested.** Every measurement doc is x3, x6 or x8.

## The decisive measurement: watch the fill level directly

Rather than inferring the level from xruns, **read it**. ALSA exposes it per-substream:

```
/proc/asound/card<N>/pcm0p/sub0/status     # state, hw_ptr, appl_ptr, avail
```

`appl_ptr - hw_ptr` **is** the fill level. Poll it at 10-20 Hz through a run and log it
alongside the xrun counter. The trace settles all four models on sight:

| trace shape | model |
|---|---|
| flat, with brief sub-ms dips, xruns anyway | **P3** — the counter is not reporting drain |
| sawtooth descending steadily, reset at each xrun | **P2** — constant clock mismatch |
| random walk wandering down to zero, no cadence | **P2'** — rate-matching noise |
| flat then one cliff to zero | **P1** — a real giant stall; capture what ran |

This is cheap, read-only, and it measures the quantity in question **directly** instead of
inferring it from a counter whose semantics we have never audited.

## Free, offline, do it first

**Audit what actually increments the xrun counter** — the harness, `mpe-xrun-probe`, and
which JACK/ALSA condition it reads. No Pi time, no measurement window. If the counter
includes anything other than a genuine playback underrun (delay reports, softmode events,
callback-overrun flags), **P3 is confirmed at zero cost** and a large part of this session's
data needs reinterpreting.

## Revised priority (post Step 4 amendment)

| # | test | cost | decides |
|---|---|---|---|
| **D+A** | DSP p99 **and** fill telemetry at 1024 / 512 / 256, identical load, cond A | ~20 min | compute-bound vs cushion models; flat fill + high p99 ⇒ P3 + graph overrun |
| **B** | nperiods sweep 2/3/4/6 at period 1024 | ~30 min | P2 vs P2′ vs P3 by curve |
| **—** | `1024×2` open-check | ~30 s | 21 ms off shipping latency if ALSA accepts |
| ~~4b~~ | irq/30 FF 90 | — | **parked** — producer lateness not binding term until D+A says otherwise |
| ~~alignment~~ | frame-phase tables | — | **unsupported, unpromising** — do not spend Pi time |

**Note on `n=2`:** it is both a diagnostic and, if it holds, **21.3 ms off the shipping
latency for free** — 64.0 ms to 42.7 ms with no change to the compute deadline. It may
simply refuse to open (`snd-usb-audio` conventionally wants >=3); that is a 30-second answer.

## What this retires

`irq/30` priority, `isolcpus`, `nohz_full`, PREEMPT_RT and the rest of the RT-tuning list all
target **producer lateness**. The arithmetic here says producer lateness of the magnitude we
can measure (sub-millisecond against a 42.7 ms cushion) **cannot** be what empties the
buffer. They are not wrong as engineering; they are aimed at the wrong term. Park them until
A/B/C say what the binding term actually is.
