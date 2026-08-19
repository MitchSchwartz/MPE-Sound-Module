# Low-latency work order — Step 0 harness + Step 1 escalation

**Pi:** `raspberrypi2` · **2026-08-19**

## Step 1 — first block (commit `5d43991`, before delay probe)

512×3, condition **D**, strict jackd, `midi-load.py`, 5×60 s back-to-back.

| run | xruns / 60 s | DSP median | DSP p99 | temp |
|---:|---:|---:|---:|---|
| 1 | 13 | 39.64% | 45.53% | 55.0°C |
| 2 | 5 | 39.28% | 43.08% | 55.5°C |
| 3 | 15 | 39.22% | 67.89% | 55.5°C |
| 4 | 16 | 39.17% | 42.94% | 56.0°C |
| 5 | 26 | 39.52% | 43.18% | 55.5°C |

**Trend:** ranked by time → 2, 1, 3, 4, 5. One low outlier at run 2, then strictly
increasing. Spearman ρ = 0.90, exact one-tailed p = 0.042 at n = 5 — significant monotone
increase, same shape as D15's 7 → 24 → 29.

**Thermal ruled out:** temp flat 55–56 °C, `throttled=0x0` all runs. Something accumulates
over ~5 minutes at 512 with no heat component.

**Run 3 p99:** 67.89% vs ~43% in the other four — single large DSP transient, not aligned
with peak xrun count. Watch once delay distribution exists.

Pi log: `~/latency-step1-512-D.log`

## Step 0 gaps (fixed before Step 2)

1. **delay_usec** — journal `JackEngine::XRun` lines carry no delay. Harness now uses
   `native/mpe-xrun-probe` (`jack_set_xrun_callback` + `jack_get_xrun_delayed_usecs`).
2. **Accumulation disambiguation** — second 5×60 s block with full-stack restart between
   blocks; run 6 is the tell (stack-scoped vs session-scoped vs noise).

Self-test ~31% at condition A is **not** comparable to D15's 38.6% baseline (10 s, no
midi-load) — do not quote as baseline.

## Step 1b — accumulation test (pending)

`--runs 10 --restart-between 5` at 512×3 condition D with delay probe. Results → append
below after run completes.

---

*Step 2 (IRQ affinity) waits on: delay probe verified + accumulation test interpreted.*
