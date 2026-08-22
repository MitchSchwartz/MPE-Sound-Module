# Agent prompt — Plan V: find the fixed per-callback cost

Copy everything below the line.

---

**Invoke the `measurement-design` skill before designing or altering any cell here.**

## What changed

W1 settled the question this project has been chasing all day. Journal during W1-c:
`JackEngine::XRun: Surge XT was not finished`, with **zero** `ALSA: xrun of at least N msecs`
lines at 1024, 512 **and** 256. Fill level flat at ~83% throughout.

**Every xrun ever measured on this appliance is a JACK graph overrun, not an ALSA underrun.
The ring buffer has never drained.**

**The binding term is compute time inside the audio callback.** Nothing else.

## Already dead — do not test, do not revisit

`lowlatency=N` · aligned periods (240/480/1008) · URB queue depth · URB completion rate ·
swap / SD major faults · frame alignment · Scarlett-vs-dongle as a *latency* question.

## Retired by W1 — do not resurrect

The "~600 us" wakeup-path gap · the cushion/drain model · `threadirqs` · `irq/30` priority ·
IRQ placement · `isolcpus` · `nohz_full` · PREEMPT_RT · the nperiods sweep **as a
diagnostic**.

All of these targeted getting audio *out of the buffer* on time. **The buffer was always
full.** They are not wrong engineering; they act on a term that does not exist here.

## Read first

| doc | why |
|---|---|
| `docs/measurements/W1-VERDICT-compute-bound-2026-08-21.md` | the finding, the model, and its weaknesses |
| `docs/measurements/PLAN-V-fixed-callback-cost.md` | this plan in full |
| `docs/measurements/xrun-counter-audit-2026-08-21.md` | what our instruments actually count |
| `docs/measurements/MEASUREMENT-DISCIPLINE.md` | pre-registration, claim classes, shortest-useful-version |

## The hypothesis — and why it must be verified before it is chased

Fitting `T = a + b*N` across W1's three cells gave **a = 1.10 ms fixed per callback**, which
retrodicts the entire T11 ladder.

**It is weak evidence:** a least-squares fit over **three points, n=1 each**, using
`dsp_p99` — a statistic W1 itself showed is wrong, because the overruns live past p99.7.
**1.1 ms is also suspiciously large** for JACK graph overhead, typically tens of microseconds
on ARM.

**Do not profile, refactor, or optimise anything until V1 confirms the fixed cost exists.**

---

## V0 — free pre-checks (read-only, before any window)

Two live confounds. Both are cheap and both may have contaminated **every prior measurement**.

| # | check | why it matters |
|---|---|---|
| V0-a | `MPE_CPU_GOVERNOR` in `/etc/mpe/mpe.env`, and the **actual** governor in `/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor` + current MHz | the example file has it **commented out**, so the box may be on stock `ondemand`. Our own comment says it *"can drop voices on polyphony spikes."* For a compute-bound problem this acts directly on the tail |
| V0-b | is `surge-poly-governor` active? `MPE_POLY_CEILING` / `MPE_POLY_FLOOR` / `MPE_POLY_EMERGENCY` values | it **dynamically reduces polyphony under CPU load**. If it ran during measurements, the load was **not constant across the window** — a confound possibly present in every cell to date |
| V0-c | is `-s softmode` still on jackd? | the Pi came back with it after the W1 restore; it changes xrun handling |

**Actions:** revert softmode. **Disable the poly governor and pin poly to a fixed value for
all V cells** — load must be constant and identical. Report the governor findings; **do not
change the CPU governor yet**, that is V5.

## V1 — silence test (~10 min): does the fixed cost exist?

Surge running, patch loaded, **zero notes playing**. With no voices, what remains is
essentially all fixed cost. **This measures `a` directly instead of inferring it.**

Measure **absolute callback time in milliseconds** (not just percent) at **1024 / 512 / 256**,
n >= 3 runs each.

| outcome | conclusion |
|---|---|
| **~1.1 ms, flat across all three buffers** | **confirmed** — a real per-callback constant. Proceed to V2 |
| **flat but much smaller (tens of us)** | the regression was noise. The real story is **per-voice cost scaling badly**, not a fixed constant. Re-aim at voice cost and say so |
| **not flat — scales with buffer** | **there is no fixed term.** The model is wrong. Say so plainly and stop |

State which row you landed in.

## V2 — client-count test (~10 min): engine cost or Surge cost?

Measure JACK's own DSP load with **no clients**, then with **Surge alone**. The difference
isolates graph traversal and inter-client context switching from Surge's internals.

**This is the direct test of the single-client-architecture idea** (Surge hosting the looper
in-process):

| result | consequence |
|---|---|
| graph traversal is a **meaningful share** of the fixed cost | the refactor is worth building |
| graph traversal is **~50 us** | **the refactor is dead** — a large piece of work avoided for 10 minutes |

## V3 — `1024 x 2` (~15 min): the free latency win, independent of everything above

W0 confirmed ALSA accepts `nperiods=2`. `1024 x 2` = **42.7 ms** against today's 64.0 ms — a
third off shipping latency with **no change to the compute deadline** (Surge still gets
21.3 ms for 1024 frames).

