---
name: measurement-design
description: Design or review a measurement on the MPE appliance (xrun runs, latency ladders, DSP load, soaks, cyclictest, IRQ census). Use before opening any Pi measurement window, before writing a measurement prompt for another agent, and when interpreting results. Enforces cheap-check-first ordering, per-cell pre-registration, and instrument audits.
---

# Measurement design (MPE appliance)

Pi time is expensive and Mitch has explicitly said he is tired of multi-hour tests followed
by "I made a design error." **The job here is to prevent that, not to be thorough.** More
cells is not more rigour.

Full reasoning: [`docs/measurements/MEASUREMENT-DISCIPLINE.md`](../../../docs/measurements/MEASUREMENT-DISCIPLINE.md).
Instrument self-test history: `AGENTS.md` -> "Self-test the instrument before it costs him anything".

## The failure this prevents

**An inference gets promoted to a premise without anyone paying to confirm it.** The
confirming check is nearly always an order of magnitude cheaper than the test that
eventually exposes the error.

## Step 1 — cheap check first (do not skip)

**Is there a free or offline check that could make this window unnecessary, or that its
interpretation depends on?** Do that first.

Free checks that have each invalidated hours of Pi time: reading the instrument's source;
`systemctl`/service survey; re-analysing existing logs per-stream; `git merge-base` on a
branch believed to contain a fix; arithmetic on the config (period rate, cushion size,
byte rate).

If such a check exists and has not been done, **stop and do it.**

## Step 2 — pre-register the cell

Write this into the measurement doc **before** running:

```
## Pre-registration
Question:       <the one thing this cell decides>
Claim class:    rate | shape | ranking
n:              <streams x runs>
Premises:       | premise | verified how | when |
Instruments:    <what each counts; when last audited>
Prediction:     <expected value, written before the run>
Falsifier:      <what result would make me abandon the hypothesis>
Cheaper check:  <what free check was considered, and why it is insufficient>
Shortest form:  <the shortest version of this test that would still change the decision>
Why not that:   <justification for anything longer — required if the two differ>
```

**Prediction and Falsifier are load-bearing.** If you cannot say what would surprise you,
the cell is not designed. A hypothesis with no disconfirming outcome is being illustrated,
not tested.

### Always ask: what is the shortest useful version of this test?

**The shortest version is not necessarily the right one — but it must be asked and answered,
and any gap between it and what you run must be justified in writing.** This is a standing
requirement from Mitch, added after a run of tests that were longer than their conclusions
needed.

Test bloat is the default failure: a window gets sized by habit rather than by what it has
to resolve. The 8-hour soaks were cut for exactly this reason and nothing was lost.

**Size the window from the expected event rate, not from convention.** To observe ~30 events:

| expected rate | window needed |
|---|---|
| 2776/min (64 frames) | **~1 second** |
| 112/min | ~15 s |
| 12/min | ~2.5 min |
| 0.13/min (512 cond A) | **~4 hours** — or change the metric, because this is not a rate you can measure in a window |

That last row is the important one: when the shortest useful version is implausibly long,
**that is a signal the metric is wrong for the question**, not a reason to run a soak. Look
for a metric with a higher event rate — fill level, DSP p99, magnitudes — or a comparison
that does not require counting rare events.

Ask the same of **n**: three streams cannot establish shape, but ten runs inside one stream
will not either. Spend the samples where the variance actually is.

## Step 3 — audit every instrument before it informs a decision

Two questions, both in writing, dated:

1. **What exactly increments it?** Read the source. *What reading would this produce if it
   were broken?* If that matches a healthy reading, it is not an instrument.
2. **Is its resolution sufficient?** A sampler below the Nyquist rate of its signal produces
   an authoritative-looking trace with the answer removed.

**Period rates on this box:** 47 Hz at 1024, 94 Hz at 512, 188 Hz at 256.

Known instrument facts (do not re-derive):

| instrument | what it actually is |
|---|---|
| `mpe-xrun-probe` xrun count | event count, **no magnitude**; conflates ALSA underruns with JACK graph overruns |
| probe `frames_late` | inter-callback jitter — **not** whether the graph finished computing |
| jackd journal `ALSA: xrun of at least N msecs` | genuine ALSA underruns **with magnitude**; free, post-hoc |
| `/proc/asound/card<N>/pcm0p/sub0/status` | `appl_ptr - hw_ptr` = fill level; **takes the substream lock** — perturbs the path it observes |

## Step 4 — check the observer effect

Any instrument that runs **during** a window is a perturbation until proven otherwise.

- No subprocess forks in polling loops (CPU doctrine).
- Pin off the audio cores (2-3) and off CPU0 (unmovable xhci IRQ) — use CPU1.
- `SCHED_OTHER`, `nice 19`.
- **Run a with/without control cell** and report it either way. "We checked and it was clean"
  is a finding.

Post-hoc capture (journal, log parsing) costs nothing during the window. Prefer it.

## Step 5 — claim class and n

| claim | needs |
|---|---|
| **rate** ("this config gives X/min") | a few runs in one stream |
| **shape** ("bimodal"/"unimodal") | **>= 10 streams** with `--restart-between` |
| **ranking** ("A beats B") | intervals reported, not just means |

Stream-start variance is large and real on this box
(`docs/measurements/stream-start-variance-2026-08-21.md`). Report **within-stream sd and
between-stream sd separately.** Small-n shape claims have already produced two contradictory
conclusions in one session.

## Step 6 — one variable, proven on paper

**Write both configs side by side and list every quantity that differs.** If more than one
differs, it is not a comparison. Remember that changing period changes the deadline, the
cushion, the USB frame alignment and the DSP efficiency simultaneously.

If a bundle is genuinely worth it (to save reboots), **say so in the doc**: a bundle cannot
attribute effect to cause.

## Step 7 — record actual state, not intended state

Results must never be attributable to a config they were not run under. Stamp into the result
file: period, nperiods, rate, device + **resolved card index** (it moves between boots),
IRQ priorities/affinities, module params, kernel cmdline, git SHA.

"I applied it earlier" is not a record.

## Step 8 — writing a prompt for another agent

Open with two lists, always:

- **Already dead — do not re-test.** Without this, refuted hypotheses return.
- **Parked, and why.**

Then: read-first docs, the pre-registration block, explicit interpretation branches ("state
which row you landed in"), and a **hard stop** before anything that changes config.

Standing constraints to restate in every prompt:
- Never run commands against the Pi while a window is open — including read-only ones.
- Resolve the card index live; never hardcode it.
- No forks in periodic loops.
- Report n, and the claim class it supports.

## Anti-patterns

| smell | why it is wrong |
|---|---|
| "run a longer soak to be sure" | duration rarely buys what design does; 8-hour soaks were cut for this reason |
| "more cells = more rigour" | Mitch pushed back on exactly this, correctly |
| "the metric went down, so it worked" | check n and claim class first — three streams cannot establish shape |
| "we applied that earlier" | verify at measurement time (Step 7) |
| "this is read-only so it is free" | reading `/proc/asound` takes a driver lock; SSH forks processes |
| a 60 s window because the last one was 60 s | size it from the expected event rate (Step 2) |
| a soak to measure a 0.13/min rate | the metric is wrong for the question — find one with a higher event rate |
