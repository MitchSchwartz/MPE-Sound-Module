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

**There is a second pattern, and it is worse — see Rule -1.** It has nine occurrences to this
one's six, and unlike this one it does not announce itself: a promoted inference eventually
contradicts something, but an instrument that reads clean while blind never does.

## Rule -1 — an instrument must never be able to fail silently

**Numbered below Rule 0 because it outranks it.** This is the most expensive pattern in the
project's history: **nine documented occurrences**, more than every other failure mode combined.

### The single root cause

**Every instrument here returns its value and its failure through the same channel.** At the
reading site there is no way to distinguish *"here is a measurement"* from *"I could not
measure."* A broken instrument and a working one are indistinguishable, so the failure arrives
as a **result** instead of as an **error** — and gets believed, written up, and acted on.

That is not nine bugs. It is one missing convention, replicated everywhere because nothing
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

**The V11 case is the clearest.** `dsp_med` read ~1% at 256x3 across three unrelated patches,
including a cell with 23 xruns. A cell missing its deadline is at ~100% by definition. The
number was not merely wrong, it was **arithmetically impossible** — and it still reached a
results table, because nothing in the path was obliged to notice.

### The four mechanisms — all of them, not a menu

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
the instrument is not trustworthy no matter what it reads. **All nine failures above would have
been caught by this one check.**

**4. Physics assertions on results, automatic and in-harness.**
Not something a human notices at review time:
- DSP% must not *fall* when the buffer halves (deadline halves, cost barely moves).
- A cell reporting xruns cannot report low DSP.
- Parts must sum to the whole; counts must be monotone where the physics is monotone.

A result violating arithmetic is **rejected by the harness**, not published for someone to
catch later.

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
