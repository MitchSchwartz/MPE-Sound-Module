# Measurement discipline — how to stop re-learning the same lesson

Written 2026-08-21 after a session in which **every** wasted measurement traced to one move.

## The pattern

**An inference gets promoted to a premise without anyone paying to confirm it.**

The confirming check is almost always **cheaper than the test that eventually exposes the
error** — usually by an order of magnitude.

| assumed | cost to check | cost of not checking |
|---|---|---|
| the xrun counter means "the buffer emptied" | 5 min, offline | most of a session's interpretation |
| E1 was reverted on `plan/t7-sequence` | one `git merge-base` | a test run on the wrong config |
| 192 vs 256 isolates frame alignment | re-read T13 | one full confounded run (T12) |
| Scarlett unimodal ⇒ adaptive clock lock | note that n=3 | a conclusion withdrawn hours later |
| the HDMI cmdline change applied | one post-reboot check | still partial; found by accident |
| 10 Hz resolves the fill trace | period-rate arithmetic | a trace that shows nothing, convincingly |

The last one was written *while* documenting the others. **Naming the pattern does not stop
it. A mechanism does.**

**There is a second pattern, and it is worse — see Rule -1.** It has ten occurrences to this
one's six, and unlike this one it does not announce itself: a promoted inference eventually
contradicts something, but an instrument that reads clean while blind never does.

## Rule -1 — an instrument must never be able to fail silently

**Numbered below Rule 0 because it outranks it.** This is the most expensive pattern in the
project's history: **ten documented occurrences**, more than every other failure mode combined.

### The single root cause

**Every instrument here returns its value and its failure through the same channel.** At the
reading site there is no way to distinguish *"here is a measurement"* from *"I could not
measure."* A broken instrument and a working one are indistinguishable, so the failure arrives
as a **result** instead of as an **error** — and gets believed, written up, and acted on.

That is not ten bugs. It is one missing convention, replicated everywhere because nothing
enforced it.

