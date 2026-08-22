# Poly governor instrumentation — 2026-08-21

**Status:** implemented on branch `yolo/poly-governor-instrumentation`. **Defaults unchanged.**
**Governor remains disabled** for measurement (`MPE_POLY_GOVERNOR=0` on the Pi during Plan V).

## What was logged

### Startup (once per service start)

```
poly-governor: startup enabled=0 floor=4 poll=0.15 emergency=90.0 spike=78.0 high=50.0 warm=48.0 low=40.0 high_hold=0.15 low_hold=5.0 step_down=2 step_down_spike=4 step_down_warm=2 step_up=1
```

Every threshold and step constant is discoverable from the journal without reading source.

### Poly-limit transition (state change only)

```
poly-governor: 16 -> 14  reason=high  cpu=61.2 raw=64.8  patch="Lead"  held=0.30s
```

Fields: old limit, new limit, reason (`high` / `spike` / `emergency` / `warm` / `recover`),
blended `cpu`, `raw_percent`, patch name, hold time before acting.

### Spam guard (oscillating controller)

If more than 10 transitions land in one second, individual lines are suppressed and a
single summary is emitted on the next window roll:

```
poly-governor: log-spam summary suppressed=12 transitions in 1.0s
```

This prevents a miscalibrated threshold (below baseline load) from turning a tuning bug into
an I/O problem: at 0.15 s poll, per-tick journal logging would be ~400 unbuffered syscalls/min
feeding journald → SD → IRQ 41.

### Verbose trace (optional, tmpfs only)

Set `MPE_POLY_GOVERNOR_VERBOSE=1` to append high-rate diagnostics to
`/run/mpe/poly-governor.trace` (tmpfs via `RuntimeDirectory=mpe`). **Never journal.**

## Env vars added (defaults = shipped constants)

| Env var | Default | Meaning |
|---|---|---|
| `MPE_POLY_POLL_INTERVAL_S` | `0.15` | Governor poll interval |
| `MPE_POLY_CPU_EMERGENCY` | `90.0` | Emergency slam threshold (%) |
| `MPE_POLY_CPU_SPIKE` | `78.0` | Immediate step-down threshold (%) |
| `MPE_POLY_CPU_HIGH` | `50.0` | Sustained high threshold (%) |
| `MPE_POLY_CPU_WARM` | `48.0` | Post-patch warm preempt threshold (%) |
| `MPE_POLY_CPU_LOW` | `40.0` | Recovery threshold (%) |
| `MPE_POLY_CPU_HIGH_HOLD_S` | `0.15` | Hold before step-down |
| `MPE_POLY_CPU_LOW_HOLD_S` | `5.0` | Hold before step-up |
| `MPE_POLY_PATCH_WARM_WINDOW_S` | `4.0` | Warm preempt window after patch load |
| `MPE_POLY_STEP_DOWN` | `2` | Normal step-down (voices) |
| `MPE_POLY_STEP_DOWN_SPIKE` | `4` | Spike step-down (voices) |
| `MPE_POLY_STEP_DOWN_WARM` | `2` | Warm preempt step-down (voices) |
| `MPE_POLY_STEP_UP` | `1` | Recovery step-up (voices) |
| `MPE_POLY_GOVERNOR_VERBOSE` | `0` | High-rate trace to tmpfs (not journal) |

Existing vars unchanged: `MPE_POLY_GOVERNOR`, `MPE_POLY_CEILING`, `MPE_POLY_FLOOR`,
`MPE_POLY_EMERGENCY`, `MPE_POLY_GOVERNOR_HEADROOM`.

**With no env vars set, behaviour matches pre-instrumentation constants exactly.**

## Task A2 — CPU affinity (fixed)

**Pre-existing defect:** `surge-poly-governor.service` declared no `CPUAffinity`, so the daemon
floated onto all four cores — including CPU2–3 where `mpe-jackd` (FF 70) and `surge-xt-cli`
(FF 65) run. At `SCHED_OTHER` it cannot preempt them, but it competes for cache between
callbacks.

**Fix applied:** `CPUAffinity=0 1` on `surge-poly-governor.service` (both non-audio cores).
Also added `RuntimeDirectory=mpe` for tmpfs verbose trace. This gap is unrelated to the
logging work; it was exposed by the same “what runs where” survey that found the CPU census
gaps.

## Report only — units with no CPUAffinity (not fixed this pass)

Global `CPUAffinity` in `system.conf` is unset, so every unit defaults to all four cores.
Only these declare an override today:

| Unit | CPUAffinity |
|---|---|
| `mpe-jackd.service` | `2 3` |
| `surge-xt-cli.service` | `2 3` |
| `mpe-peak-meter.service` | `2 3` |
| `mpe-sooperlooper.service` | `2 3` |

