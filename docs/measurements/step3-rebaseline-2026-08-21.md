# Step 3 — re-baseline after Phase 0 hygiene (2026-08-21)

**Harness:** separate invocations (not `--no-restore-buffer` chain) · **Condition A** unless noted · **n=15** × 60 s · **meter_live=1** on every window.

Logs on Pi: `~/hygiene-rebaseline-{512-A,256-A,1024-D8}.log`

## 512 × 3, condition A

| metric | value |
|---|---|
| mean xruns / 60 s | **0.13** (2 total across 15 windows) |
| clean / 15 | **14/15** |
| void windows | 0 |

Per-run xruns: 0,0,0,0,0,0,0,0,0,0,0,0,0,0,**2**

## 256 × 3, condition A

| metric | value |
|---|---|
| mean xruns / 60 s | **7.80** |
| clean / 15 | **0/15** |
| void windows | 0 |

Per-run xruns: 16,4,6,10,4,4,12,4,12,10,6,6,8,6,9

### 256 × 3 discrepancy — still open

| run | mean xruns/60 s | clean |
|---|---:|---:|
| T11 (pre-hygiene) | 12.10 | 0/15 |
| T13 (pre-hygiene) | 1.53 | 6/15 |
| **Step 3 (post-hygiene)** | **7.80** | **0/15** |

Separate invocations removed run-order as an explanation, but the spread **12.10 → 1.53 → 7.80** is still not a single stable number. **Do not quote an absolute 256×3 rate.** Relative claims within one harness invocation remain valid (T13 128×6 vs 256×3 refutation unchanged).

## 1024 × 3, condition D, 8 loops playing — shipping claim re-take

| metric | value |
|---|---|
| mean xruns / 60 s | **0.20** (3 total) |
| clean / 15 | **12/15** |
| void windows | 0 |

Runs with xruns: run2=1, run8=1, run11=1.

**Prior claim (0.00, 15/15) does not replicate** on the cleaned appliance — but both old
and new numbers are **single-stream draws** and are **not shippable claims**. See
[`stream-start-variance-2026-08-21.md`](stream-start-variance-2026-08-21.md).

## Confidence split (updates spec)

**Survives Phase 0 (structural / within-run relative):**

- Period size binds at equal runway (T13)
- Drain below JACK; Poisson xruns (T5)
- 512 × 3 not shippable with full stack at low latency (this run: 14/15 clean at A only — looper not in A)

**Provisional (absolute rates — do not quote until stream sampling):**

- All Step 3 means are **within-stream only** — see [`stream-start-variance-2026-08-21.md`](stream-start-variance-2026-08-21.md)
- Phase 0 **delta unevaluated**; defects fixed, benefit not yet measured on the right axis
- Shipping claim **withdrawn** (was one stream; so was re-take)

*All windows meter_live=1; trap-5 passed on buffer cells.*
