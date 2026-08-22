# Are we at the CPU's limit? — the assumption stack, and every lever

**2026-08-22.** Written because "the Pi is maxed out" was about to become a fact without
anyone auditing what it rests on.

## The short answer

**No, and not close.**

We are running **one core of four**, at **83% of maximum clock**, on patches where **a single
voice costs roughly eight times a normal voice**, with a patch-cost-reduction mechanism that
exists in the repo and is **empty**.

The defensible statement is much narrower:

> *A single Surge instance, single-threaded, on one Cortex-A72 at 1.8 GHz, cannot compute more
> than 3-5 voices of these particular heavy patches inside a 21.3 ms deadline.*

**Every qualifier in that sentence is a lever.**

## What is actually established

- **Every xrun is a JACK graph overrun** — `Surge XT was not finished`. Zero ALSA underruns
  at any buffer size, ever. The audio buffer has never run dry. (`W1-VERDICT`)
- **JACK graph overhead is ~35 us** — noise. The cost is **Surge's own DSP**. (`V1-VERDICT`)
- **There is no fixed per-callback cost** — it scales with buffer. (`V1-VERDICT`)
- **Crystals: 3 voices @ 1024. Cloud Horn: 5 voices @ 1024.** Both confirmed over 60 s.
  (`V9`)

That much is solid. **The leap from there to "the CPU is maxed" is not.**

## The arithmetic that points at the patch, not the chip

Cloud Horn: 5 voices = ~57% DSP; silence = ~6%. So ~51% for 5 voices:

- **~10% of the deadline per voice** -> **~2.1 ms per voice**
- At 1.8 GHz: **~3.8 M cycles per voice per 1024-sample block**
- **= ~3,700 cycles per sample, per voice**

A straightforward synth voice costs a few hundred cycles per sample. **We are roughly an
order of magnitude above that.**

That is the signature of **heavy unison**: 8 unison copies x ~450 cycles each lands almost
exactly here. **"One voice" in these patches is probably eight oscillators wearing a trench
coat.** That is not a maxed CPU; it is one note costing eight notes of work.

**Corollary from the same numbers:** silence is only **~6%**, and silence includes the
patch's effects, which run per-block rather than per-voice. **So FX are not the problem —
the entire fixed cost of the patch is 6%.** Cutting reverb will not help. It is all in the
voices.

## The assumption stack

| # | assumption behind "maxed out" | status |
|---|---|---|
| A1 | Surge can only use one core | **True — but that is a software limit, not hardware.** Three cores sit idle |
| A2 | The build is optimal for this chip | **Mostly.** NEON yes, `-O3` yes; **`-mcpu=cortex-a72` absent** |
| A3 | The clock is maxed | **No.** 1800 MHz; ceiling is 2147 |
| A4 | Nothing else competes for the audio cores | **Partly.** The governor was unpinned; systemd's global `CPUAffinity` is still unset |
| A5 | The patch costs what it costs | **Never examined.** `patch_normalization.json` is `{}`; nothing in the codebase references unison |
| A6 | 48 kHz is required | Assumed, never questioned |

---

## The levers, explained

### 1. Unison — the patch lever *(biggest, cheapest to try)*

**What it is.** Surge's unison setting makes one key press spawn **N slightly detuned copies**
of the oscillator, which is what produces a wide, thick sound. Cost scales roughly **linearly
with N**: unison 8 means a single note does eight oscillators' worth of work.

**Why we think it is the issue.** ~3,700 cycles/sample/voice is ~8x a plain voice — the exact
shape unison produces.

**Size:** potentially **2-8x** on heavy patches. Halving unison roughly halves per-voice cost,
which roughly doubles polyphony.

**Cost:** low. `config/patch_normalization.json` already exists as a load-time rewrite hook
and is currently `{}`. Nothing in the codebase touches unison today.

**The catch:** unison is a large part of what these patches *sound* like. Thinner and narrower
is a **musical** tradeoff, not an engineering one — **Mitch's call, and audible immediately.**

### 2. Multi-core — the architecture lever *(biggest theoretical, most expensive)*

**What it is.** Surge computes every voice on a single thread. The Pi has **four cores**, and
we are using **~57% of one**. That is roughly **14% of the machine**.

**Why it is not free.** Plugins are single-threaded by convention — the *host* parallelises
across plugins, not within one. Surge would have to split voice processing across threads
internally. That is upstream C++ work with real risk (RT-safe synchronisation inside an audio
callback), not a config change.

**Size:** up to ~3x theoretical. **Cost:** high. **Not a near-term option, but it is the
reason "maxed out" is false.**

### 3. Overclock — the clock lever

**What it is.** Run the CPU faster. Currently **1800 MHz** via `arm_boost=1` (Raspberry Pi's
own validated setting, same as the Pi 400). Firmware ceiling for `arm_freq` is **2147 MHz**.

**Size:** 1800 -> 2147 is **~19%**. Real, but it does not change the *shape* of anything — a
3-voice patch stays a 3-voice patch.

**Its real value is calibration.** If a 19% clock bump yields ~19% more headroom, that
confirms cleanly that we are purely compute-bound and prices every future percent of
optimisation.

**The shipping gate is thermal, not performance.** For an instrument, **a clock that
occasionally throttles is worse than a lower clock that never does** — throttling is a sudden
step *down* mid-performance, exactly the tail excursion that causes `Surge XT was not
finished`. Clearing it needs a sustained soak in the real enclosure at worst-case ambient,
ending at `throttled=0x0`. **Testing it is cheap; shipping it is a separate decision.**

### 4. `-mcpu=cortex-a72` — the build lever

