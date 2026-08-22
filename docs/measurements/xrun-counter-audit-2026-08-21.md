# Xrun counter semantics audit (2026-08-21)

*Offline — no Pi time. Feeds [`cushion-model-2026-08-21.md`](cushion-model-2026-08-21.md) gate C.*

## What the harness reports as `xruns`

| layer | source | semantics |
|---|---|---|
| **RESULT `xruns=`** | `mpe-peak-meter` → `/run/mpe/meter.state` | Delta of cumulative **`jack_set_xrun_callback`** count over the 60 s window |
| **Per-second table** | same meter | Same counter, sampled each second |
| **Probe `XRUN_COUNT`** | `mpe-xrun-probe` | Independent **`jack_set_xrun_callback`** on a passive client — should track engine events |
| **Probe `frames_late_*`** | `jack_frames_since_cycle_start` in process callback | **In-cycle lateness** (µs into the period when probe runs) — **not** an underrun |
| **Probe `jitter_*`** | monotonic inter-callback period error | Wakeup timing — **not** an underrun |
| **Legacy `delay_*`** | deprecated path | Harness marks `(legacy, ignore)` |

**The primary metric is JACK engine xrun notifications, not ALSA `avail` or a driver underrun counter read directly.**

## Code paths (repo)

```
measure-latency-run.sh
  _enable_strict_xrun_reporting  → MPE_JACK_SOFTMODE=0 in /etc/mpe/mpe.env, restart jackd
  _meter_xruns                   → mpe_meter_xruns_read() from meter.state
  _start_xrun_probe              → mpe-xrun-probe (parallel xrun + jitter + frames_late)

native/mpe-peak-meter/mpe-peak-meter.c
  jack_set_xrun_callback(on_xrun) → atomic ++g_xrun_count → meter.state xruns=

native/mpe-xrun-probe/mpe-xrun-probe.c
  jack_set_xrun_callback(on_xrun) → atomic ++g_xrun_count (logged as XRUN_COUNT)
  comment: jack_get_xrun_delayed_usecs is 0 on JACK2/ALSA — no delay magnitude from JACK API

scripts/start-jackd.sh
  -s when MPE_JACK_SOFTMODE=1 (shipping default: softmode)
  no -s when MPE_JACK_SOFTMODE=0 (strict — zombify late clients)
```

## Integrity gates already in place

| gate | status |
|---|---|
| Softmode must be off during measurement | `_enable_strict_xrun_reporting` writes **env file**, not shell export (fixed 2026-08-17 bench bug) |
| Meter must be live | `MPE_PEAK_METER=1`; harness **VOID** if meter stale or xruns go backwards mid-window |
| Journal xrun lines | **Not used** for harness (journal has zero xrun lines on this appliance — documented in `looper_health.py`) |

## What JACK `xrun_callback` actually means (binding term)

On JACK2 + ALSA backend, the engine invokes registered xrun callbacks when the **backend reports an xrun** — typically ALSA playback delay/underrun or a cycle that missed the hardware clock. It is **one counter per engine xrun event**, not per client.

It is **not** the same as:

- “playback buffer empty because producer was 600 µs late” (inferred)
- probe `frames_late` (sub-period callback entry time)
- DSP load % from `jack_cpu_load` (graph CPU time)

**P3 (counter artifact) is not confirmed outright.** The counter reflects real JACK engine xrun events under strict mode. What remains unknown is **which backend condition** fired — genuine drain, clock mismatch, driver delay report, or a classification that does not match our cushion arithmetic.

That gap is exactly why fill-level telemetry (`appl_ptr − hw_ptr` from `/proc/asound/.../status`) is the next measurement: correlate meter increments with buffer trace shape.

## Implications for session data

| finding | effect on interpretation |
|---|---|
| Counter is engine-level, not raw ALSA | Cannot infer buffer fill from xruns alone — supports cushion-model pivot |
| No delay magnitude from JACK on ALSA | Step 2’s 429 µs cyclictest and probe `frames_late_p99` (~300 µs at 256 in Step 4b partial) measure **different quantities** than the xrun event |
| Strict mode during harness runs | Shipping softmode runs may **under-count or behave differently** — measurement windows are not identical to gig config |
| Probe xrun count vs meter | Should match; if they diverge, investigate client registration / meter restart |

## Verdict for gate C

**P3 not eliminated.** Counter semantics are understood and are not a no-op, but they do **not** prove “playback underrun from producer lateness.” Next: **fill telemetry (A)** and **nperiods sweep at fixed period (B)** per cushion model.
