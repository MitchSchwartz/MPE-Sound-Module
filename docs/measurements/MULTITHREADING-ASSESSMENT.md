# Multithreading Surge — what it would actually involve

**2026-08-22.** Written because multi-core is now the **only remaining lever with a multiple
in it**, after the unison theory was retracted (`PATCH-COST-what-makes-them-heavy.md`).

**This is an assessment, not a proposal.** Recommendation is at the end.

## The prize

Surge computes every voice on **one thread**. Measured: **~57% of one core** at Cloud Horn's
5-voice ceiling. The Pi 4 has **four Cortex-A72 cores**, so we are using roughly **14% of the
machine**.

Parallelism is bounded by `min(cores, voices)`. With 3 worker cores and enough voices to fill
them, polyphony scales roughly **3x**:

| patch | now | with 3 worker cores |
|---|---|---|
| Crystals | 3 voices | **~7-9** |
| Cloud Horn | 5 voices | **~12-15** |

**No other lever comes close.** Overclock is 11-19%; `-mcpu` is 5-15%. This is a multiple.

## Where the parallelism would go

| level | granularity | fit for our patches |
|---|---|---|
| **Voice-level** — split the active voice list across worker threads | coarse | **Best fit.** Voices are independent until the final sum |
| **Oscillator-level** — split a voice's oscillators | fine | **Useful here**: Crystals has 3 oscillators/voice, so it helps even at 1-2 voices, where voice-level parallelism cannot |
| Scene-level (A/B) | coarse | **No use** — every heavy patch is `scenemode=0`, scene A only |
| FX-level | per-block | **No use** — the entire fixed patch cost is ~6% |

**Voice-level alone does not help at low voice counts**, which is exactly where we are. A
serious implementation would need **both** voice- and oscillator-level splitting to move
Crystals from 3 voices upward.

## What makes it hard

**1. Real-time safety.** No mutexes, no condition variables, no allocation, nothing that can
block inside the audio callback. Work distribution has to be lock-free — a pool of
pre-spawned threads with atomic work claiming and a spin-wait barrier. Spin-waiting burns CPU,
though here that is affordable: the cores are idle anyway.

**2. Thread priority and placement.** Workers must run `SCHED_FIFO` at the audio thread's
priority, pinned to cores nothing else uses. Get this wrong and you get **priority inversion
that is worse than single-threaded.** Our cores 2-3 host jackd and Surge; cores 0-1 carry the
unmovable xhci IRQ and the relocated IRQ set. **There is no clean set of three free cores
today** — the core allocation would need rethinking alongside this.

**3. Shared mutable state — the real work.** Voices are conceptually independent but share
implementation details: scratch buffers for oscillator output, the modulation matrix, and
scene-level state. **Per-thread scratch would have to be introduced throughout the voice
path.** This is not a wrapper around a loop; it is a change to how Surge holds working memory.

**4. Barrier cost.** Favourable, and worth stating: at 1024 frames the block is 21.3 ms and a
barrier costs tens of microseconds — **well under 1%.** Even at 256 frames (5.3 ms) it stays
small. **Sync overhead is not the blocker.**

**5. Determinism.** Voices sum, so ordering does not change output *if* they are truly
independent. Shared modulation sources and any voice-stealing interaction with
`enforcePolyphonyLimit()` need checking — the same code path implicated in the pops.

**6. It is upstream C++.** Surge XT is a large codebase. This means either a **maintained fork**
(ongoing merge cost forever) or **upstreaming** (maintainer buy-in, review cycles, months).

**One point in our favour:** we run `surge-xt-cli` **standalone**, not as a plugin. Plugin
hosts often forbid or complicate plugin-spawned threads; standalone has no such contract.

## Effort, honestly

| phase | scope |
|---|---|
| Investigation | Read Surge's voice-processing path; identify every shared buffer and mutable structure. **Days.** |
| Prototype | Thread pool, lock-free claim, per-thread scratch, voice-level split. **1-2 weeks focused C++.** |
| Correctness | Denormals, modulation, voice stealing, MPE per-voice state, patch loading during play. **The long tail.** |
| Oscillator-level split | Needed for low voice counts. **Additional.** |
| Placement | Rework core allocation across the whole appliance. |
| Maintenance | Fork forever, or upstream. |

**This is a multi-week project with real risk of being slower than single-threaded if the
RT-safety or placement work is imperfect.**

## Cheaper things that are not this

- **Two Surge instances** — helps layered/split setups; **does nothing for one patch's
  polyphony.** Not applicable.
- **JACK parallel clients** — JACK2 already runs *independent* clients in parallel. Ours are
  serial (Surge -> looper) and Surge is one client. No gain.
- **Check upstream first** — has voice-level threading been discussed or attempted in Surge XT?
  If prior art or a maintainer opinion exists, that is free to find and changes the estimate
  substantially. **Do this before anything else.**

## Recommendation

**Do not start this now.**

It is the only lever with a multiple, and it is also **the most expensive and riskiest item on
the entire list** — by a wide margin. Everything else worth doing is minutes-to-hours:

1. Ship `1024 x 2` — **21 ms off latency, already measured, free**
2. Governor fade, then re-enable — makes overload graceful
3. Overclock diagnostic — 11-19%, 15 minutes
4. `-mcpu=cortex-a72` rebuild — 5-15%, ~30 minutes
5. Patch census across the library — free, decides whether Crystals-class is rare or common

**Do 1-5 first.** They are plausibly ~30% combined plus a shipped latency win, for a day's
work rather than a month's.

**Then decide multithreading against a clear product question:** *is a 3-voice ceiling on
Plaits-stack pads acceptable for this instrument?* If most of the library is comfortable —
which the census will show — this is an edge case to document, not a month of C++.

**The value of writing this down is that "the CPU is maxed out" stays false.** It is not
maxed; it is single-threaded. That is a software fact with a known, priced remedy that we are
choosing not to spend yet.
