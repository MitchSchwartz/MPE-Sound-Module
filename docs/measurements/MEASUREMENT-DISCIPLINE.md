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
Prediction:        <expected value, written down before the run>
Falsifier:         <what result would make me abandon the hypothesis>
Cheaper check:     <what free/offline check was considered and why it is insufficient>
```

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

1. Cheap check before expensive window. Always.
2. Write the prediction and the falsifier before the run.
3. Audit the instrument before its first decision.
4. Harness records actual state, not intended state.
5. Declare claim class; shape needs n >= 10.
6. Write both configs side by side before calling it one variable.
7. Say when something is a bundle.
8. List what is dead at the top of every prompt.
