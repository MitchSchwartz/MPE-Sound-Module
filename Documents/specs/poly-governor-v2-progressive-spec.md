# Poly governor v2 — ramp-aware proactive limit (no duck)

**Status:** Approved (Gate A 2026-08-23) · **Pi 5 default candidate:** `always_on` (ear tune 2026-08-23)  
**Created:** 2026-08-23 (America/Toronto)  
**Last updated:** 2026-08-23 19:47 (America/Toronto)

**Pi 5 result:** [`docs/measurements/poly-governor-v2-always-on-pi5-2026-08-23.md`](../../docs/measurements/poly-governor-v2-always-on-pi5-2026-08-23.md)

**Related:** [`docs/measurements/poly-governor-instrumentation-2026-08-21.md`](../../docs/measurements/poly-governor-instrumentation-2026-08-21.md) · [`docs/measurements/archive/V7-capacity-curve-plan.md`](../../docs/measurements/archive/V7-capacity-curve-plan.md) · [`docs/measurements/G2-RESULT-2026-08-23.md`](../../docs/measurements/G2-RESULT-2026-08-23.md) · [`patch_browser/surge_poly_governor.py`](../../patch_browser/surge_poly_governor.py)

**Supersedes (behaviorally):** discrete threshold-step v1 (warm / spike / high / emergency table) once v2 passes ear test. v1 fade actuation (PR #105) stays as the voice-release layer.

---

## Problem statement

Mitch reports the current governor still produces **two audible failures in sequence**:

1. **Crackle** — graph xruns while playing hard (deadline exceeded before actuation helps).
2. **Obvious level drop** — sudden thinning when the governor finally engages.

These are two mechanisms. v1 fade improved steal *shape* but not *timing* or *gradualism*.

**Gate A feedback (2026-08-23):** Output duck is **out of scope**. Mitch hears a level dip today (from poly cut / fade / emergency — duck is not deployed) and duck would make it worse. The fix is a **more responsive, proactive governor** that reacts to **load ramp-up**, not only absolute thresholds.

**Product requirement:** under overload, reduce poly **early and smoothly** as load rises — before crackle and before an obvious cliff. Recovery stays slow and musical.

**Non-goals:**

- Output amp duck / master attenuation overlay (rejected at Gate A).
- Replacing buffer-size / latency work (V3, V9, V12).
- Upstream Surge `uber_release` rewrite.
- Per-patch unison reduction (separate lever).

---

## What you hear today (not duck)

There is **no duck layer shipped**. The level dip is from:

| Cause | What it sounds like |
|---|---|
| **Spike / emergency** poly step (−4 or 9→3) | Obvious thinning / drop |
| **Deferred limit + emergency note-offs** (fade v1) | Notes dying; can read as a dip |
| **Crackle** (gov too late) | Then governor fires → double failure |

v2 targets the **controller logic**, not the amp path.

---

## Why v1 fails structurally

From measurement canon + code review:

1. **Discrete thresholds.** warm 48 / high 50 / spike 78 / emergency 90 — binary regions with step sizes (−2, −4). Crossing spike is already late on Pi 5 with ported Pi 4 numbers.
2. **Level-only input.** No **rate-of-rise** term — a fast ramp from 55→85% looks the same as a slow crawl until each threshold fires.
3. **Reactive cadence.** 150 ms poll + 150 ms high hold ≈ 14 periods @ 128×2 before sustained high actuation.
4. **Wrong meter.** Proc CPU proxy; governor explicitly rejects smoothed UI meter but still ignores JACK deadline fill.
5. **Polylimit is not a load knob.** Lowering limit does not reduce DSP on sounding notes until note-on or fade release ([Task C](../../docs/measurements/poly-governor-instrumentation-2026-08-21.md)).

---

## Post Gate A — always-on jack model (Pi 5 ear tune 2026-08-23)

**Supersedes progressive as Pi 5 shipping candidate** until full Gate B. Threshold-gated `progressive` mode remains for A/B.

Ear A/B on Pi 5 showed:

- **Jack `dsp_percent`** — no crackle under overload; held voices too aggressively when pegged at 100% at idle.
- **Proc fallback** — crackle at orange header CPU; governor engaged late.

**Pivot:** Governor always on; jack deadline primary; loosen via **baseline offset**, not proc meter or soft-start disable.

```
stress = raw_jack − baseline          # linear; baseline raise = looser hold
target = always_on_curve(stress)      # smoothstep 0→hard; min_headroom at rest
emergency = xruns only (default)
```

| Env | Pi 5 tuned | Role |
|---|---|---|
| `MPE_POLY_LIMIT_MODE` | `always_on` | No SOFT_START gate |
| `MPE_POLY_GOVERNOR_METER` | `jack` | No proc fallback on peg |
| `MPE_POLY_JACK_BASELINE` | `96` (ear) | Idle loosen dial |
| `MPE_POLY_MIN_HEADROOM` | `3` | Rest voices = ceiling − 3 |
| `MPE_POLY_EMERGENCY_XRUN_ONLY` | `1` | No load≥90 cliff |
| `MPE_POLY_FLOOR` | `4` | Required (breaks 64/64 parity) |

Full chronology: [`poly-governor-v2-always-on-pi5-2026-08-23.md`](../../docs/measurements/poly-governor-v2-always-on-pi5-2026-08-23.md).

---

## Design — ramp-aware continuous governor

Replace the threshold **step table** with a **continuous target curve** plus **rise-rate anticipation**. Keep v1 fade as the actuation tail.

```
load sample (jack dsp preferred)
    │
    ├─► continuous target_limit(load, dLoad/dt)   ← replaces warm/spike/high steps
    │
    ├─► rate-limited apply (−1 voice / 250 ms down; slow up)
    │
    └─► fade actuation (deferred limit + emergency note-offs) — unchanged
```

### 1 — Load signal

| Priority | Source | Notes |
|---|---|---|
| 1 | `dsp_percent` in `/run/mpe/meter.state` | Peak meter samples `jack_cpu_load` @ 5 Hz — deadline-aligned |
| 2 | Raw proc/OSC (current path) | Fallback only |

Env: `MPE_POLY_GOVERNOR_METER=auto|jack|proc` (default `auto`).

Extend `mpe-peak-meter` to publish `dsp_percent=NN.N`. Governor reads one tmpfs file — **no fork in the poll loop**.

### 2 — Continuous target (replaces solid thresholds)

Map load → target poly limit with a smooth curve between ceiling and floor:

```
effective_load = load + rise_bias(dLoad/dt)

load ≤ SOFT_START     →  target = ceiling
load ≥ HARD           →  target = floor
else                  →  target = ceiling - (ceiling - floor) * smoothstep(load)

smoothstep = 3t² − 2t³   (Hermite — no slope discontinuity at knees)
```

Defaults (Pi 5 — **must re-tune**, not Pi 4 ports):

| Env | Default | Role |
|---|---|---|
| `MPE_POLY_LIMIT_SOFT_START` | `58` | Load % where limit begins sliding |
| `MPE_POLY_LIMIT_HARD` | `82` | Load % where target hits floor |
| `MPE_POLY_LIMIT_FLOOR` | from `poly_floor()` | Unchanged |

**Legacy mode:** `MPE_POLY_LIMIT_MODE=legacy` restores v1 warm/spike/high/emergency table for A/B.

### 3 — Ramp-up / proactive term (new)

Act on **how fast load is rising**, not only where it is.

```
dLoad/dt = (load_now - load_prev) / delta_t     # %/s, clamped window ≥ 50 ms

rise_bias = clamp(dLoad/dt / RISE_FULL_RATE, 0, 1) * RISE_BIAS_MAX

effective_load = load + rise_bias
```

| Env | Default | Behavior |
|---|---|---|
| `MPE_POLY_RISE_ENABLE` | `1` | Master switch for rise term |
| `MPE_POLY_RISE_FULL_RATE` | `40` | dLoad/dt (%/s) that adds full bias |
| `MPE_POLY_RISE_BIAS_MAX` | `12` | Max virtual load points added to curve input |

**Intent:** a chord that ramps 50→75% in 200 ms gets limit pressure **before** absolute HARD. A steady 60% hold does not accumulate bias.

Optional **xrun nudge:** if `xruns` delta > 0 in `meter.state`, add fixed +8 to `effective_load` for one poll window (immediate bias, no duck).

### 4 — Adaptive poll cadence

| Condition | Poll interval |
|---|---|
| `dLoad/dt > 0` or `load > SOFT_START` | **50 ms** (20 Hz) |
| else stable below soft start | **150 ms** (current) |

Env: `MPE_POLY_POLL_FAST_S=0.05`, `MPE_POLY_POLL_SLOW_S=0.15`.

**CPU budget:** governor loop is Python; 20 Hz only while load is elevated. At rest stays 6.7 Hz.

### 5 — Rate-limited application (down fast-in-steps, up slow)

Inherited from v1 fade work — this is what makes degradation **gradual**:

| Direction | Rule |
|---|---|
| **Down** | Max −1 voice per `MPE_POLY_LIMIT_STEP_INTERVAL_S` (default **0.25 s**) |
| **Up** | +1 voice after `MPE_POLY_LIMIT_RECOVER_HOLD_S` (default **5 s**) below SOFT_START |

Never OSC polylimit below `active_voice_count` except emergency (fade deferred path).

### 6 — Emergency (unchanged semantics, tighter trigger)

When `effective_load ≥ EMERGENCY` (default **90**) or xrun storm: jump toward `poly_emergency()` with fade note-offs if needed. Emergency is last resort, not the main curve.

### 7 — Patch warm preempt (keep, tune)

Keep post-patch-change warm window but drive it from **continuous target** instead of a fixed −2 at warm threshold — e.g. if load > SOFT_START within 4 s of patch change, apply one rate-limited step down early.

---

## Rejected at Gate A

| Approach | Why |
|---|---|
| **Output duck** | Does not reduce callback CPU; audible level dip Mitch rejects; wrong lever |
| Refuse note-ons | Wrong for performance instrument (V7) |
| Lower v1 thresholds only | Earlier cliff, same artefacts |
| Faster polylimit-only polling without curve | Still discrete steps |
| Smoothed UI meter for control | Governor already avoids this — lags on rise |

---

## Acceptance criteria

### Automated

| ID | Criterion |
|---|---|
| A1 | `effective_load` rises with dLoad/dt when RISE_ENABLE=1 |
| A2 | Continuous target monotonic in load; smooth at SOFT/HARD |
| A3 | Rate limiter: max −1 voice per STEP_INTERVAL on step load jump |
| A4 | Adaptive poll: 50 ms when rising, 150 ms when stable low |
| A5 | `legacy` mode matches v1 behavior (regression on `test_surge_poly_governor.py`) |
| A6 | `dsp_percent` in meter.state; parsers ignore unknown keys |
| A7 | No fork added to governor poll loop for jack meter |

### Ear (Gate B — Mitch)

| ID | Criterion |
|---|---|
| B1 | Same overload gesture: **crackle reduced** vs v1 @ Pi 5 128×2 |
| B2 | Degradation **gradual** — no obvious single-step level drop before crackle |
| B3 | Clean play (Cloud Horn @5) does **not** engage limit |
| B4 | Recovery does not pump |

### Platform

| ID | Criterion |
|---|---|
| P1 | Pi 5 SOFT/HARD/RISE tuned from measurement, not Pi 4 78/68 |
| P2 | Positive control: synthetic overload engages limit **before** xrun storm |

---

## Project structure

```
patch_browser/
  surge_poly_governor.py      # v2 loop; legacy mode retained
  governor_load.py            # NEW — meter.state dsp + proc fallback + dLoad/dt
  poly_voice_tracker.py       # fade tail (unchanged)
native/mpe-peak-meter/
  mpe-peak-meter.c            # publish dsp_percent=
tests/
  test_poly_governor_v2.py    # NEW
config/mpe.env.example
Documents/specs/
  poly-governor-v2-progressive-spec.md
```

No `governor_duck.py`, no `patch_loader` choke-point changes.

---

## Environment variables

```bash
# Mode
MPE_POLY_GOVERNOR_V2=1
MPE_POLY_GOVERNOR_METER=auto        # auto | jack | proc
MPE_POLY_LIMIT_MODE=progressive     # progressive | legacy

# Continuous curve
MPE_POLY_LIMIT_SOFT_START=58
MPE_POLY_LIMIT_HARD=82

# Ramp-up / proactive
MPE_POLY_RISE_ENABLE=1
MPE_POLY_RISE_FULL_RATE=40          # %/s → full bias
MPE_POLY_RISE_BIAS_MAX=12           # virtual load points

# Rate limits
MPE_POLY_LIMIT_MAX_STEP_DOWN=1
MPE_POLY_LIMIT_STEP_INTERVAL_S=0.25
MPE_POLY_LIMIT_RECOVER_HOLD_S=5.0

# Adaptive poll
MPE_POLY_POLL_FAST_S=0.05
MPE_POLY_POLL_SLOW_S=0.15

# Emergency + fade (v1)
MPE_POLY_CPU_EMERGENCY=90
MPE_POLY_GOVERNOR_FADE=1
```

Commented in `config/mpe.env.example`. **`player-env-parity.env` only after Pi 5 tune + Gate B.**

---

## Implementation phases (post Gate A)

| Phase | Deliverable |
|---|---|
| **1** | `governor_load.py` + `dsp_percent` in peak meter |
| **2** | Continuous target + rise bias + adaptive poll in governor |
| **3** | Rate-limited apply wired to fade; legacy flag |
| **4** | Pi 5 threshold tune + result doc → Gate B |
| **5** | Promote v2 default in parity env |

---

## Future work (out of v2.0)

- **Predictive ceiling** from V7/V8 per-patch capacity tables (pre-limit before first note of heavy patch).
- **Optional output duck experiment** — off by default, separate spec, only if ramp governor alone fails B2.

---

## Open questions (Gate A)

1. **RISE_BIAS_MAX 12** — enough lead time, or too aggressive on legato swells?
2. **Xrun nudge +8** — include in Phase 2 or ear-test first without?
3. **Disable fade temporarily for A/B?** Helps isolate controller vs actuation — Mitch call.

---

## Gate A checklist (Mitch)

- [x] Duck removed — ramp-aware limit-only path matches intent
- [x] Rise-rate + continuous curve (not solid thresholds) matches intent
- [x] Legacy v1 mode for A/B is enough
- [x] Approve spec → Status: **Approved**

*Last updated: 2026-08-23 19:47 (America/Toronto)*
