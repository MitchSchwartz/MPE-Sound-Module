# A2 pass 1 re-validation — stock binary control

*Validated: 2026-08-22 (America/Toronto)*

## Purpose

Rule 0 cheap check: re-run `mpe_result_load_tag` + `mpe_result_physics_assert` on A2
pass 1 cell logs **offline** against parser commit `a1e80e3` (fail-closed
`MPE_EXPECT_SAMPLES`, derived `jitter_n` floor). No Pi measurement time unless re-run
is mandatory.

## Original run

| Field | Value |
|-------|-------|
| Artifact dir (Pi) | `~/reference-suite-pi4-20260822-204559/` |
| JSON | `reference-suite-pi4-pass1.json` |
| Repo at run | `110977a` |
| Binary | stock (`~/surge/build/surge_xt_products/surge-xt-cli`) |
| Window | 25 s × 2 runs per cell |
| Cells | 12 loaded (P1–P12) + 3 silence (S1–S3) |

## Re-validation method

- **Parser:** `a1e80e3` on Pi `~/MPE-Module`
- **Export:** `MPE_EXPECT_SAMPLES=25`
- **Primary tag:** last `SENTINEL run-complete` row per cell log (run 2)
- **Checks:** `mpe_result_load_tag`, `mpe_result_physics_assert`, `jitter_n ≥ floor(25)=42`
- **Script:** `scripts/revalidate-reference-suite-pass.sh`

## Results — loaded cells (P1–P12)

| cell | tag | status | xruns | dsp_median | samples | jitter_n |
|------|-----|--------|-------|------------|---------|----------|
| P1-Crystals | A-b1024-p2-l0-run2 | PASS | 0 | 38.399246 | 25 | 1502 |
| P2-Crystals | A-b512-p2-l0-run2 | PASS | 0 | 39.294529 | 25 | 3008 |
| P3-Crystals | A-b256-p3-l0-run2 | PASS | 2 | 40.741222 | 25 | 6027 |
| P4-Cloud_Horn | A-b1024-p2-l0-run2 | PASS | 0 | 56.891697 | 25 | 1505 |
| P5-Cloud_Horn | A-b512-p2-l0-run2 | PASS | 4 | 58.174309 | 25 | 3016 |
| P6-Cloud_Horn | A-b256-p3-l0-run2 | PASS | 15 | 59.367722 | 25 | 6041 |
| P7-Duduk | A-b1024-p2-l0-run2 | PASS | 0 | 38.341194 | 25 | 1501 |
| P8-Duduk | A-b512-p2-l0-run2 | PASS | 0 | 39.396675 | 25 | 3009 |
| P9-Duduk | A-b256-p3-l0-run2 | PASS | 0 | 40.447342 | 25 | 6029 |
| P10-Brave_New_World | A-b1024-p2-l0-run2 | PASS | 0 | 38.411869 | 25 | 1500 |
| P11-Brave_New_World | A-b512-p2-l0-run2 | PASS | 0 | 39.302170 | 25 | 3009 |
| P12-Brave_New_World | A-b256-p3-l0-run2 | PASS | 2 | 40.495159 | 25 | 6033 |

Silence cells (S1–S3) use relaxed parsing in the suite harness and were not re-validated
with the strict loaded-cell parser path.

## Verdict

**CONTROL STANDS.** 12/12 loaded cells pass at `a1e80e3`. Stock-binary pass 1 remains the
A2 control; proceed to A3 (a72 install + suite re-run).

## Harness audit (same session)

| Script | MPE_EXPECT_SAMPLES on write | MPE_EXPECT_SAMPLES on read |
|--------|----------------------------|----------------------------|
| `measure-latency-run.sh` | exported before `mpe_result_physics_assert` (line ~612) | N/A |
| `measure-reference-suite.sh` | exported at script start + pilot cell | exported before `_parse_last_run` / `load_tag` |
| `measure-confirm-at-voices.sh` | via `measure-latency-run.sh` delegate | read path uses awk only (no `load_tag`) — OK |

No harness gaps found for A3 paths.
