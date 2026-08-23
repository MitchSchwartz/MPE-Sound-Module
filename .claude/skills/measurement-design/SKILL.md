---
name: measurement-design
description: Design or review a measurement on the MPE appliance (xrun runs, latency ladders, DSP load, soaks, cyclictest, IRQ census). Use before opening any Pi measurement window, before writing a measurement prompt for another agent, and when interpreting results. Enforces conformance-tested instruments, cheap-check-first ordering, per-cell pre-registration, and physics assertions on results.
---

# Measurement design (MPE appliance)

Pi time is expensive and Mitch has explicitly said he is tired of multi-hour tests followed
by "I made a design error." **The job here is to prevent that, not to be thorough.** More
cells is not more rigour.

Full reasoning: [`docs/measurements/MEASUREMENT-DISCIPLINE.md`](../../../docs/measurements/MEASUREMENT-DISCIPLINE.md).
Instrument self-test history: `AGENTS.md` -> "Self-test the instrument before it costs him anything".

## The two failures this prevents

**A. An inference gets promoted to a premise** without anyone paying to confirm it. The
confirming check is nearly always an order of magnitude cheaper than the test that eventually
exposes the error. Six occurrences.

**B. An instrument reads clean while blind.** **Ten occurrences — the most expensive pattern
in this project's history.** See Step 0; it outranks everything else here.

## Step 0 — the instrument and its failure must not share a channel

**Do this before anything else. A suite that completes on a blind instrument is worse than one
that halts, because it produces confident wrong numbers instead of no numbers.**

### The root cause, stated once

Every instrument on this appliance returns its value and its failure **through the same
channel**. At the reading site you cannot distinguish *"here is a measurement"* from *"I could
not measure."* So the failure arrives as a **result**, not an error — and gets believed.

Nine instances: `xrun-corr.sh` (empty file, exit 0), `set-surge-audio.sh` (a run labelled 512
that ran at 1024), latency taps v1 and v2 (`n=0` after 382 pad taps), V8-b auto-pick (plausible
wrong patch), peak-meter shutdown (looked stopped), V10-b ramp probe (`|| start=0` swallowing a
blind meter), census `unison_voices` (summed engine selectors into a plausible integer), V11
`dsp_med` (`unknown` plus idle readings — including ~1% DSP in a cell with 23 xruns, which is
arithmetically impossible).

**Do not treat these as nine bugs to avoid individually. It is one missing convention.**

### The five mechanisms — required, not optional

1. **No in-band failures.** No `|| x=0`, no `unknown`, no continue-on-error. Invalid or missing
   **halts the cell** naming the instrument. A default value is a lie with a number attached.
2. **Positive control.** Force a known condition, assert the reading *matches*. Not "did it
   return something" — "did it return the **right** something."
3. **Negative control.** Break it deliberately — kill the meter, stale the state file, rename
   the field — and assert the harness **halts**. All nine failures would have been caught here.
4. **Physics assertions in the harness.** DSP% must not fall when the buffer halves. A cell with
   xruns cannot report low DSP. Parts sum to the whole. **The harness rejects impossible
   results**; a human noticing at review time is not a mechanism.
5. **A terminal sentinel on every exit path** for anything long-running. Not only on success —
   otherwise **"no result yet" and "died" share a channel**. A reader must tell running /
   completed / aborted from the artifact alone. Track a stage marker, write
   `SENTINEL <name>-aborted stage=... rc=...` from the EXIT trap, route stderr into the log,
   and emit a sentinel on entering the loop. *(Occurrence ten: the Gate 1 soak log, 253 bytes,
   header only, four hours in — setup died under `set -e` and every failure path wrote to
   stderr or nowhere.)*

### The standing requirement

**No suite runs until an instrument conformance pass has run in this session and passed** —
positive and negative control for every metric the suite will emit.

**This is proven, not theoretical.** V11's xrun column is trustworthy and its DSP column is not,
and the only difference is that a positive control ran on the xrun path that morning. Five
minutes saved half the run.

Strongest at a **platform change** (new kernel, new JACK, new IRQ topology): those break
instruments silently, and on new hardware there is no baseline to catch an impossible reading
against.

## Step 1 — cheap check first (do not skip)

**Is there a free or offline check that could make this window unnecessary, or that its
interpretation depends on?** Do that first.

Free checks that have each invalidated hours of Pi time: reading the instrument's source;
`systemctl`/service survey; re-analysing existing logs per-stream; `git merge-base` on a
branch believed to contain a fix; arithmetic on the config (period rate, cushion size,
byte rate).

If such a check exists and has not been done, **stop and do it.**

## Step 1.5 — pilot before you run at length

