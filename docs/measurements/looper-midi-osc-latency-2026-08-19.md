# Looper MIDI-in → OSC-out latency — 2026-08-19

**Criterion 42.** Worst-case MIDI-in → OSC-out latency on the appliance, with the HUD
thread running and stopped. Produces numbers; asserts no threshold.

## Result

`raspberrypi2`, `1024 x 3`, merged `looper-session`, n = 100 samples per condition.

| condition | n | p50 | p99 | max |
|---|---:|---:|---:|---:|
| HUD thread **on** (merged session) | 100 | 0.188 ms | 0.835 ms | 5.575 ms |
| HUD thread **off** (`--bench-only`) | 100 | 0.187 ms | 2.202 ms | 2.302 ms |
| HUD **on**, under audio load | 100 | 0.201 ms | 0.723 ms | 0.840 ms |

**No measurable HUD-thread penalty.** p50 is identical to three decimal places across
conditions. The p99 ordering is *inverted* — worse with the HUD off — which is not a
result a real effect can produce, so the tail spread is noise at this sample size, not
signal. Under audio load the tail is the tightest of the three.

The concern criterion 42 exists for was GIL contention: the bench polls at ~2 ms while
the HUD writes files and samples health at 2 Hz in the same interpreter. At a p50 of
0.19 ms and a p99 under 1 ms, the HUD thread is not on the MIDI path in any way that
costs the player anything.

## Method

The bench is run with `--measure-latency N`. It stamps every APC MIDI edge and records
the delta to the next `/hit` OSC send, discarding a stamp that no send followed inside
100 ms. Pad presses are **synthetic** — a virtual ALSA port connected to the bench's
MIDI input:

```sh
# on the Pi, with mpe-sooperlooper running
python3 scripts/looper-session.py --measure-latency 100 > /tmp/lat.txt 2>&1 &
python3 /tmp/synthpad.py 180        # rotates note 0..7, ~0.65 s apart
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
  5.575 ms max in the HUD-on idle run is one sample.
- Measured at `1024 x 3`. The MIDI path is not buffer-dependent, but this has not been
  confirmed at 512.
