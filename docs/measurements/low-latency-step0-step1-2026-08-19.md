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

**Run 6 = 7** after stack restart vs **run 5 = 21** — reset toward block-1 baseline (~4–24
band), not continuation at 26+. Block 2 does not re-climb monotonically (7, 5, 14, 10, 9).

**Verdict:** accumulation is **stack-scoped** — lives in sooperlooper / session /
watchdog state and clears on unit restart. Not session-scoped (would stay high at run 6).
Not pure noise across n = 10 (block 1 still climbs; restart resets it).

**Proceed to Step 2** (IRQ affinity), but **parallel hunt:** find what grows in the looper
stack over ~5 min (OSC subscriptions, buffers, registrations). Step 2–4 measurements remain
valid if stack is restarted before each block until leak is fixed.

Pi log: `~/latency-step1b-accumulation.log`

## delay_usec — probe status

`mpe-xrun-probe` fires (hundreds of callbacks per run) but **`jack_get_xrun_delayed_usecs`
returns 0.000 on JACK 1.9.22 / ALSA backend** — also logged `delay_max_usec`. Harness now
reports `delay_events` / `delay_nonzero`; first accumulation run had probe lifecycle bug
(fixed: `pkill -x mpe-xrun-probe` per window). **Step 2 gate:** need non-zero delay or an
alternate backend metric before trusting IRQ fixes by count alone.

## Notes

- Self-test ~31% at condition A (10 s, no midi-load) is **not** comparable to D15's 38.6%
  baseline — do not quote as regression.
- Run 3 block-1 p99 50.6% in step1b — watch against delay once available.

---

*Step 2 (IRQ affinity) waits on: Mitch at reboot + delay metric unblocked.*
