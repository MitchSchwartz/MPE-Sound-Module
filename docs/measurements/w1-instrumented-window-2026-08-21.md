# W1 — four-instrument window

*Measured: 2026-08-21 (America/Toronto)*  
*Pi artifacts: `/root/w1-20260821-214044`*  
*Harness: `docs/scarlett-findings` @ `b5eee1a`*

## Setup

| item | value |
|---|---|
| Load | **Condition A** — 75-voice `midi-load.py` via `measure-latency-run.sh` (identical all cells) |
| Card | **2** (live resolve — Scarlett 4i4 USB) |
| Fill poller | 10 Hz, CPU1, `nice 19`, no fork loop |
| Window | 60 s per cell, **n=1** per cell |
| `throttled` | `0x0` all windows |

## W0

**1024×2 opens.** Confirmed earlier same evening (`jackd -p 1024 -n 2` UP); not re-measured.

## Control — poller perturbation (1024×3, cond A)

| run | meter xruns/min | probe xruns/min |
|---|---|---|
| poller OFF | 6 | 8 |
| poller ON | 2 | 4 |

Poller-on was **not** materially worse (fewer events in this pair). Treat poller as **transparent enough** for this session; W1 ladder cells used poller ON.

## Four-instrument table

| cell | #1 probe events/min | #2 ALSA count | #2 magnitudes (msec) | #3 fill min (frames) | #3 shape | #4 dsp_p99 |
|---|---|---|---|---|---|---|
| W1-a 1024×3 | 6 | **0** | — | 0* | flat high | 58.9% |
| W1-b 512×3 | 2 | **0** | — | 0* | flat high | 62.4% |
| W1-c 256×3 | 32 | **0** | — | 513 | flat high | 76.1% |

\*Single-sample `min=0` on 1024/512 with `sanity_over_buf=0` — likely pointer-wrap edge at one 10 Hz sample, not sustained drain. p50 fill ≈ 83% of buffer all cells.

Meter xruns/min (instrument cross-check): W1-a **4**, W1-b **0**, W1-c **28**.

### Instrument 3 detail (10 Hz, n≈530 samples/cell)

| cell | buf | p50 fill | p99 fill | mean fill | % of buf (p50) |
|---|---|---|---|---|---|
| W1-a | 3072 | 2547 | 3057 | 2553 | 83% |
| W1-b | 1536 | 1279 | 1529 | 1276 | 83% |
| W1-c | 768 | 635 | 764 | 634 | 83% |

No multi-second drift toward zero. **10 Hz cannot resolve per-period sawtooth** (see PROMPT-W1 amendment); shape call is slow drift only.

### Instrument 2 — post-hoc journal

Harness `jackd_alsa_xrun_stats` hit **mawk** incompatibility during run (fixed after — gawk-style `match(..., arr)`). Post-hoc `journalctl` for each window: **zero** lines matching `ALSA: xrun of at least N msecs`.

During W1-c the journal shows **`JackEngine::XRun: client = Surge XT was not finished`** and **`mpe-peak-meter was not finished`** — graph overruns, not ALSA underrun messages. Instrument 2 correctly reports **0 ALSA magnitudes** when failures are graph-side.

## Interpretation row — **explicit**

**Primary: `#1 = N`, `#2 = 0` → all counted xruns are graph overruns.** The cushion was never drained. Producer-lateness / IRQ-priority / ~600 µs stall levers are the **wrong term** for this symptom on this stack.

**Secondary (256 cell): `#3 flat` + `#4 ≈ 76%` → compute-bound at small buffer** — steep dsp_p99 ladder (59% → 62% → 76%) reproduces Step 4 direction with matched load. Fixed per-callback costs dominate at 256×3.

Fill trace shape per cell: **flat high fill, no sustained drain, xruns at 256 anyway** — consistent with **P3 (counter fires on graph overruns)** plus compute headroom collapse, not P2/P2′ drain or P1 giant stall.

## What this retires

| line of work | status |
|---|---|
| Producer lateness / cushion drain as xrun cause | **retired** (reinforces `cushion-model-2026-08-21.md`) |
| `irq/30` FF 90, `isolcpus`, `nohz_full`, PREEMPT_RT for this crackle | **retired** (was parked; W1 adds evidence) |
| ~600 µs cyclictest stall as binding term | **retired** for xrun mechanism (`find-600us-2026-08-21.md` Step 2 still true; not what empties buffer) |
| Aligned period tables | **retired** (already dead pre-W1) |
| URB rate / swap / major-fault hypotheses | **retired** (Steps 0–1) |

**Not retired:** Surge/graph CPU at 256×3 — dsp_p99 76% with 28–32 xruns/min is the live problem.

## Could not measure

| item | why |
|---|---|
| Per-period fill waveform | 10 Hz poller by design; raising rate would perturb (PROMPT-W1 amendment) |
| ALSA underrun magnitudes this run | No ALSA xrun lines in journal — failures were graph-side |
| Distribution shape / confidence intervals | **n=1** per cell — rates are point estimates only |

## Harness notes

- Poller sanity passed (`sanity_over_buf=0`).
- Post-run restore left jackd on **1024×3** with **`-s` softmode** — re-apply strict reporting before the next trusted count if needed.
- Raw fill traces: `/root/w1-20260821-214044/*-fill-*.log`
- Full cell logs: `/root/w1-20260821-214044/{control-no-fill,control-fill,W1-a,W1-b,W1-c}.log`
