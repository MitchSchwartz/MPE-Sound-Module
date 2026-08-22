# Agent prompt (yolo) — poly governor: instrument and de-risk, do not retune

Copy everything below the line.

---

## Scope

**Make the governor visible and configurable. Do NOT retune its thresholds, and do NOT
re-enable it.** Calibration is gated on the V7 capacity curve, which has not run.

Target: `patch_browser/surge_poly_governor.py` (259 lines), plus
`patch_browser/surge_cpu_monitor.py` and `patch_browser/surge_playback.py` as needed.

## Why this work exists

Confirmed by ear on 2026-08-21: **the "quick pops on chords from heavy patches" were this
governor.** With it disabled the pops vanish. It was cutting Surge's poly limit under CPU
load, and sounding voices were being removed.

It was **also active with an unset env during every measurement this project has ever run**,
silently varying voice count in response to the CPU load being measured. It confounded W1's
entire DSP ladder. Nobody noticed because **it logs nothing but errors.**

## Leading hypothesis — record it, do not act on it yet

`SurgeCpuMonitor` reads **`/proc` CPU time for the `surge-xt-cli` process**. Surge's audio
thread is essentially the whole process, so that signal tracks DSP load closely.

**Measured normal DSP load at 1024 x 3 is ~58.9%. `CPU_HIGH_THRESHOLD` is 50.0.**

If those are comparable quantities, **the governor's step-down threshold sits below baseline
load** — it would be in near-permanent step-down during ordinary playing, and
`CPU_LOW_THRESHOLD = 40.0` is low enough that it would rarely recover. That explains the pops
far better than steal policy does: it was not protecting against overload, it was cutting
voices continuously.

**Your job is to make this measurable, not to fix it by guessing new numbers.**

## Already present — do not re-add

**Hysteresis exists**: separate `CPU_HIGH_THRESHOLD` / `CPU_LOW_THRESHOLD`, plus
`CPU_HIGH_HOLD_S` (0.15) and `CPU_LOW_HOLD_S` (5.0), and asymmetric `STEP_DOWN` / `STEP_UP`.
An earlier design note proposed adding hysteresis; **it is redundant, strike it.**

---

## Task A — telemetry (highest value, lowest risk)

**Every poly-limit change must be logged**, to the journal, one line each:

```
poly-governor: 16 -> 14  reason=high  cpu=61.2 raw=64.8  patch="<name>"  held=0.30s
```

Include: old value, new value, **reason** (high / spike / emergency / warm / recover /
patch-load / manual), blended `cpu` **and** `raw_percent`, the patch name, and how long the
condition was held before acting.

Also log **once at startup**: enabled/disabled, every threshold and step constant in effect,
and the resolved floor/ceiling. **The governor's configuration must be discoverable from the
journal without reading source.**

### Cost constraints — read before writing any logging code

This daemon runs at `POLL_INTERVAL_S = 0.15` and its unit sets **`PYTHONUNBUFFERED=1`** with
**`StandardOutput=journal`**. That means **every `print()` is an immediate `write()` syscall
with no batching**, and journald then writes to the SD card — which lands on **IRQ 41, shared
with the SDIO WiFi**.

| logging rate | consequence |
|---|---|
| per-tick @ 0.15 s | ~400 lines/min, 400 unbuffered syscalls, continuous journald -> SD churn |
| **state-change only** | a handful of lines/min — noise against the ~16 MiB/60 s of SD writes already present |

**Requirements:**

- **Log on state change only. Never per tick.** Guard the call so an unchanged state does no
  work at all — no string formatting, no f-string evaluation, no allocation.
- **No subprocess forks** (CPU doctrine).
- If anything higher-rate is ever wanted for diagnostics, write it to **`/run/mpe` (tmpfs,
  RAM-backed, zero SD writes)** via `RuntimeDirectory=mpe`, as `mpe-peak-meter.service`
  already does — **not** to the journal. Journal is for state changes only.
- Add a **spam guard**: if transitions exceed a sane rate (say 10/s), collapse to a single
  summary line rather than emitting each. A miscalibrated threshold can make this oscillate,
  and the logging must not amplify that.

