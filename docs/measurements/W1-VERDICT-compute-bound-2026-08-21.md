# W1 verdict — it was never the buffer. It is a 1.1 ms fixed cost per callback.

**2026-08-21.** Reading of `w1-instrumented-window-2026-08-21.md`.

## The finding

Journal during W1-c: `JackEngine::XRun: Surge XT was not finished`.
**Zero** `ALSA: xrun of at least N msecs` lines at **every** buffer size.

**Every xrun this project has measured is a graph overrun. The ring buffer never drained,
at any buffer size, ever.** Fill level sat flat at ~83% of buffer throughout.

## What this retires — permanently

All of the following targeted *getting audio out of the buffer on time*. The buffer was
always full. **They were aimed at a term that does not exist in this system.**

| line of work | status |
|---|---|
| the "~600 us unexplained" wakeup-path gap | **retired** — measured a real quantity that never mattered |
| cushion / drain model (`cushion-model-2026-08-21.md`) | **retired** — correct arithmetic, irrelevant premise |
| `threadirqs`, `irq/30` priority, IRQ placement | **retired** |
| `isolcpus`, `nohz_full`, PREEMPT_RT | **retired** |
| USB runway, URB depth, URB completion rate | already dead; now moot |
| frame alignment, aligned-period table | already dead; now moot |
| Scarlett vs Sound Blaster as a latency question | **moot** — neither device was ever the constraint |
| nperiods sweep W2 (`n` = 2/3/4/6) as a *diagnostic* | **retired** — cushion size cannot matter if it never drains |

**W2 survives only as a latency win, not as an experiment.** `1024 x 2` opens (W0 confirmed).
It cuts total latency 64.0 ms -> 42.7 ms with **no change to the compute deadline** — Surge
still gets 21.3 ms for 1024 frames. Worth taking on its own merits.

## The model

Converting `dsp_p99` to absolute time (cond A, 75-voice `midi-load`, n=1 per cell):

| buffer | deadline | dsp_p99 | time used |
|---|---|---|---|
| 1024 | 21.33 ms | 58.9% | 12.57 ms |
| 512 | 10.67 ms | 62.4% | 6.66 ms |
| 256 | 5.33 ms | 76.1% | 4.06 ms |

Least-squares fit of `T = a + b*N` over the three points:

> **a = 1.10 ms fixed per callback**
> **b = 0.0111 ms per frame**

Predicted vs measured: 12.51/12.57, 6.81/6.66, 3.96/4.06. **A fixed ~1.1 ms is paid on every
callback regardless of buffer size.**

| buffer | fixed cost as share of deadline |
|---|---|
| 1024 | 5.2% |
| 512 | 10.3% |
| 256 | **20.7%** |
| 128 | **41.4%** |
| 64 | **82.8%** |

## Independent validation — it retrodicts the T11 ladder

Extending the fit to buffers W1 never ran, against `t11-condA-ladder-2026-08-21.md`,
collected before this model existed:

| buffer | predicted total load | T11 measured xruns/min |
|---|---|---|
| 512 | 64% | 0.13 |
| 256 | 74% | 12.1 |
| 128 | **95%** | 677.6 |
| 64 | **136%** | 2776 |

**One fixed cost explains the whole ladder.** It also resolves the T11 anomaly that never
made sense — *"at 64 frames the callback never missed its deadline while 6% of periods
underran."* The callback never missed its **wakeup**; it missed its **computation**. Two
different quantities, exactly as `xrun-counter-audit-2026-08-21.md` warned.

**Caveat:** T11 ran cond A *without* `midi-load`, so `b` differs between the datasets. The
retrodiction is qualitative — the *shape* of the ladder, not its numbers.

## Two defects in W1 itself

**1. n=1 per cell.** The rate column shows 512 at 2/min *better* than 1024 at 6/min —
inverted, and certainly noise at a single draw. Per `MEASUREMENT-DISCIPLINE.md` that is a
rate claim on n=1 and does not support ordering.
**The decisive finding does not depend on it:** "zero ALSA lines across three cells" is a
presence/absence claim, and zero is zero.

**2. `dsp_p99` is the wrong statistic.** At 256, p99 = 76% while **0.28%** of periods
overran (32/min against ~11,280 periods/min). **The overruns live past p99.** We are watching
a percentile that by construction excludes the events we care about.

**Report `p99.9`, `p99.99` and `max` from here.** This is the same resolution error as the
10 Hz fill poller, one layer up: an instrument whose output looks authoritative and has the
answer removed by construction.

**3. Housekeeping:** the Pi came back with `-s softmode`, which changes xrun handling.
**Revert before any further counting.**

## Where the work goes now

The question is no longer "why is audio late out of the buffer." It is:

> **What is Surge doing for 1.1 ms on every callback, independent of block size?**

Candidates, none yet measured:

| candidate | why plausible |
|---|---|
| JACK graph traversal + inter-client context switches | per-cycle cost, independent of block size; this is what the **single-client architecture** idea (Surge hosting the looper in-process) attacks directly |
| JUCE wrapper per-block overhead | fixed setup/teardown per `processBlock` |
| MPE / MIDI event processing | per-callback event-queue walk |
| parameter smoothing + modulation matrix setup | Surge runs internal 32-sample blocks; some setup is per-callback |
| denormal handling, cache reload | fixed per-entry cost |

**If the 1.1 ms were eliminated, 256 would sit at ~55% of deadline** — comfortable. That
makes this worth real effort, and it is the first lever in this project that acts on the
actual binding term.

## Next

| # | cell | why |
|---|---|---|
| **V1** | Re-run the ladder with **p99.9 / p99.99 / max**, n >= 3 streams, strict mode | current statistic excludes the events of interest |
| **V2** | **Profile the callback** — where do the 1.1 ms go? `perf` on the Surge process, or in-callback timing around graph entry/exit | identifies the actual target |
| **V3** | `1024 x 2` as a shipping change (42.7 ms) | free latency win, independent of all of the above |

**Do not run V1 before fixing softmode**, and pre-register both V1 and V2 per
OM-Repo `.claude/skills/measurement-design`.

## Product position

**Shipping stays `1024 x 3` (64 ms) today**, with `1024 x 2` (42.7 ms) as an immediate
candidate pending a confirmation run.

Lower buffers are **not** blocked by the Pi's kernel, its USB stack, or the audio interface.
They are blocked by **~1.1 ms of fixed per-callback cost inside the audio graph.** That is a
software problem in our own stack, and it is tractable.