**What it is.** The build is `-DCMAKE_BUILD_TYPE=Release` and nothing else
(`docs/SURGE_ARM_BUILD.md`). Telling the compiler the exact chip lets it schedule instructions
for the A72's specific pipeline rather than a generic aarch64 target.

**What it is *not*:** an earlier draft of this analysis suspected missing NEON. **That was
wrong** — aarch64 mandates NEON, Surge maps its SSE intrinsics onto it through simde, and
CMake Release implies `-O3`. The SIMD path is already there.

**Size:** realistically **5-15%**. **Cost:** low — rebuild and measure.

### 5. CPU affinity hygiene — the contention lever

**What it is.** systemd's global `CPUAffinity` is unset, so **every** service defaults to all
four cores; only `mpe-jackd` and `surge-xt-cli` declare `2 3`. The poly governor was found
floating onto the audio cores; others likely still are.

**Size:** low single digits — they are `SCHED_OTHER` and cannot preempt the audio threads, but
they pollute cache. **Cost:** low, though a global default constrains the touch UI too and
needs its own responsiveness check.

### 6. Sample rate — the workload lever

**What it is.** 44.1 kHz instead of 48 kHz is **~8% fewer samples** to compute for the same
wall-clock time.

**Size:** ~8%. **Cost:** low technically; has implications for anything expecting 48 kHz.
Listed for completeness, not recommended.

### 7. Oscillator and filter choice — the other patch lever

**What it is.** Surge oscillator types differ enormously in cost (wavetable and string models
far exceed a plain saw), as do filter types. Same musical-tradeoff character as unison, but
less mechanical to adjust.

**Size:** patch-dependent. **Cost:** requires per-patch judgement.

---

## Review — V9-c is not worth finishing

`scripts/measure-plan-v9c.sh` exists on `docs/v8-patch-capacity` and was **not in the V9
prompt** — it was added by the agent. Three cells:

| cell | what | verdict |
|---|---|---|
| **V9-c1** | Cloud Horn @ 7 x 60 s, "V8-b regression" | **Drop.** V9-a already established the knee is ~5-6. Re-confirming that 7 overruns documents a number we no longer use |
| **V9-c2** | Closed Hat binary ceiling search | **Drop — the metric does not apply.** Mitch: *"hat is a short oneshot, it has no sustain like most others."* Sustained-voice capacity is undefined for a one-shot. This cell would produce an authoritative number that means nothing |
| **V9-c3** | Crystals ramp @ 512x3 | **Defer.** Mildly interesting, but 512 is not a shipping candidate while heavy patches do 3 voices at 1024 |

**V9-d (multi-patch `1024x2` confirmation) is worth keeping** — it is the last check before
shipping the latency win.

**The general point:** V9-c is methodology cleanup on measurements we have decided not to act
on. **Reconciling V8's ramp numbers matters only if we intend to use them, and we do not** —
the two anchors we need (Crystals 3, Cloud Horn 5) are confirmed at 60 s.

## The plan

### Now — ship what is already won

| # | action | cost |
|---|---|---|
| **P1** | **V9-d**: `1024 x 2` vs `x3` at 2-3 more patches at verified-clean counts | ~15 min |
| **P2** | If clean, **make `1024 x 2` the default** — 64.0 ms -> **42.7 ms** | config |
| **P3** | **Governor: implement the fade**, then re-enable and ear-test | code |

P3 matters because overload will always be reachable; the question is whether it degrades
audibly. Fade is the fix (`V7-capacity-curve-plan.md`); the steal *policy* is secondary.

### Next — the only lever with an order of magnitude in it

| # | action | cost |
|---|---|---|
| **P4** | **Read the unison setting** on Crystals, Cloud Horn, Bowed Strings, Analog Foundation | free — patch files |
| **P5** | Halve unison on one heavy patch; measure **CPU** and **listen** | ~15 min + ears |
| **P6** | If the tradeoff is acceptable, populate `patch_normalization.json` | code |

**P4 is free and answers the central question of this document.** If unison is 8 or 16, the
cycle arithmetic is confirmed and P5 is obvious. If it is 1-2, the ~3,700 cycles/sample come
from somewhere else and we look there instead.

### Then — the cheap percentages, in order

| # | action | size | cost |
|---|---|---|---|
| **P7** | Overclock **2000/2147 MHz as a diagnostic** — does 19% clock give 19% headroom? | 19% | ~15 min |
| **P8** | Rebuild with `-mcpu=cortex-a72`, measure | 5-15% | ~30 min |
| **P9** | Global `CPUAffinity=0 1` default + touch-UI responsiveness check | low | ~20 min |

**P7 and P8 stack multiplicatively with P5** and with each other. Together they are plausibly
**~30%** — worth having, but they do not move a 3-voice patch into comfort on their own.
**Only the patch lever does that.**

### Separately — a real bug, not a capacity question

**Attenborough kicks do not play at any setting** (Mitch). That is a **different failure** —
not xruns, not capacity. Worth one look, tracked apart from all of the above.

### Not planned

Multi-core Surge (P-none) — the largest theoretical lever, but upstream C++ work with RT-safety
risk. **Named here so "maxed out" is never claimed again**, not proposed as work.

## What would make "maxed out" true

All of the following, none of which currently hold:

1. Surge parallelised across all four cores, **and**
2. the clock at 2147 MHz sustained without throttling, **and**
3. patches at minimum viable unison, **and**
4. the build tuned for A72, **and**
5. nothing else on the audio cores.

**Until then the honest phrasing is "this configuration is at its limit", never "the
processor is."**
