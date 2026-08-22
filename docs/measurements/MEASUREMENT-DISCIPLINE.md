# Measurement discipline

*Last updated: 2026-08-22 (America/Toronto)*

Rules for every measurement on the MPE appliance. Read before writing a harness, a
prompt, or a work order that records numbers.

---

## Rule −1 — Instrument conformance (outranks everything below)

**Doctrine:** Every instrument returns its **value** and its **failure** through the
**same channel**. A broken instrument is indistinguishable from a working one at the
reading site. Blindness follows — not from nine separate bugs, but from one missing
convention.

This is the most expensive pattern in the project. Nine documented instances, five months
of void runs, 382 pad presses producing `n=0`, a run labelled 512 that executed at 1024.
See the inventory in [`PROMPT-C0-instrument-conformance.md`](PROMPT-C0-instrument-conformance.md).

### Four required mechanisms

Every metric an instrument emits must have all four before a human reads a table:

| # | Mechanism | What it rejects |
|---|---|---|
| 1 | **No in-band failures** | `\|\| echo 0`, `unknown`, `continue-on-error`, empty-string defaults — all become **halts** with non-zero exit |
| 2 | **Positive control** | Asserts the reading is **right**, not merely present (e.g. meter age ≤ 3 s, sample count = expected, JACK period read back from `/proc`) |
| 3 | ** negative control** | Deliberately breaks the instrument; harness **must halt** (missing `meter.state`, stale timestamp, wrong field name) |
| 4 | **Physics assertions** | Reject impossible results in-harness before aggregation (see below) |

**Hard gate:** `scripts/instrument-conformance.sh` must pass (≤ 15 min, offline) before
any Pi measurement involving Mitch or a shipping claim. A gate that is slow gets skipped;
a skipped gate is not a gate.

**Do not weaken an assertion to make a test pass.** If a physics assertion fires on real
data, either the instrument is wrong or the physics model is wrong — both are findings.
Neither is a reason to lower the threshold.

### Physics assertions (examples that would have caught void runs)

| Observation | Why it is impossible |
|---|---|
| DSP 39.6% → 1.6% when halving buffer only | Per-callback overhead **doubles** when period halves; DSP cannot collapse |
| DSP 10% with 23 xruns in 60 s at 512×3 | Xruns imply the graph is under stress; idle baseline is ~19% at 1024, ~39% at 512 — not 10% |
| `n=0` latency samples after 382 pad presses | Instrument path is wrong, not “no latency” |
| `meter_live=1` but `meter_max_age_s` > 3 | Freshness positive control failed |

Implementations live in `scripts/lib/measurement-result.sh` and
`tests/test_instrument_conformance.sh`.

---

## Rule 0 — Label confidence

Every claim is **measured** / **experiment** / **guess**. Never present the three in the
same voice.

---

## Rule 1 — Verify on the device

Passing unit tests did not stop two xrun counters from reading dead sources for months. A
fix is not done until observed on `raspberrypi2` with real output pasted into the PR.

---

## Rule 2 — Fail loud (superseded in detail by Rule −1)

Failure paths return `None` or raise. Never `0`, `""`, `False`, or a default that reads
the same whether the source is live or dead. Rule −1 adds the conformance gate that
enforces this mechanically.

---

## Rule 3 — No forks in periodic loops

See `Documents/DECISIONS.md` § *2026-08-18 — CPU is the scarcest resource*.

---

## Rule 4 — Do not withdraw a conclusion silently

If a prior finding is wrong, say so explicitly in the doc where the original claim lives.

---

## Rule 5 — Bisect before you grid

Run the cheapest test that could falsify the hypothesis. State the decisive comparison
first.

---

## Rule 6 — Certification comes last

Long soaks prove a configuration is sound. Do not soak before the configuration is decided.

---

## Rule 7 — Announce the block

Before anything over ~15 minutes, say which task it is and expected runtime.

---

## Rule 8 — One variable per measured comparison

Changing two knobs voids the experiment, not just the code path.

---

## Pre-registration block (copy into every measurement prompt)

```
Conformance: scripts/instrument-conformance.sh must exit 0 before this run starts.
             Every RESULT field parsed via measurement-result.sh (missing field = halt).

Impossible if: halving buffer drops DSP by >50% without condition change;
              dsp_median < 15 with xruns > 5 at 512×3;
              samples != SECONDS_PER_RUN;
              jitter_n < 100 when SECONDS_PER_RUN >= 30;
              any metric reads 0/empty/unknown without a prior ERROR halt.
```

---

## SR&ED note

Instrument-conformance work is eligible measurement-system development. The documented
finding — *value and failure share a channel* — with nine instances is a genuine R&D
outcome, not lost calendar time.