**All other units — no affinity declared (can land on audio cores):**

| Unit | Notes |
|---|---|
| `surge-poly-governor.service` | **fixed this pass** → `0 1` |
| `midi-clock-in.service` | periodic |
| `midi-clock-out.service` | periodic |
| `surge-watchdog.service` | periodic |
| `sl-watchdog.service` | periodic |
| `touch-patch-browser.service` | UI + patch load |
| `patch-browser.service` | OLED UI |
| `mpe-session-publisher.service` | 0.5 s snapshot |
| `mpe-looper-session.service` | HUD + bench |
| `mpe-pressure-remap.service` | MIDI path |
| `mpe-cpu-governor.service` | cpufreq |
| `mpe-irq-affinity.service` | boot-time IRQ move |
| `mpe-splash.service` | power-off splash only |
| `mpe-audio-profile-sync.service` | oneshot |
| `boot-animation.service` | boot only |
| `touch-boot-animation.service` | boot only |
| `foot-pedal.service` | input |
| `mic-to-uac2-bridge.service` | USB host |
| `usb-audio-gadget.service` | USB gadget |
| `uac2-stall-watchdog.service` | USB host |

**Structural fix (deferred):** set global default `CPUAffinity=0 1` in `system.conf` and let
audio units override to `2 3`. Needs its own change with touch-browser responsiveness check —
constrains everything including patch loading.

## Task C — what Surge does on a poly-limit drop

**Evidence:** Surge XT source (`src/common/SurgeSynthesizer.cpp`, `src/common/dsp/SurgeVoice.cpp`,
`src/common/dsp/modulators/ADSRModulationSource.h`), upstream manual, GitHub issue #2498.

### 1. Fade or hard-cut?

**Not an instant hard mute.** When voice stealing runs, Surge calls `softkillVoice()` which
invokes `SurgeVoice::uber_release()` — that sets the amp envelope to `s_uberrelease` state
(`scalestage = output; phase = 1`) and clears the gate. The voice **continues processing**
through its release envelope; `enforcePolyphonyLimit()` only `freeVoice()`s voices already in
`uberrelease`.

**However:** `uber_release` is a fast forced release, not a gentle fade to silence. On a held
note at full level, triggering release from peak can produce an **audible discontinuity**
(step into release slope) — consistent with Mitch’s “quick pops on chords.” The class docstring
claim “Surge softkill, not MIDI note-offs” is **partially true** (no MIDI note-off panic), but
**misleading** about audibility: it is still an abrupt voice termination path.

### 2. OSC polylimit drop while notes are sounding

`setParameter01()` for `polyphony_limit` updates storage; **`softkillVoice()` is not called
on parameter change**. Enforcement runs on **note-on** (`play()` path ~line 777): excess
voices above the new limit trigger `softkillVoice()` in a loop, then `enforcePolyphonyLimit()`.

So lowering `/param/global/polyphony_limit` via OSC **does not immediately cull** sounding
voices — the steal happens on the **next note-on** that would exceed the lowered ceiling.
The pop Mitch heard on chords is therefore: governor lowered limit → next chord note-on →
Surge steals oldest/released voice via `uber_release`.

### 3. Lower only at note-off boundaries?

**Not supported by Surge today.** The engine enforces on note-on, not on parameter change
boundaries. A governor-side fix would need either:

- Never set OSC limit below currently-sounding voice count (requires a voice-count query Surge
  exposes on the Poly display but not clearly via OSC in our stack), or
- Accept note-on-triggered steals and tune thresholds so limit rarely drops under load.

Upstream issue #2498 notes per-scene polylimit doubling in dual-scene modes — another confound
for any voice-count comparison.

### Steal order (for future policy work)

`softkillVoice()` priority: **in-release (oldest release age) first**, else **oldest gated
playing voice**. Matches the prompt’s “released/in-release first, then oldest” ordering.

**No fix implemented this pass** — actuation layer TBD after V7 capacity curve.

## Open calibration question (V7)

Measured normal DSP/process CPU at 1024×3 ≈ **58.9%**. Shipped `MPE_POLY_CPU_HIGH` default
**50.0%**. If these quantities are comparable, the governor sits in near-permanent step-down
during ordinary playing, with recovery threshold 40% rarely reached — explaining continuous
voice cuts better than steal-policy tuning alone. **Do not retune until V7 capacity curve
runs.**

## Verification

- Unit tests: `tests/test_surge_poly_governor.py` (hysteresis, logging, spam guard, defaults)
- Unchanged limit: no `print()` on tick (guard before f-string)
- No subprocess forks added to 0.15 s loop
