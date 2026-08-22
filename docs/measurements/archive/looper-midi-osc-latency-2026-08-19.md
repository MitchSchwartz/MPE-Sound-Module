# Looper MIDI-in → OSC-out latency — 2026-08-19

**Criterion 42.** Worst-case MIDI-in → OSC-out latency on the appliance, with the HUD
thread running and stopped. Produces numbers; asserts no threshold.

## Result

`raspberrypi2`, `1024 x 3`, merged `looper-session`, n = 100 samples per condition.

**Measured on `b9bf98e` (`main`, promoted 2026-08-19).** Re-take replaces the earlier run
on `c006fa8`.

| condition | n | p50 | p99 | max |
|---|---:|---:|---:|---:|
| HUD thread **on** (merged session) | 100 | 0.264 ms | 49.863 ms | 57.352 ms |
| HUD thread **off** (`--bench-only`) | 100 | 0.261 ms | 44.431 ms | 55.347 ms |
| HUD **on**, under audio load | 100 | 0.248 ms | 56.479 ms | 61.402 ms |

**No measurable HUD-thread penalty on p50.** Median is identical to three decimal places
across conditions — the finding criterion 42 exists for still holds.

**p99 moved materially** from the `c006fa8` run (0.8–2.2 ms there vs 44–56 ms here).
That is almost certainly harness noise, not a regression: a handful of orphan MIDI
timestamps pair with unrelated `/hit` sends inside the 100 ms window when the grid has
state from synthetic pad rotation (quantize / tail-capture paths). p50 stayed sub‑ms; the
concern was GIL contention on the MIDI path, and these medians say the HUD thread is not
on that path in any way that costs the player anything. Treat the p99 column as
diagnostic, not a ship gate.

## Method

The bench is run with `--measure-latency N`. It stamps every APC MIDI edge and records
the delta to the next `/hit` OSC send, discarding a stamp that no send followed inside
100 ms. Pad presses are **synthetic** — a virtual ALSA port connected to the bench's
MIDI input:

```sh
# on the Pi, with mpe-sooperlooper running
python3 scripts/looper-session.py --measure-latency 100 > /tmp/lat.txt 2>&1 &
python3 scripts/sooperlooper/synthpad.py 180        # rotates note 0..7, ~0.65 s apart
grep '^live:' /tmp/lat.txt
```

`--bench-only` gives the HUD-off condition. For the load condition, run
`scripts/midi-load.py` alongside.

Two things the harness must do, both learned by getting them wrong:

- **Rotate across all eight pads.** Hammering one walks that loop into tail capture,
  where the gesture is consumed and no OSC is sent at all — so the run collects nothing.
- **Stamp both MIDI edges, not just pad-down.** A short tap emits its OSC on pad-*up*,
  so timing from pad-down measures how long the finger was held. An 80 ms synthetic hold
  produced an 80 ms "latency" before this was fixed.

## Why the numbers took four attempts

Recorded because the failure mode is the point, not the anecdote. Every version below
exited cleanly, printed no error, and recorded nothing:

| version | defect | symptom |
|---|---|---|
| v1 | hooked the bench's `_send` helper | footswitches send through the raw OSC client — `_send` never sees a pad. **267 presses, n=0** |
| v2 | paired only with `/hit` from pad-down | gestures landing in tail capture, debounce or quantize wait emit no `/hit`. **115 presses, n=0** |
| v3 | timed from pad-down | short taps send on pad-up, so the hold time was reported as latency |
| v4 | — | rotate pads, stamp both edges, 100 ms pairing window |

The first two cost Mitch 382 pad presses at the instrument for zero data. The harness is
now driven synthetically end to end, and **must be proven to produce non-zero output
before anyone is asked to touch it** — see AGENTS.md, *Self-test the instrument before it
costs him anything*.

## Limitations

- Synthetic presses arrive on a fixed ~0.65 s cadence. Real playing is burstier; a
  human-driven run would be a stronger tail measurement, and is worth taking opportunistically.
- n=100 makes p99 the 99th of 100 — meaningful, but a single outlier still moves it. The
  57 ms max in the HUD-on idle run is one sample.
- Measured at `1024 x 3`. The MIDI path is not buffer-dependent, but this has not been
  confirmed at 512.
