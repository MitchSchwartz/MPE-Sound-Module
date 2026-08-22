# I3 — E1 revert verification (2026-08-20)

**Experiment:** confirm E1 revert restored condition A baseline after config-only checks.
**Harness:** `e4c32fe` — `meter_live=1` on every RESULT line.

## Config verified (not sufficient alone)

```
irqaffinity=0,1
CPUAffinity=2-3 (mpe-jackd, surge-xt-cli, mpe-sooperlooper)
jackd taskset: 2,3
```

## Measurement — condition A, 512×3, n=5, 60 s/run

Pi `plan/t6-harness` @ `e4c32fe`. Log: `/tmp/i3-e1-revert-verify.log`.

| run | xruns/60 s | meter_live | meter_max_age_s |
|---:|---:|---|---:|
| 1 | 2 | 1 | 0 |
| 2 | 0 | 1 | 0 |
| 3 | 0 | 1 | 0 |
| 4 | 0 | 1 | 0 |
| 5 | 2 | 1 | 0 |

**Mean: 0.80 xruns/60 s** (sd ≈ 1.10, n=5)

## Compare

| source | A mean @ 512 | n |
|---|---:|---:|
| Pre-E1 baseline | 0.13 | 15 |
| E1 bad config | 0.80 | 15 |
| Post-revert doc check (ee73fb0) | 0.40 | 5 |
| **This run (I3)** | **0.80** | **5** |

## Verdict

**Config reverted — measured A does not match baseline yet.**

- Revert **did take** on irqaffinity + CPUAffinity (verified above).
- Mean **0.80 matches E1 bad-config A**, not baseline **0.13**. With n=5 this is
  underpowered vs the n=15 ladder, but two events in five runs is not “14/15 clean.”
- **Not** evidence the revert failed — could be run-to-run variance, strict-mode +
  midi-load differ from the original ladder, or warm stack state. **Does** mean we
  should not treat the revert as verified at baseline until n=15 repeats ~0.13.

Harness I2 fix exercised: every RESULT carried `meter_live=1`; a blind meter would
have aborted the run.

*Last updated: 2026-08-20 (America/Toronto)*