| date | instrument | returned | should have returned |
|---|---|---|---|
| 08-19 | `xrun-corr.sh` | exit 0, empty file (12 runs) | write failure |
| 08-19 | `set-surge-audio.sh` | continued without `sudo` — a run labelled 512 ran at 1024 | hard stop |
| 08-19 | latency tap v1 | `n=0` after 267 presses (wrong code path) | no-events error |
| 08-19 | latency tap v2 | `n=0` after 115 presses | no-events error |
| 08-21 | V8-b auto-pick | a plausible patch name — the wrong one | selection failure |
| 08-21 | `mpe-peak-meter` shutdown | looked stopped; wasn't | shutdown failure |
| 08-22 | V10-b ramp probe | `0` xruns, via `\|\| start=0` swallowing a blind meter | blind-meter error |
| 08-22 | census `unison_voices` | a plausible integer (summed engine selectors) | unsupported-field error |
| 08-22 | V11 `dsp_med` | `unknown`, plus idle readings presented as measurements | field + alignment error |
| 08-22 | reference-suite TSV `printf` | 14 format specifiers for 15 args — `$log` column silently dropped (#104) | format/arg mismatch error |

**The V11 case is the clearest.** `dsp_med` read ~1% at 256x3 across three unrelated patches,
including a cell with 23 xruns. A cell missing its deadline is at ~100% by definition. The
number was not merely wrong, it was **arithmetically impossible** — and it still reached a
results table, because nothing in the path was obliged to notice.

### The five mechanisms — all of them, not a menu

**1. No in-band failures. Anywhere.**
Kill every `|| x=0` default, every `unknown` string, every "continue on error". A missing or
invalid reading **halts the cell** and says which instrument and why. A default value is a
lie with a number attached.

**2. Positive control — force a known answer, assert the reading matches.**
Not *"did it return something"* — **"did it return the right something."** Force an overrun and
assert the counter moves. Run a known load and assert DSP lands in the expected band. V11's
0.9% would have failed this instantly.

**3. Negative control — break it deliberately, assert the harness halts.**
Kill the meter, stale the state file, rename the field. If the harness still prints a number,
the instrument is not trustworthy no matter what it reads. **All ten failures above would have
been caught by this one check.**

**4. Physics assertions on results, automatic and in-harness.**
Not something a human notices at review time:
- DSP% must not *fall* when the buffer halves (deadline halves, cost barely moves).
- A cell reporting xruns cannot report low DSP.
- Parts must sum to the whole; counts must be monotone where the physics is monotone.

A result violating arithmetic is **rejected by the harness**, not published for someone to
catch later.

**5. A long-running measurement must write a terminal sentinel on every exit path.**
Not only on success. Otherwise **"no result yet" and "died" share a channel** — for anything
longer than the poll interval, that is the same defect as a blind counter wearing a different
mask, and it is not caught by controls on the instrument, because the *instrument* is fine.
The **run** died.

**The tenth occurrence, 2026-08-22:** the Gate 1 soak log
(`~/instrument-soak-1024x2.log`) is **253 bytes — header only**, four hours after
`SENTINEL soak-start`. `measure-soak-instrument.sh` appends a `SOAK minute=N` line every 60 s,
so ~240 lines should exist. It died during setup, between the start sentinel and the loop:
`systemctl restart mpe-jackd`, `mpe_wait_for_jack_server`, `systemctl restart surge-xt-cli`,
and `load-patch-osc.py` all run under `set -e` **with no guard**, and every failure path writes
to stderr or nowhere — never to the log. Under `trap _cleanup EXIT` the process kills its load
and exits **silently**. The resulting file is byte-identical to a healthy run whose first
minute has not yet elapsed. **Gate 1 was never certified, and nothing said so.**

Requirements:
- Track a stage marker through setup; on EXIT, if the success sentinel was never written,
  append `SENTINEL <name>-aborted stage=<X> rc=<n>` **to the log**.
- **Route stderr into the log**, not only the console — nobody is watching a terminal at 03:00.
- Emit a sentinel on **entering** the measurement loop, so setup failure and loop failure are
  distinguishable at a glance.
- A reader must be able to tell **running / completed / aborted** from the artifact alone,
  without inspecting process state.

### Why this works — it is already proven

**V11's xrun column is trustworthy and its DSP column is not, and the only difference is that
A0 ran a positive control on the xrun path that morning.** Five minutes of forced-overrun check
is the entire reason half that run survived. The mechanism is not theoretical; it needs
generalising to every metric, not inventing.

### Standing requirement

**No suite runs until an instrument conformance pass has run in the same session and passed.**
Positive and negative control for every metric the suite will emit. It replaces the ad-hoc
per-prompt "check the instrument" step, which depended on whoever wrote the prompt remembering.

This matters most at a **platform change**: a new kernel, a new JACK, a new IRQ topology are
exactly the conditions that break instruments silently — and on new hardware there is no
baseline to catch the impossible reading against.

---

## Rule 0 — cheap-first, always

**Before opening any measurement window, ask: is there a free or offline check that could
make this window unnecessary?**

This session's four highest-value findings all came from free checks, not from Pi time:
the counter audit (offline), the service survey (3 min), the per-run stream re-analysis
(existing data), the branch containment check (`git merge-base`).

If an offline check exists and has not been done, **do it first.** Not as a courtesy — the
expensive test keeps discovering what the cheap check would have.

## Rule 0.5 — pilot the test before you run it at length

**Conformance (Rule -1) asks "is the instrument trustworthy?" This asks a different question:
"will this specific test, as designed, produce output I can interpret?"** A conformant
instrument wired into a badly-shaped run still yields nothing.

**Never run a measurement at full length before running it once at minimum length and reading
the output.**

### The rule

Before any run longer than ~5 minutes:

1. Run **one cell, shortest possible window, n=1.**
2. **Read the actual output** — every field the full run will report.
3. Confirm each field is **present, numeric, and physically plausible**.
4. Only then scale to full length.

Step 3 is the whole rule. **Exit code 0 is not the check.** Every silent-instrument failure in
this project exited 0.

### What it costs and what it buys

V11 ran 24.5 minutes and produced a DSP column that was unusable. **A single 2-minute cell,
read before scaling, would have shown `dsp_med=unknown` immediately** — the parser bug was
visible in the first cell's output. Cost: 2 minutes. Saved: 24.5 minutes and a
re-run.

The same is true of most of the nine: the failure was visible in the **first** cell of every
one of them. Nobody looked before the run had finished.

### Pilot every *new* thing, not every run

A pilot is required when anything is new or changed:

- a new harness or plan script
- a new metric, field, or parser
- a changed instrument, or a changed platform (**mandatory on the Pi 5**)
- a new patch, buffer config, or voice count never measured before
- **any run following a fix** — the fix is the new thing

A re-run of an unchanged cell on an unchanged platform does not need one.

### What the pilot must show

| check | fails if |
|---|---|
| every reported field present | any `unknown`, `?`, `n/a`, or missing key |
| every field numeric and in range | a percentage outside 0-100, a negative count |
| physically plausible | idle DSP under load; xruns with low DSP; a value identical across unrelated cells |
| the window actually opened | no `PROBE_ACTIVE` / alignment signal |
| n as expected | sample count far below the window x rate |

**Any failure stops the scale-up.** Fix, re-pilot, then run.

### Size the window for over-dispersion, not just for rate

**Measured 2026-08-23** (`X1-RESULT-burstiness-2026-08-23.md`): xruns on this appliance are
**not Poisson**. From the B2 soak, minutes 1-15:

| statistic | value | Poisson |
|---|---|---|
| mean | 3.87/min | — |
| **Fano factor** (var/mean) | **4.32** | 1.00 |
| silent minutes | **5 of 15 (33%)** | 2% |

**A third of all minutes are completely silent at a mean of nearly four per minute.** Events
arrive in bursts separated by real quiet stretches.

**Consequences, all load-bearing:**

1. **Effective sample size is roughly `n / Fano`.** A window must be **~4x longer** than Poisson
   arithmetic suggests. ~30 effective events at 3.87/min needs ~130 raw events ~ **33 minutes**.
   This is the real basis for the 30-minute minimum.
2. **A short-window zero is not evidence of clean.** `0/0/0` over 3 x 25 s is consistent with any
   true rate from 0 to several per minute. Every short-window "clean" claim in this project's
   history is downgraded to *"no events observed in N seconds."*
3. **Short windows are screening; long windows certify.** For event counts, there is no third
   option.
4. **Use a continuous metric in short windows.** This is the "metric is wrong for the question"
   rule with teeth: `dsp_max` / headroom has no burst structure and is informative in 25 s.
   **Screen on DSP; certify on a long-window count.**
5. **Report the Fano factor whenever a rate is claimed.** A bare rate implies Poisson, and here
   that implication is false by more than 4x.

**Do not compute confidence from Poisson assumptions on this appliance without checking
dispersion first.** A Poisson estimate in the X1 prompt put P(three silent 25 s windows) at ~8%;
the empirical silent-minute fraction alone is 33%, and clustering pushes consecutive-window
silence higher still. The wrong model produced a wrong conclusion about whether an instrument was
defective — it was not.

### Hard limit — anything over 30 minutes needs Mitch's explicit approval

**Standing instruction, 2026-08-23.** Any measurement window longer than **30 minutes** requires
his approval *before* it runs, with a written justification stating: the expected event rate, how
many events the conclusion needs, and **why a shorter window cannot answer the question.**

**This exists because the rule above was not applied to the B2 soak.** At the observed ~2
xruns/min, **one hour yields ~200 events** — a rate estimate good to ~7%, more than enough to
answer "does 1024x2 hold." The 8-hour run bought one additional fact (the rate is non-stationary: minutes 1-15 average
**3.87/min** and decay toward ~1.8/min by hour 4), most of which is visible inside the first 30
minutes. *(An earlier version of this paragraph said the rate "peaks ~3.5/min near minute 14" —
that was a **cumulative average** misread as an instantaneous rate. The curve is front-loaded,
not ramping. Corrected 2026-08-23.)* Defensible **once**, as first characterisation of a rate nobody had measured.
Indefensible as a routine gate — and nobody did the event-rate arithmetic before spending the
8 hours.

Re-certification after a config change: **30 minutes.** First characterisation of an unknown
rate: **60 minutes**, and say so.

### Applies to harness changes too

A harness edit is not verified by the harness running. **Pilot it against a cell whose answer
is already known** and confirm it reproduces it. A "fix" that changes a known-good number is a
regression, and that is only visible if the pilot targets known ground.

---

## Rule 1 — pre-register every cell

Fill this in **before** running. It is short on purpose; a checklist nobody completes is
worthless.

```
## Pre-registration
Question:          <the one thing this cell decides>
Claim class:       rate | shape | ranking
n:                 <streams x runs>   (shape claims need n >= 10 streams)
Premises:          <what must be true for the result to mean anything>
                   | premise | verified how | when |
Instruments:       <what each one actually counts, and when that was last audited>
Conformance:       <positive + negative control run THIS session? PASS/FAIL per metric — required>
Impossible if:     <what reading would be arithmetically impossible; assert it in the harness>
Pilot:             <one cell at minimum length run and output READ? PASS/FAIL — required if anything is new>
Prediction:        <expected value, written down before the run>
Falsifier:         <what result would make me abandon the hypothesis>
Cheaper check:     <what free/offline check was considered and why it is insufficient>
Shortest form:     <shortest version that would still change the decision>
Why not that:      <justification for running anything longer>
```

**Always ask what the shortest useful version of this test is.** The shortest is not
necessarily optimal — but it must be asked, answered, and any gap justified in writing.
Test bloat is the default: windows get sized by habit, not by what they must resolve.

Size the window from the **expected event rate**, not convention. To see ~30 events at
2776/min takes ~1 second; at 112/min ~15 s; at 12/min ~2.5 min; **at 0.13/min ~4 hours.**
When the shortest useful version comes out implausibly long, that is evidence **the metric is
wrong for the question** — not a reason to run a soak. Switch to a metric with a higher event
rate (fill level, DSP p99, ALSA magnitudes) or to a comparison that does not require counting
rare events.

**"Conformance" is a hard gate** — a cell whose instruments have not passed positive and
negative control **this session** does not run. See Rule -1.

**"Prediction" is the load-bearing line.** If you cannot say what would surprise you, the
cell is not designed yet. **"Falsifier" is second** — a hypothesis with no disconfirming
outcome is not being tested, it is being illustrated.

## Rule 2 — audit an instrument before its first decision, not after a surprise

Any metric used to make a decision needs a **written statement of exactly what increments
it**, dated. One-time cost per instrument; it never has to be paid again.

Apply the [measurement-integrity](../../MEMORY.md) test to each:

> **What reading would this instrument produce if it were broken?**
> If that is the same as a healthy reading, it is not an instrument.

Also check its **resolution against the thing it measures**. A sampler below the Nyquist
rate of its signal produces a confident, empty trace. Period rates: 47 Hz at 1024, 94 Hz at
512, 188 Hz at 256.

## Rule 3 — the harness records actual state, never intended state

A result must never be attributable to a configuration it was not run under.

The harness should stamp into every result file: period, nperiods, sample rate, device and
**resolved card index**, IRQ priorities and affinities, relevant module parameters, kernel
cmdline, and git SHA. `boot-assert-cmdline.sh` already does this for cmdline — **generalise
it.**

"I applied it earlier" is not a record. E1 and the partial HDMI disable were both this.

## Rule 4 — declare the claim class, and respect n

| claim | needs |
|---|---|
| **rate** ("this config gives X/min") | a few runs within one stream |
| **shape** ("bimodal", "unimodal") | **>= 10 streams**, with `--restart-between` |
| **ranking** ("A beats B") | overlapping intervals reported, not just means |

Three streams cannot establish shape. Step 1 claimed unimodal and Step 4 claimed bimodal,
both on small n, and the two claims fought each other for no reason.

**Report within-stream sd and between-stream sd separately.** Stream-start variance is a
real, large effect on this box (`stream-start-variance-2026-08-21.md`).

## Rule 5 — one variable, and prove it on paper first

Before running a comparison, **write out every quantity that differs between the two cells.**
If more than one differs, it is not a comparison.

T12 changed period size *and* alignment while explicitly testing alignment — designed by the
same person enforcing the one-variable rule on everyone else. Writing the two configs side
by side would have caught it in thirty seconds.

## Rule 6 — a bundle is not an experiment

Multiple simultaneous changes are sometimes worth it to save reboots. That is fine, **as long
as the doc says so**: a bundle cannot attribute effect to cause. Attribution has to come from
the clean single-variable cells that justified each element.

Step 3 was labelled this way deliberately. Keep doing that.

## Rule 7 — retire dead lines loudly, in one place

Every prompt should open with **"already dead — do not re-test"** and **"parked, and why."**
Without it, refuted hypotheses come back: `lowlatency=N` was demoted, resurrected on a new
mechanism, then killed properly; the aligned-period table was closed, withdrawn, and had to
be re-argued.

## The half-page version

0. Ask the shortest useful version of the test. Justify anything longer.
1. Cheap check before expensive window. Always.
2. Write the prediction and the falsifier before the run.
3. Audit the instrument before its first decision.
4. Harness records actual state, not intended state.
5. Declare claim class; shape needs n >= 10.
6. Write both configs side by side before calling it one variable.
7. Say when something is a bundle.
8. List what is dead at the top of every prompt.

---

## Relationship to AGENTS.md

`AGENTS.md` has carried **"Self-test the instrument before it costs him anything"** since
2026-08-19, written after 382 pad taps produced zero samples. That rule catches an instrument
returning **nothing**.

**We fell into the trap again on 2026-08-21 anyway**, because the new failures were the other
half: instruments returning **confident, plausible numbers that meant something else.** The
xrun counter self-tested clean and passed every existing check — it simply counted a
different thing. A 10-20 Hz fill poller would have produced a smooth, legible trace with the
answer aliased out.

That is why Rule 2 above adds **semantics** and **resolution** to the existing checks, rather
than restating them. The two documents are one doctrine: AGENTS.md holds the short form at
the point of use; this file holds the reasoning and the per-cell mechanics.