### Task A2 — pin the service (pre-existing defect, fix it here)

**`surge-poly-governor.service` declares no `CPUAffinity`.** It floats across all four cores,
**including CPU2 and CPU3 where jackd (FF 70) and Surge (FF 65) run.** It is `SCHED_OTHER` so
it cannot preempt them, but it pollutes their cache and competes between callbacks.
`mpe-jackd` and `surge-xt-cli` both declare `CPUAffinity=2 3`; this one declares nothing.

**Add `CPUAffinity=1`** — off the audio cores, and off CPU0 which carries the unmovable xhci
IRQ. Note in the deliverable that this is a **pre-existing gap unrelated to the logging work**,
and check whether `midi-clock-in`, `surge-watchdog`, `sl-watchdog` and `touch-patch-browser`
have the same omission — report, do not fix them in this pass.

## Task B — make thresholds configurable, keep defaults identical

Move `CPU_EMERGENCY_THRESHOLD`, `CPU_SPIKE_THRESHOLD`, `CPU_HIGH_THRESHOLD`,
`CPU_WARM_THRESHOLD`, `CPU_LOW_THRESHOLD`, `CPU_HIGH_HOLD_S`, `CPU_LOW_HOLD_S`, `STEP_DOWN`,
`STEP_DOWN_SPIKE`, `STEP_DOWN_WARM`, `STEP_UP` to env overrides with the **current values as
defaults**, following the existing `MPE_POLY_*` convention.

**Behaviour must be byte-for-byte identical when no env vars are set.** State that explicitly
in the commit message. Document each in `config/mpe.env.example`, commented out, with the
default noted — matching how `MPE_CPU_GOVERNOR` is documented there.

This lets V7's capacity numbers be applied without a code change.

## Task C — investigate, do not implement: what does Surge do on a poly-limit drop?

`send_polylimit()` sends an OSC float to `POLY_LIMIT_OSC`. **Surge decides which voices die
and how.** The class docstring asserts *"Surge softkill, not MIDI note-offs"* — treat that as
a **claim to verify, not a fact.**

Establish and write up:

1. When `/polylimit` drops below the currently sounding count, does Surge **fade** removed
   voices or **hard-cut** them? A hard cut is a step discontinuity — **that is the pop.**
2. If Surge hard-cuts, is there an alternative that does not (release the voice, let its
   envelope run, and lower the ceiling for new notes only)?
3. Could we avoid the problem entirely by **lowering the limit only at note-off boundaries**,
   so the ceiling never drops below what is currently sounding?

**Report findings. Do not implement a fix in this pass** — the right layer is not yet known,
and it may not be our code.

### Steal policy — for the record, not for implementation

An earlier draft proposed **refusing note-ons** rather than stealing sounding voices.
**That is wrong for a performance instrument** (Mitch, 2026-08-21): the player presses a key
and gets silence, which reads as broken. Voice stealing is standard and expected.
**The problem is the hard cut, not the stealing.** If a policy is ever needed, the order is
**released/in-release first, then quietest, then oldest.**

## Task D — verification

- Unit-test or manually exercise the logging path: force a state change and confirm one line
  per transition, correct fields, **no per-tick spam**.
- Confirm defaults-unchanged: with no env set, the constants resolve to today's values.
- Confirm no added cost in the 0.15 s loop — no forks, no per-tick allocation or formatting.

## Explicit non-goals

- **Do not retune any threshold.** Gated on V7.
- **Do not re-enable the governor.** It is deliberately off; measurement depends on that.
- **Do not implement fade or steal-policy changes.** Task C decides where that belongs.
- Do not touch the audio path, jackd config, or buffer settings.

## Deliverable

Branch off `dev`. Commit the code changes plus
`docs/measurements/poly-governor-instrumentation-2026-08-21.md` containing:

- what was logged and an example line
- the full table of env vars added, with defaults
- **Task C findings** — what Surge actually does on a poly-limit drop, with evidence
- an explicit statement that **defaults are unchanged and the governor remains disabled**
- the threshold-vs-baseline mismatch recorded as an open calibration question for V7