Confirm at **n >= 3 streams**, strict mode, fixed poly. Report xruns/min and DSP against the
`1024 x 3` baseline. **Run this regardless of how V1 and V2 turn out.**

## V5 — CPU governor (~15 min): a known lever that was never pulled

**This is not a new idea. It is a documented, prioritised lever that got lost.**
`docs/LATENCY-SPIKE.md` recorded the governor as **`ondemand`**, flagged it as *"a classic
cause of dropouts on transient polyphony spikes"*, and called the governor arm **"the most
promising, and the cheapest."** The mechanism was built (`14da2ca`) but **ships off by
default** and the example file has it commented out. Its sibling knob (Surge RT scheduling)
was completed — Surge runs FF 65 today. **The governor apparently was not.**

It is only *meaningful* now: while this looked like an IRQ/latency problem, clock speed was
irrelevant. For a compute-bound problem it is directly proportional, and `ondemand`'s ramp
acts precisely on the **tail**, which is where the overruns live.

If V0-a confirms `ondemand`, test `performance` as a **single-variable** change at
`256 x 3` — the cell with least headroom. Report DSP p99.9/max in **ms**, xruns/min, against
the current baseline.

**Mandatory safety check.** `LATENCY-SPIKE.md` records `get_throttled = 0x50000` on this
board: **under-voltage and throttling have both occurred historically.** `performance` raises
power draw. Record `vcgencmd measure_temp` **and** `vcgencmd get_throttled` before, during
and after. **If `get_throttled` becomes non-zero, stop and revert** — the measurement is
invalid and the board is the constraint.

### Overclocking — diagnostic only, and not in this pass

**Do not set `arm_freq`, `over_voltage` or `force_turbo`.**

Rationale, so it is not re-litigated: overclocking would be informative (a 20% clock bump
yielding ~20% less callback time confirms pure compute-bound and calibrates the prize), but

1. it **masks** the software problem — removing 1.1 ms of fixed cost recovers ~20% of the
   256 deadline permanently, on every unit, without heat or power margin;
2. this board has a **documented under-voltage history**, so it is the worst candidate for
   raising draw;
3. shipping an overclocked appliance is a product decision — enclosure thermals, PSU
   headroom, silicon variation across customer units, long-term reliability — **and it is
   Mitch's call, not a measurement outcome.**

Revisit only after V1/V2/V4 have said how much fixed cost is removable.

## V4 — profile (~30 min) — ONLY if V1 confirms

`perf` on the Surge process, or timestamps around `processBlock` entry/exit.

Surge XT processes internally in **32-sample blocks**, so anything per-*internal*-block scales
linearly with buffer size and **cannot** be the fixed term. The cost must be per
`processBlock` **call**. Candidates: JUCE wrapper setup/teardown, MPE/MIDI event-queue
walking, modulation-matrix or parameter-smoothing rebuild, denormal handling, cache reload.

Note: Surge's DSP is **single-threaded** and cannot be spread across cores. The useful
question is not "parallelise it" but **"what in the callback is not DSP and could be deferred
off the audio thread?"**

---

## Instrument requirements (all cells)

1. **Report DSP in absolute milliseconds as well as percent.** Percent-of-deadline hides that
   a fixed cost is constant.
2. **Report `p99.9`, `p99.99` and `max` — not `p99`.** At 256, p99 = 76% while **0.28%** of
   periods overran; p99 excludes the events of interest by construction.
3. **Capture the jackd journal per window** and report both `JackEngine::XRun` lines and any
   `ALSA: xrun of at least N msecs` lines. If ALSA lines ever appear, that is new and
   important — say so loudly.
4. **Stamp actual state into every result:** period, nperiods, rate, resolved card index,
   governor + current MHz, poly ceiling, softmode on/off, git SHA.

## Rules

1. **One variable per cell.** Write both configs side by side before calling it a comparison.
2. **Load must be identical and constant across cells** — fixed poly, poly governor disabled.
3. **No commands against the Pi while a window is open**, including read-only ones.
4. **Resolve the card index live** (it moved 6 -> 2 after the Step 3 reboot).
5. **Report n and the claim class it supports.** Shape claims need n >= 10 streams; W1's
   rate column was n=1 and its 512-beats-1024 ordering is noise.
6. **Ask the shortest useful version of each cell** and justify anything longer.
7. If a result refutes something above, **name the doc and say so plainly.**

## Deliverable

`docs/measurements/v1-fixed-cost-2026-08-21.md` on a branch off `dev`:

- V0 findings (governor, poly governor, softmode) and what you changed
- V1 table in **absolute ms**, and **which outcome row** you landed in
- V2 split: engine cost vs Surge cost, and the **explicit verdict on the single-client
  refactor**
- V3 result vs the `1024 x 3` baseline, with n stated
- V5 result if run, with thermals
- a **"what this retires"** section
- anything you could not measure, and why

**Do not propose work beyond what these results imply.** V4 is gated on V1; if V1 says there
is no fixed cost, say so and stop rather than looking for one anyway.
