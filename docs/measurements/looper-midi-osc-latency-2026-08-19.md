# Looper MIDI-in → OSC-out latency — 2026-08-19

**Criterion 42.** Measures worst-case MIDI-in → OSC-out latency with the HUD
background thread on and off. Produces numbers; does not assert a threshold.

## Method

Tool: `scripts/sooperlooper/measure_midi_osc_latency.py`

| Mode | Command |
|---|---|
| Synthetic (laptop / CI) | `python3 scripts/sooperlooper/measure_midi_osc_latency.py --synthetic` |
| Pi live (APC pads) | `python3 scripts/sooperlooper/measure_midi_osc_latency.py --samples 200` |

Synthetic mode runs 500 iterations of a minimal send hook at the bench's ~2 ms poll
cadence. With `--hud-on`, a background thread performs the HUD file write +
`collect_jack_graph_health()` at 2 Hz (same work class as the merged session).

Live Pi mode times rtmidi callback → next footswitch OSC send while
`mpe-looper-session.service` is running.

## Results (synthetic, nerdrack 2026-08-19)

| Condition | n | p50 | p99 | max |
|---|---:|---:|---:|---:|
| HUD off | 500 | 0.005 ms | 0.028 ms | 0.182 ms |
| HUD on | 500 | 0.005 ms | 0.017 ms | 0.124 ms |

Synthetic harness shows no measurable HUD-thread penalty at p99. **Pi live numbers
(with real APC MIDI and SooperLooper OSC) still required** — run the live command
during the next appliance soak and append a row here.

## Notes

- Criterion 41 collapsed bench + HUD to one OSC listen port (9953); latency
  measurement is unchanged in methodology.
- If live Pi p99 with HUD on exceeds bench-only by >1 ms, move HUD file I/O and
  health sampling fully off the shared poll path (spec acceptance gate).