**Rule -1/Step 0 asks whether the instrument is trustworthy. This asks whether *this test, as
designed*, will produce interpretable output.** A conformant instrument in a badly-shaped run
still yields nothing.

**Never run a measurement at full length before running one cell at minimum length and reading
the output.**

1. One cell, shortest window, n=1.
2. **Read every field the full run will report.**
3. Confirm each is present, numeric, and physically plausible.
4. Only then scale.

**Step 3 is the rule. Exit code 0 is not the check** — every silent-instrument failure in this
project exited 0.

**V11 ran 24.5 min and produced an unusable DSP column. A 2-minute pilot would have shown
`dsp_med=unknown` in the first cell.** The same was true of all nine failures: visible in cell
one, noticed only after the run finished.

**Any window over 30 minutes requires Mitch's explicit prior approval**, with the expected event
rate, the events the conclusion needs, and why a shorter window cannot answer it. The B2 soak ran
8 h to establish a ~2/min rate that one hour measures to ~7% — the event-rate arithmetic was never
done. Re-certification after a config change: 30 min. First characterisation of an unknown rate:
60 min.

**Pilot whenever something is new or changed** — new harness, new metric or field, changed
instrument, changed platform (**mandatory on the Pi 5**), a config never measured before, or
**any run following a fix**. An unchanged cell on an unchanged platform does not need one.

**Harness changes get piloted against a cell whose answer is already known**, to confirm they
reproduce it. A fix that moves a known-good number is a regression, visible only if the pilot
targets known ground.

## Step 2 — pre-register the cell

Write this into the measurement doc **before** running:

```
## Pre-registration
Question:       <the one thing this cell decides>
Claim class:    rate | shape | ranking
n:              <streams x runs>
Premises:       | premise | verified how | when |
Instruments:    <what each counts; when last audited>
Conformance:    <positive + negative control run this session? PASS/FAIL per metric — required>
Impossible if:  <what reading would be arithmetically impossible; assert it in the harness>
Pilot:          <one cell at minimum length run and output READ? PASS/FAIL — required if anything is new>
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

**Check dispersion before trusting any event-rate arithmetic.** Xruns here are **not Poisson** —
measured Fano factor **4.32**, with **33% of minutes silent at a 3.87/min mean**
(`X1-RESULT-burstiness-2026-08-23.md`). Effective sample size is ~`n / Fano`, so event-count
windows need to be **~4x longer** than Poisson math suggests. A short-window zero is **not**
evidence of clean — screen on a continuous metric (`dsp_max` / headroom), certify on a long
window. Report the Fano factor whenever you claim a rate.

Ask the same of **n**: three streams cannot establish shape, but ten runs inside one stream
will not either. Spend the samples where the variance actually is.

## Step 3 — audit every instrument before it informs a decision

Three questions, all in writing, dated. Step 0 is the mechanism; this is the reasoning behind it.

1. **What exactly increments it?** Read the source. *What reading would this produce if it
   were broken?* If that matches a healthy reading, it is not an instrument.
2. **Is its resolution sufficient?** A sampler below the Nyquist rate of its signal produces
   an authoritative-looking trace with the answer removed.
3. **Is the sample window aligned to the load window?** V11's DSP read idle values because the
   sampler was not provably measuring while the load was running. **A correct instrument
   sampling at the wrong time is indistinguishable from a broken one.**

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
(`docs/measurements/archive/stream-start-variance-2026-08-21.md`). Report **within-stream sd and
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
- **Open with an instrument conformance gate** (Step 0) — positive and negative control for
  every metric the prompt will report. **Halt the whole run on failure**, do not degrade.
- **State what reading would be impossible** for each metric, and assert it in the harness.
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
| running the full plan first, reading output after | pilot one cell and read it (Step 1.5); V11 cost 24.5 min for a 2-min lesson |
| "it exited 0, so it worked" | every silent-instrument failure here exited 0 |
| testing a fix by running the full suite | pilot against a cell whose answer is already known |
| `\|\| value=0` / `// 0` / `except: pass` on a reading | **in-band failure** — the instrument can now report blindness as data (Step 0) |
| "unknown" / "n/a" / empty in a results field | same defect wearing a different mask; halt instead |
| a number that looks plausible, accepted because it looks plausible | plausibility is what all nine failures had in common |
| "the instrument worked last week" | a new kernel/JACK/build changes this; conformance is per-session |
| a result that violates arithmetic, explained rather than rejected | check the instrument **first**; V11's 1% DSP with 23 xruns was impossible, not interesting |

---

## After the session (labour evidence)

Invoke **`sred-daily-capture`** (`.claude/skills/sred-daily-capture/SKILL.md`) and append to [`docs/SRED-DAILY-LOG.md`](../../docs/SRED-DAILY-LOG.md). Conditions before the run; labour after — same session, not Friday.
