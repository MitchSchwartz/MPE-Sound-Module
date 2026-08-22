# Plan V — find and remove the fixed per-callback cost

**2026-08-21, after W1.** Supersedes `PLAN-2026-08-21-evening.md`, whose Phases B/C were
retired by `W1-VERDICT-compute-bound-2026-08-21.md`.

## Where we are

W1 established, with zero ambiguity, that **every xrun ever measured on this appliance is a
JACK graph overrun, not an ALSA underrun**: `JackEngine::XRun: Surge XT was not finished`,
and **zero** `ALSA: xrun of at least N msecs` lines at 1024, 512 and 256. Buffer fill sat
flat at ~83% throughout. **The ring buffer has never drained.**

Everything aimed at the buffer-drain path is therefore retired (see the verdict doc): the
~600 us gap, the cushion model, `threadirqs`, `irq/30` priority, `isolcpus`, `nohz_full`,
PREEMPT_RT, USB runway, URB depth and rate, frame alignment, and the Scarlett-vs-dongle
comparison as a latency question.

**The binding term is compute time inside the audio callback.**

## The hypothesis under test — and its weakness

Fitting `T = a + b*N` across W1's three cells gave **a = 1.10 ms fixed per callback**, which
retrodicts the whole T11 ladder including why 64 frames was catastrophic.

**But it is weak evidence.** It is a least-squares fit over **three points, n=1 each**, using
`dsp_p99` — a statistic W1 itself showed is the wrong one, since the overruns live past
p99.7. And **1.1 ms is suspiciously large** for JACK graph overhead, which is typically tens
of microseconds on ARM.

**So V1 measures the fixed cost directly rather than inferring it.** Do not begin profiling
or refactoring until it is confirmed to exist.

## V1 — silence test (~10 min): does the fixed cost exist?

Surge running, patch loaded, **zero notes playing**. With no voices, what remains is
essentially all fixed cost. Measure absolute callback time at 1024 / 512 / 256.

| outcome | reading |
|---|---|
| **~1.1 ms, flat across all three buffers** | **confirmed.** A real per-callback constant; proceed to V2 |
| **flat but much smaller (tens of us)** | the regression was noise. The real story is that **per-voice cost scales badly**, not that there is a fixed constant. Re-aim at voice cost |
| **not flat — scales with buffer** | there is no fixed term at all; the model is wrong. Say so and stop |

This is the shortest useful version of the question and it needs no fitting.

## V2 — client-count test (~10 min): engine cost or Surge cost?

Measure JACK's own DSP load with **no clients**, then with **Surge alone**. The difference
isolates graph traversal and inter-client context switching from Surge's internals.

**This is also the direct test of the single-client architecture idea** (Surge hosting the
looper in-process, raised earlier in the project):

| result | consequence |
|---|---|
| graph traversal is a **meaningful share** of the fixed cost | the single-client refactor is worth building |
| graph traversal is **~50 us** | **the refactor is dead** — a large piece of work avoided for 10 minutes of measurement |

## V3 — `1024 x 2` (~15 min): the free latency win

W0 confirmed ALSA accepts `nperiods=2` on this device. `1024 x 2` = **42.7 ms** total against
today's 64.0 ms — a **third off shipping latency** with **no change to the compute deadline**
(Surge still gets 21.3 ms for 1024 frames, exactly as now).

**Independent of V1/V2.** It does not depend on understanding or fixing the fixed cost.

Confirm at **n >= 3 streams**, strict mode, then it is a config change.

## V4 — profile (only if V1 confirms)

`perf` on the Surge process, or timestamps around `processBlock` entry/exit.

Surge XT processes internally in **32-sample blocks**, so anything per-*internal*-block scales
linearly with buffer size and **cannot** be the fixed term. The cost must be per
`processBlock` **call**. Candidates: JUCE wrapper setup/teardown, MPE/MIDI event-queue
walking, modulation-matrix or parameter-smoothing rebuild, denormal handling, cache reload.

## Instrument changes required

**Stop reporting `dsp_p99` as the primary statistic.** At 256, p99 = 76% while **0.28%** of
periods overran — the failures are past p99.7, which p99 excludes by construction.

**Report `p99.9`, `p99.99` and `max`,** and report DSP in **absolute milliseconds** as well as
percent, since percent-of-deadline hides that the fixed cost is constant.

**Revert `-s softmode`** before any counting run; the Pi came back with it after the W1
restore.

## Order and cost

| # | cell | Pi time | gates |
|---|---|---|---|
| **V1** | silence test, 1024/512/256 | ~10 min | V2 and V4 |
| **V2** | client-count test | ~10 min | the single-client refactor |
| **V3** | `1024 x 2` at n >= 3 | ~15 min | independent — run regardless |
| **V4** | profile the callback | ~30 min | only if V1 confirms |

**~35 minutes decides whether a refactor is worth building and whether we can ship a third
off our latency.**

## Product position

Shipping stays **`1024 x 3` (64 ms)**; **`1024 x 2` (42.7 ms)** is a candidate pending V3.

Lower buffers are **not** limited by the Pi's kernel, its USB stack, or the audio interface.
They are limited by **compute time inside our own audio graph** — a software problem in code
we control.
