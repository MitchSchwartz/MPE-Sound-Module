# MPE measurement skill

Use when planning, executing, reviewing, or writing prompts for Pi measurements on
MPE-Module.

Read first: `docs/measurements/MEASUREMENT-DISCIPLINE.md`, `AGENTS.md` (self-test section),
`docs/measurements/README.md`.

---

## Step 0 — Instrument conformance (before any cheap check)

**Hard gate.** Run offline before Pi time or human involvement:

```bash
./scripts/instrument-conformance.sh
```

Must finish in **≤ 15 minutes**. Exit non-zero blocks the measurement.

This step outranks every “quick sanity check” — a skipped gate is not a gate.

---

## Step 1 — Cheap checks (only after Step 0 passes)

- `python3 -m unittest discover -s tests -q` (includes conformance tests)
- Confirm branch, clean tree, `MPE_PEAK_METER=1` if xruns are in scope
- Read back JACK period from `/proc`, not from the argument (trap 5)

---

## Step 2 — Pre-register

Copy the pre-registration block from `MEASUREMENT-DISCIPLINE.md`:

- **Conformance:** gate command and parser requirements
- **Impossible if:** physics assertions for this run

Both lines are mandatory in the work order or prompt.

---

## Step 3 — Drive synthetically first

Before Mitch or a shipping claim: produce non-zero output from the instrument path.
Count what you discard.

---

## Step 4 — Execute on Pi (if required)

Use `mpe` subcommands where they exist. Announce blocks > 15 min (Rule 7).

---

## Step 5 — Parse with strict library

Never hand-roll `grep` for RESULT fields. Use `scripts/lib/measurement-result.sh`:

- Field name **`dsp_median`** — `dsp_med` is a hard error (rename alone hides the next bug)
- Missing fields halt; no silent defaults

---

## Step 6 — Physics check before writing conclusions

Run `mpe_result_physics_assert` on aggregated rows. Firing assertion → instrument or
model bug; do not weaken thresholds.

---

## Step 7 — Record

One file per run under `docs/measurements/`. Provenance, commit, traps avoided, confidence
labels.

---

## Step 8 — Prompts for other agents

Every YOLO or delegation prompt **must open** with:

1. **Conformance gate:** `./scripts/instrument-conformance.sh` exit 0 before Pi/human steps
2. **Impossible if:** at least one physics line specific to the metrics being read

Example opener:

```
Conformance: ./scripts/instrument-conformance.sh must pass before any Pi step.
Impossible if: dsp_median < 15 with xruns > 5 at 512×3; samples != 60 per window.
```

---

## Anti-patterns (instant P0)

| # | Pattern | Fix |
|---|---|---|
| 1 | `\|\| echo 0` on a measurement read | Halt; use `mpe_meter_xruns_read` |
| 2 | `continue` after `set-surge-audio.sh` failure | Halt; assert period from JACK |
| 3 | `unknown` / `?` in a RESULT field treated as data | Halt or omit field with ERROR |
| 4 | Instrument writes to a file; harness reads stdout | Single channel; assert non-empty output |
| 5 | Positive control checks presence only (`grep RESULT`) | Assert value correctness (count, age, period) |
| 6 | Lowering a physics threshold because real data tripped it | Fix instrument or model; never weaken |

---

## Nine occurrences (reference)

| # | Instrument | Failure | Looked like |
|---|---|---|---|
| 1 | `xrun-corr.sh` | writes `~/xrun-corr.out`, not stdout | exit 0, empty redirect |
| 2 | `set-surge-audio.sh` | no sudo → env fail → continues | run labelled 512 at 1024 |
| 3 | latency tap v1 | hooked `_send`; pads use raw client | n=0 |
| 4 | latency tap v2 | paired only with `/hit` | n=0 |
| 5 | cyclictest wrapper | usage text logged as data | exit 0 |
| 6 | `JournalXrunCounter` | journal without xrun lines | 0 xruns |
| 7 | watchdog `XrunCounter` | tail missing file | 0 xruns |
| 8 | jq snapshot | `.stale // true` | false stale |
| 9 | `measure-latency-run.sh` (was) | `\|\| echo 0` on meter | RESULT xruns=0 |
