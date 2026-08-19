# Looper MIDI-in → OSC-out latency — 2026-08-19

**Criterion 42.** Measure worst-case MIDI-in → OSC-out latency on the Pi.
Produces numbers; does not assert a threshold.

## Method

Tool: `scripts/sooperlooper/measure_midi_osc_latency.py`

```sh
# merged looper session must be running; APC connected
python3 scripts/sooperlooper/measure_midi_osc_latency.py --samples 200
```

The harness runs the APC bench with `--measure-latency N`. It timestamps each
pad-down and records the delta to the next footswitch ``/hit`` OSC send — the
actual MIDI→OSC path, not a synthetic timer loop.

For HUD-thread overhead comparison, run with the merged session normally
running vs `--bench-only` (HUD thread off). Do not use `--hud-only` for latency
A/B — that mode is HUD writer without bench, not "HUD disabled."

## Results

| Condition | n | p50 | p99 | max |
|---|---:|---:|---:|---:|
| Pi live | — | — | — | — |

*Fill after appliance soak.*
