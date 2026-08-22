# V7 capacity curve + V3 latency win

*Measured: 2026-08-21 (America/Toronto)*  
*Pi artifacts: `/root/plan-v7-20260821-223340`*  
*Harness: `docs/scarlett-findings` @ `3784a3f`*

## Patch

**Crystals** (`~/Documents/Surge XT/Patches/Quick Select/Crystals.fxp`) — loaded on Surge at run time per `~/.patch_browser_poly_state.json`.

Capacity numbers are **meaningless without this patch name.**

## Standing conditions

| item | value |
|---|---|
| Poly governor | **OFF** (unit stopped/disabled) |
| Poly ceiling/floor | **64** (out of the way) |
| Strict mode | **ON** during cells; softmode restored after |
| Governor / clock | `performance` @ **1800 MHz**, `arm_boost=1`, `throttled=0x0` throughout |
| Card | **2** (live resolve) |
| Load pattern | `midi-load-hold.py` — N simultaneous MPE voices, 12 s probe / 20 s confirm |
| Probe step | +2 voices; first xrun delta > 0 = first overrun |

## V7 — capacity curve

| buffer | voices to first overrun | highest sustained clean | DSP ms @ clean (median) | DSP p99.9 / max @ clean |
|---|---|---|---|---|
| **1024×3** | **6** | **4** | 10.07 ms (47% of 21.3 ms period) | 97.9% / 99.5% |
| **512×3** | **4** | **2** | 3.28 ms (31% of 10.7 ms) | 66.1% / 73.4% |
| **256×3** | **4** | **2** | 1.73 ms (32% of 5.3 ms) | 91.8% / 92.0% |

Confirm windows: **n ≈ 20** jack_cpu_load samples each (20 s) — shape claims not supported; transition counts are the datum.

### Recommended poly ceilings (from V7, Crystals + hold pattern)

Use **sustained-clean** as the hard cap; leave one voice headroom for performance if desired:

| buffer | sustained clean | suggested `MPE_POLY_CEILING` |
|---|---|---|
| 1024×3 | 4 | **4** (was **12** — guess was ~3× too high for this patch) |
| 512×3 | 2 | **2** |
| 256×3 | 2 | **2** |

**V7's ceiling numbers are the input the governor work needs** — tune against these, not against 12.

### Probe log (transition detail)

| buffer | ramp |
|---|---|
| 1024 | 2→0, 4→0, **6→86 xruns** |
| 512 | 2→0, **4→2 xruns** |
| 256 | 2→0, **4→14 xruns** |

## V3 — `1024×2` vs `1024×3` baseline

**n = 3 runs × 60 s**, condition A, `midi-load.py` (default 3-voice harness load), strict mode.

| config | meter xruns/min (runs) | probe xruns/min | dsp_p99 | ALSA xrun lines |
|---|---|---|---|---|
| **1024×2** | 1248 / 1289 / 1248 → **~1262** | ~1400 | 100% | **0** |
| **1024×3** | 1227 / 1337 / 1379 → **~1314** | (not extracted) | 100% | **0** |

**Latency win stands:** `1024×2` = **42.7 ms** shipping total vs **64.0 ms** today, **same 21.3 ms Surge compute deadline** per callback.

**Under this overload load, xruns/min does not materially improve** with nperiods=2 (both saturated at ~100% DSP). V3 confirms ALSA accepts the config and quantifies that the win is **latency**, not headroom under fixed midi-load.

W0 (opens) + this run (stable strict measurement) → **candidate shipping config** pending product sign-off, not xrun improvement under overload.

## Poly governor — document only (no code this pass)

Recorded for later implementation (`PROMPT-YOLO-poly-governor.md`):

- **Root cause of pops:** poly governor cut polyphony under CPU load → **stole sounding voices** (confirmed by ear with governor off/on).
- **Fix 2 (fade) is the actual fix** — hard cut = step discontinuity = the pop.
- **Fix 1 is steal *policy*, not prohibition:** released/in-release first, then quietest, then oldest. Refusing note-ons reads as broken on a performance instrument.
- **Fix 3:** hysteresis — separate raise/lower thresholds, rate-limit changes.
- **Open question:** governor sets poly limit over OSC; Surge decides what dies when limit drops below sounding count. **Does fade belong in Surge voice handling or in how we drive the limit** (e.g. lower only at note-off boundaries)? Settle before code.

## What this retires

| item | killed by |
|---|---|
| `MPE_POLY_CEILING=12` as a universal guess | **V7** — Crystals sustains **4** @ 1024, **2** @ 512/256 |
| Fixed 75-voice-equivalent load as capacity proxy | **V7** — transition counts replace arbitrary soak |
| nperiods=2 as an xrun fix under overload | **V3** — same saturation; win is latency only |
| (prior dead list items 1–7) | see `PROMPT-V7-capacity-curve.md` |

## Could not measure

| item | why |
|---|---|
| Capacity > 32 simultaneous hold voices | ramp capped at 32; Crystals failed well below |
| Per-patch ceilings for patches other than Crystals | one patch per V7 design |
| V5/V6 clock arms | V0 — already at performance + arm_boost |

## Artifacts

- `/root/plan-v7-20260821-223340/v7-capacity.log`
- `/root/plan-v7-20260821-223340/v3-1024x2.log`
- `/root/plan-v7-20260821-223340/v3-baseline-1024x3.log`

## Branch hygiene (Mitch)

`docs/scarlett-findings` is **~30 commits** ahead of `dev` with measurement doctrine, W1/V7 harnesses, skills, and AGENTS updates. **Merge to `dev` regardless of measurement outcome** — still outstanding.
