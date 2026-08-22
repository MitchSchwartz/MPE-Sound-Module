# Plan V — V0 / V1 / V2 results

*Measured: 2026-08-21 (America/Toronto)*  
*Pi artifacts: `/root/plan-v-20260821-221011`*  
*Harness: `docs/scarlett-findings` @ `0d44df2`*

## V0 — pre-checks and actions

| check | finding | action taken |
|---|---|---|
| **V0-a governor** | `MPE_CPU_GOVERNOR=performance`; all CPUs **1800 MHz**; **`arm_boost=1`** already in `config.txt` | **none** (V5/V6 baselines may already be active — see below) |
| **V0-b poly governor** | `surge-poly-governor` was **active**; poly env vars **unset** (defaults + dynamic governor) | `MPE_POLY_GOVERNOR=0`, `MPE_POLY_CEILING=16`, `MPE_POLY_FLOOR=16`; unit **stopped + disabled** |
| **V0-c softmode** | `MPE_JACK_SOFTMODE=1`; jackd had **`-s`** | strict mode during cells; **restored softmode** after V2 |

**Confound note:** Poly governor was likely active during **all prior measurement cells** (service active, env unset). W1 and earlier runs may have had **non-constant poly** under load. V1/V2 below used fixed poly=16, governor off.

**Clock note:** Box was already on **`performance` @ 1.8 GHz** with `arm_boost=1` before V0. Historical `get_throttled=0x50000` is **not** cited as a constraint; all readings this session **`0x0`**.

## V1 — silence test (Surge on, zero notes)

**n = 3 runs × 30 s per buffer.** Metric: `jack_cpu_load` → absolute ms per period (p50 / p99 / max).

| buffer | period ms | dsp_ms median (runs) | dsp_ms p99 (typical) | dsp_ms max (typical) |
|---|---|---|---|---|
| **1024×3** | 21.33 | 1.286 – 1.294 | ~1.31 | ~1.32 |
| **512×3** | 10.67 | 0.743 – 0.750 | ~0.76 | ~0.82 |
| **256×3** | 5.33 | 0.417 – 0.422 | ~0.45 | ~0.55 |

Median absolute ms **scales ~linearly with period length** (~6–8% of deadline at all three sizes). It is **not flat at ~1.1 ms**.

As **percent of deadline:** ~6.0% @ 1024 → ~7.0% @ 512 → ~7.9% @ 256 (slight uptick at smaller buffers).

### V1 outcome row — **explicit**

**Row 3: not flat — scales with buffer.** There is **no fixed absolute-ms per-callback term** of the kind the W1 regression inferred. The `T = a + b·N` model with **a ≈ 1.1 ms** is **not confirmed**; the silence data fit a **proportional (per-sample) cost** far better than a constant offset.

**V4 is gated off.** Do not profile for a fixed term that V1 did not confirm.

## V2 — client-count test @ 1024×3

**n = 3 runs × 30 s each.** Same strict mode, fixed poly, no midi-load.

| condition | dsp_ms median | dsp_ms p99 | dsp_ms max |
|---|---|---|---|
| **Surge OFF** (other JACK clients remain: peak meter, `jack_cpu_load`, etc.) | **1.312 – 1.315** | ~1.33 | ~1.40 |
| **Surge ON** (silence, patch loaded) | **1.347 – 1.348** | ~1.37 | ~1.38 |

**Delta (Surge ON − OFF): ~0.035 ms (~35 µs)** on median; p99 delta ~0.04 ms.

### V2 verdict — single-client refactor

**The refactor is dead.** JACK graph / inter-client overhead is **~35–40 µs**, not a meaningful share of the ~1.3 ms silence budget. Surge silence at 1024 adds almost nothing over an empty-ish graph; the ~1.3 ms at 1024 is **baseline graph + instrumentation**, not Surge DSP.

## What this retires

| item | status |
|---|---|
| **Fixed ~1.1 ms per-callback constant** (W1 regression) | **retired** — V1 shows proportional scaling, not flat ms |
| **V4 profile-the-callback** | **not run** — gated on V1 confirm |
| **Single-client architecture refactor** (Surge hosts looper) | **retired** — V2 delta ~35 µs |
| **“Graph traversal is the problem”** | **retired** at measured scale |

## What remains open (not run this session)

| cell | note |
|---|---|
| **V3** | 1024×2 — independent, still pending |
| **V5** | governor `performance` — **already active**; cell may be baseline-only or skip |
| **V6** | `arm_boost=1` — **already active**; diagnostic compare vs stock 1.5 GHz needs revert-to-stock branch, not yet run |

## Could not measure

| item | why |
|---|---|
| Surge-isolated cost with **zero** JACK clients | JACK requires clients for `jack_cpu_load`; empty graph still carries peak meter etc. |
| High-confidence shape / CI | n=3 per cell — rates and medians are point estimates |

## Artifacts

- Master log: `/root/plan-v-20260821-221011/plan-v.log`
- V1: `/root/plan-v-20260821-221011/v1-silence.log`
- V2: `/root/plan-v-20260821-221011/v2-client-count.log`
