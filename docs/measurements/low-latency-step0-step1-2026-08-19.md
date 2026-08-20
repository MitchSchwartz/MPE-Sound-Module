# Low-latency work order — Step 0/1/1b

**Pi:** `raspberrypi2` · **2026-08-19/20**

## Step 1 — first block (commit `5d43991`, meter count only)

512×3, condition **D**, 5×60 s back-to-back.

| run | xruns | DSP median | temp |
|---:|---:|---:|---|
| 1 | 13 | 39.64% | 55.0°C |
| 2 | 5 | 39.28% | 55.5°C |
| 3 | 15 | 39.22% | 55.5°C |
| 4 | 16 | 39.17% | 56.0°C |
| 5 | 26 | 39.52% | 55.5°C |

**Trend (corrected):** ranked by time → 2, 1, 3, 4, 5. One low outlier at run 2, then
strictly increasing. Spearman ρ = 0.90, exact one-tailed p = 0.042 — significant monotone
increase at n = 5, same shape as D15's 7 → 24 → 29.

**Thermal ruled out:** flat 55–56 °C, `throttled=0x0` all runs. Something accumulates over
~5 minutes with no heat component.

Pi log: `~/latency-step1-512-D.log`

## cyclictest floor (stock kernel)

`~/latency-cyclictest-stock.log` · **worst case 320 µs** (avg 7 µs). Well inside 512×3
period (~10.7 ms); 256×3 (~5.3 ms) is arithmetically feasible on scheduler floor alone.

## Step 1b — accumulation disambiguation (commit `925abe3`)

10×60 s, condition **D**, full-stack **restart between run 5 and run 6**.

| run | block | xruns | DSP median | temp |
|---:|---|---:|---:|---|
| 1 | 1 | 4 | 38.42% | 54.0°C |
| 2 | 1 | 20 | 38.49% | 54.5°C |
| 3 | 1 | 24 | 38.35% | 55.0°C |
| 4 | 1 | 9 | 38.21% | 55.5°C |
| 5 | 1 | 21 | 38.43% | 55.0°C |
| **6** | **2 (post-restart)** | **7** | 38.67% | 55.0°C |
| 7 | 2 | 5 | 38.04% | 54.5°C |
| 8 | 2 | 14 | 38.37% | 54.5°C |
| 9 | 2 | 10 | 38.23% | 54.5°C |
| 10 | 2 | 9 | 38.64% | 54.0°C |

### CORRECTED VERDICT — no accumulation, and no trend

An earlier revision of this section concluded "accumulation is stack-scoped … clears on
unit restart" and dispatched a parallel leak hunt. **That conclusion is withdrawn.** It
does not survive the arithmetic, and it would have sent someone hunting a leak that this
data gives no evidence for.

**1. The trend did not replicate.** Spearman ρ on runs by time:

| block | values | ρ | exact one-tailed p |
|---|---|---:|---:|
| Step 1 original | 13, 5, 15, 16, 26 | 0.90 | **0.042** |
| Step 1b block 1 | 4, 20, 24, 9, 21 | 0.50 | 0.225 |
| Step 1b block 2 | 5, 14, 10, 9 | 0.20 | 0.458 |

The single significant result failed to reproduce on the next attempt. D15's monotone
7 → 24 → 29 has a 1-in-6 chance of arising by luck at n = 3. Taken together this is a
false positive, not a replicated effect. **Escalation is disproved.**

**2. The restart effect is not distinguishable from noise.** Over all ten runs,
mean 12.3, sd 7.1.

- Run 6 (post-restart) = 7 sits at **z = −0.75**. Unremarkable.
- Runs 1 and 4 produced **4 and 9 with no restart at all** — at or below the post-restart
  value.

A drop to 7 after a restart is the same size as the drop to 9 that happened
spontaneously one run earlier. There is no restart effect here.

**3. What the data does show — and it is the real blocker.** Spread is **6×** (4 to 24),
sd 7.1 on mean 12.3, CV 0.58. Power on that: detecting a genuine **50% improvement**
(12.3 → 6.2) with n = 5 per arm gives roughly **25% power**. A real halving of xruns
would be missed three times in four.

**Counting xruns cannot evaluate Step 2.** IRQ affinity could work perfectly and the
measurement would very likely report nothing. This — not `delay_usec` — is what gates
Step 2.

Pi log: `~/latency-step1b-accumulation.log`

## delay_usec — probe status

`mpe-xrun-probe` fires (hundreds of callbacks per run) but **`jack_get_xrun_delayed_usecs`
returns 0.000 on JACK 1.9.22 / ALSA backend** — also logged `delay_max_usec`. Harness now
reports `delay_events` / `delay_nonzero`; first accumulation run had a probe lifecycle bug
(fixed: `pkill -x mpe-xrun-probe` per window, `713cc9c`).

**Abandon this metric.** JACK2's ALSA backend never populates the field — it is filled on
some driver paths and not that one. There is no flag and no build option. Do not spend
further time in the JACK2 source.

It would not have been sufficient anyway: one delay sample per xrun is ~12 samples per
run, which does not solve the variance problem above.

**Replacement metric — per-callback period jitter.** In the probe's process callback,
take `clock_gettime(CLOCK_MONOTONIC)` and record the delta from the previous callback.
Expected period at 512/48 kHz is **10,667 µs**; deviation from it *is* the jitter.

- ~94 callbacks/s at 512 → **~5,600 samples per 60 s run**, against ~12 xruns.
- Yields a distribution — median, p99, p99.9, max — on continuous data.
- An IRQ fix that tightens p99 from 3 ms to 400 µs is unmistakable at n = 5,600 even if
  the xrun count barely moves.
- An xrun becomes the visible tail of a distribution rather than the only observable.

`jack_frames_since_cycle_start()` at callback entry is a useful second signal: it reports
how late the callback entered its period, which is closer still to the quantity of
interest, and it is backend-independent.

## Notes

- Self-test ~31% at condition A (10 s, no midi-load) is **not** comparable to D15's 38.6%
  baseline — do not quote as regression.
- Run 3 block-1 p99 50.6% in step1b — watch against delay once available.

---

*Step 2 (IRQ affinity) waits on: the period-jitter histogram + a baseline run at 512.
It does **not** wait on Mitch — the affinity masks are already `0-3`, so Step 2 is a
runtime write with instant rollback and no reboot. Only Steps 3 and 4 need him.*
