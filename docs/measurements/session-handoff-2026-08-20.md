# Session handoff — 2026-08-20 (consolidated)

**Branch:** `plan/t6-harness` @ `f543f38` · **Status: paused**

Single rollup. Detail lives in linked artifacts; don't re-read the full work order unless
resuming a specific task.

---

## What landed (code + docs)

| Item | Commit | One line |
|---|---|---|
| T2 sweep | `656b081` | Class A/B/B-live table — findings only |
| T3 guards | `656b081` | periodic-loop lint + meter liveness on appliance |
| E1 measurement | `ee73fb0` | Config refuted (A 6.2×); crowding not isolated |
| I2 harness fix | `e4c32fe` | `_meter_xruns` fails loud; RESULT `meter_live=1` |
| T6 rig sweep | `f543f38` | xrun-corr fixed; lint walks call graph; README §rig |
| T4/T5 scripts | `f543f38` | `measure-loop-curve.sh`, `measure-soak.sh` (not run to completion) |

---

## Measurements (confidence labelled)

### Baseline ladder (pre-E1, n=15, 512×3) — **measured**

| cond | xruns/60 s mean |
|---|---:|
| A | 0.13 |
| B | 2.27 |
| C | 2.53 |
| D | 3.13 |

512 usable without looper; not shippable with it. 1024 remains default.

### E1 three-core experiment — **experiment, refuted**

Two variables (`irqaffinity` + `CPUAffinity`). A went to **0.80** (6.2× worse, no looper).
Reverted to `irqaffinity=0,1` + `CPUAffinity=2 3`. Artifact:
[`e1-three-cores-T1-2026-08-20.md`](e1-three-cores-T1-2026-08-20.md).

### I3 revert check (n=5, 512×3, condition A) — **measured**

Config verified on Pi. Runs: 2,0,0,0,2 → **mean 0.80**. Harness showed `meter_live=1` every
window. Config reverted; **numbers do not match baseline 0.13** (n=5 underpowered, but
two events in five runs). Artifact:
[`i3-e1-revert-verify-2026-08-20.md`](i3-e1-revert-verify-2026-08-20.md).

### T4 loop curve — **partial, paused**

Stopped mid `buf1024-loops8` (90/120 runs). Raw log on Pi: `/tmp/t4-loop-curve.log`.

| block | n | mean xruns/60 s |
|---|---:|---:|
| buf512-loops0 | 15 | 3.00 |
| buf512-loops4 | 15 | 1.33 |
| buf512-loops8 | 15 | 2.67 |
| buf512-loops16 | 15 | 3.40 |
| buf1024-loops0 | 15 | 0.00 |
| buf1024-loops4 | 15 | 0.00 |
| buf1024-loops8 | — | *interrupted* |

**Guess:** idle 512 curve is flat/noisy at this n; 1024 looks clean so far. Not enough to
pick a loop-count tier spec — do not quote as product truth.

### T5 soak — **not started**

Script ready: `scripts/measure-soak.sh --hours 8`. Blocked on: I3 baseline ambiguity +
user pause.

---

## Harness guards (T6) — proven on Pi

- Missing/stale meter → exit 1 (not 0). `tests/test_meter_harness.sh` on Pi.
- `MPE_PEAK_METER=0` → harness exit 1.
- RESULT lines carry `meter_live=1` when a number is recorded.

Sweep: [`t6-harness-sweep-2026-08-20.md`](t6-harness-sweep-2026-08-20.md).

---

## Pause — resume here

1. **I3:** n=15 condition A @ 512 — confirm revert vs baseline before trusting any soak.
2. **T4:** optional — finish last 30 runs if loop-count curve still wanted; else discard partial.
3. **T5:** 8 h soak only after I3 clears.
4. **Merge:** PR #85 → experiment-plan → feat/audio-core-affinity → `dev` (unchanged).

Do not merge D numbers measured before named bugs were fixed (gate still applies).

*Last updated: 2026-08-20 (America/Toronto)*
