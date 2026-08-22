# C0 — instrument conformance gate

**Hand to a fresh agent.** Self-contained. **Must complete in ≤ 15 minutes** (offline).

**Conformance:** `./scripts/instrument-conformance.sh` must exit 0 before Track A (A1+) or
any Pi measurement affecting the shipping claim.

**Impossible if:** halving buffer drops `dsp_median` by >50% without condition change;
`dsp_median` < 15 with xruns > 5 at 512×3; `samples` != window length; any metric is 0 /
empty / unknown without a prior ERROR halt.

---

## Why this exists

Nine occurrences, one root cause: **every instrument returns value and failure through the
same channel**, so a broken one is indistinguishable from a working one at the reading
site. AGENTS.md lists four from 2026-08-19 (382 pad presses, n=0; 512 labelled at 1024).
T2/T6 add five more. Five months of void runs.

Four mechanisms (all required): no in-band failures; positive control; negative control;
physics assertions in-harness.

**Do not weaken an assertion to make a test pass.**

---

## Doctrine lives in six places (do not skip any)

| # | Location |
|---|---|
| 1 | `docs/measurements/MEASUREMENT-DISCIPLINE.md` — Rule −1 |
| 2 | `.claude/skills/mpe-measurement/SKILL.md` — Step 0 + six anti-patterns |
| 3 | Pre-registration in `Documents/specs/low-latency-512-256-spec.md` |
| 4 | Pre-registration in `Documents/specs/rerun-order-2026-08-19.md` |
| 5 | `AGENTS.md` — doctrine on self-test section |
| 6 | `Documents/PROGRESS.md` — standing rule 4 + Track A HALTED banner |

Skill Step 8: every prompt for another agent opens with Conformance + Impossible if lines.

---

## Task 1 — Inventory metrics → three tests each

For every metric the harnesses emit, implement in `tests/test_instrument_conformance.sh`:

| Source | Metrics |
|---|---|
| `measure-latency-run.sh` | `xruns`, `meter_live`, `meter_max_age_s`, `dsp_median`, `dsp_p99`, `dsp_max`, `jitter_n`, `jitter_*`, `frames_late_*`, `samples` |
| `xrun-corr.sh` | per-second `dsp%`, `peak`, `xrun`; `TOTAL` line |
| `measure-soak.sh` | `xruns_total`, `invalid_windows` |
| `bench-xruns.sh` | per-buffer xrun count |

Per metric:

1. **Positive control** — fixture with known-good value parses correctly
2. **Negative control** — broken fixture halts (missing file, stale meter, wrong field)
3. **Physics assertion** — impossible combo rejected (see MEASUREMENT-DISCIPLINE.md)

Parser: `scripts/lib/measurement-result.sh` — **hard error on `dsp_med`**; require
`dsp_median`.

---

## Task 2 — Known defects in scope

### dsp_med / dsp_median

Rename alone hides the next bug. Parser must:

- Accept only `dsp_median=`
- **Halt** on `dsp_med=` with explicit ERROR
- Halt on missing `dsp_median` when parsing a primary RESULT row

### Sampler window alignment

V10-b suspect: per-probe activity before meter baseline misaligns the 60 s window.

Fix in `measure-latency-run.sh`:

- Capture meter baseline **before** starting xrun probe
- Wait for `PROBE_START` in probe log before sample loop
- Emit `window_align=1` on RESULT when alignment checks pass

Do **not** restart jackd per probe window — strict restart once per harness invocation only.

---

## Task 3 — Gate script

`scripts/instrument-conformance.sh`:

- Runs `tests/test_instrument_conformance.sh`
- Runs `tests/test_meter_harness.sh` (offline meter fixtures)
- Runs unit tests touching measurement parsers
- Prints `SENTINEL conformance-pass` on success
- **Wall clock ≤ 15 min** on nerdrack (document actual time in deliverable)

---

## Task 4 — V11 recovery (offline)

Attempt to re-parse existing V11-class logs with strict parser:

- **xrun column:** keep if `meter_live=1` and fields parse
- **DSP column:** withhold where `dsp_med` was used or field missing
- Record in `docs/measurements/instrument-conformance-c0-2026-08-22.md`

Use fixtures under `tests/fixtures/instrument-conformance/` if Pi logs unavailable.

---

## Deliverable

- All six doctrine locations updated
- `scripts/lib/measurement-result.sh` + conformance tests
- `scripts/instrument-conformance.sh`
- Harness fixes (dsp field, window alignment)
- `docs/measurements/instrument-conformance-c0-2026-08-22.md` — inventory, test matrix, V11
  recovery outcome, wall time
- `./scripts/instrument-conformance.sh` exit 0

---

## Explicit non-goals

- No Pi soak until C0 passes on `dev`
- No weakening physics thresholds
- No Track A tasks (A1+) until PROGRESS HALTED banner cleared

---

## After C0

Run `/review-loop` scope `instrument-conformance-c0`. Then enqueue A1.
