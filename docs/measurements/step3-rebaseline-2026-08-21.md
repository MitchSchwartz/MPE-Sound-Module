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

### 256 × 3 — three streams, three rates (not noise)

Per-run arrays — full analysis in [`stream-start-variance-2026-08-21.md`](stream-start-variance-2026-08-21.md):

```
T11  256   10 16  8 15 20 12  8  6 12 20  4 10 14 12 14
T13  256    4  0  2  2  0  0  1  0  0  2  2  4  2  4  0
hyg  256   16  4  6 10  4  4 12  4 12 10  6  6  8  6  9
```

T13 never exceeds 4; post-hygiene never drops below 4; **no overlap**. Each row is one
jackd stream × 15 correlated windows. **Do not quote any 256×3 mean as a population rate.**
T13's 128×6 vs 256×3 refutation (within one invocation) still stands.

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
