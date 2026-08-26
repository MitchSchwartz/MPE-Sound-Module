# C0 — instrument conformance (2026-08-22)

**Branch:** `yolo/instrument-conformance-c0`  
**Gate:** `./scripts/instrument-conformance.sh`  
**Wall time (nerdrack):** recorded at end of this doc after test run.

---

## Finding (SR&ED)

**Instrument value and failure share a channel** — nine documented instances, one root
cause. Broken instruments exit 0 and produce plausible empty/zero readings. C0 adds a
mechanical gate so the pattern cannot recur without failing conformance first.

---

## Doctrine — six locations

| Location | Change |
|---|---|
| `docs/measurements/MEASUREMENT-DISCIPLINE.md` | Rule −1 + physics table |
| OM-Repo `.claude/skills/measurement-design/SKILL.md` | Step 0 gate, Step 8 prompt opener, 6 anti-patterns |
| `Documents/specs/low-latency-512-256-spec.md` | Conformance + Impossible if pre-registration |
| `Documents/specs/rerun-order-2026-08-19.md` | Conformance + Impossible if pre-registration |
| `AGENTS.md` | Doctrine block on self-test section |
| `Documents/PROGRESS.md` | Track A **HALTED**; standing rule 4 rewritten |

---

## Metric inventory → tests

| Metric | Positive | Negative | Physics |
|---|---|---|---|
| `dsp_median` | fixture parses 38.52 | `dsp_med=` halts; missing halts | <15% @ 512 with xruns>5 rejected |
| `xruns` | 2 from good log | missing field halts | paired with DSP above |
| `meter_live` | must be 1 | non-1 halts in physics | — |
| `samples` | 60 | mismatch vs expected halts | — |
| `window_align` | 1 on good fixture | absent on void run (harness) | probe PROBE_ACTIVE required |
| buffer halving DSP | 19→38 plausible | — | 39.6→1.6 rejected |

Implementation: `scripts/lib/measurement-result.sh`, `tests/test_instrument_conformance.sh`.

---

## Defect fixes

### dsp_med / dsp_median

Parser hard-errors on `dsp_med=`. Primary RESULT rows require `dsp_median=`, `dsp_p99=`,
`dsp_max=`. Rename without hard-error would hide the next silent parse failure.

### Sampler window alignment

`measure-latency-run.sh`: meter baseline captured **before** probe attach; sample loop
starts only after `PROBE_ACTIVE` in probe log. RESULT carries `window_align=1`. Jackd strict
restart remains **once per harness invocation**, not per probe window.

---

## V11 recovery (offline)

Re-parsed fixtures with `mpe_result_v11_recover`:

| Log | xruns column | DSP column |
|---|---|---|
| `good-512-a.log` | 2 | 38.520000 (not withheld) |
| `dsp-med-typo.log` | 23 | **withheld** (`dsp_withheld=1`) |

**Verdict:** xrun column stands where `meter_live=1` and fields parse. DSP column withheld
when `dsp_med` typo or missing median — matches PROGRESS.md V11 row.

---

## Track A

Queue remains **HALTED** until this gate is merged and green on the soak branch. Resume at
A1 (T14) after C0 pass.

---

## Test run

```
./scripts/instrument-conformance.sh → SENTINEL conformance-pass
conformance wall_time_s=6 (well under 15 min gate)
```


## Cycle 1 fixes (review-loop)

- `PROBE_ACTIVE` after `jack_activate`; harness waits before meter baseline
- `window_align=${_window_align}` computed, not literal
- `xrun-corr.sh` cats OUT to stdout (occurrence #1)
- `mpe_result_v11_recover`: per-row withhold, missing file halts
- DSP `n==0` path halts; `dsp_median=0` rejected
- `load_tag` parses primary xruns row first

Review artifacts: `Documents/reviews/review-loop-index-instrument-conformance-c0-2026-08-22.md`
