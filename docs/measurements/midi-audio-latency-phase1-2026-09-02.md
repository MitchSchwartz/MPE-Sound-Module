# Phase 1 — Surge's MIDI→audio latency, measured

**Date:** 2026-09-02
**Branch/SHA:** `fix/midi-offset-correct-and-dynamic` @ `8509e3c`
**Harness:** [`scripts/measure-midi-audio-latency.py`](../../scripts/measure-midi-audio-latency.py)

## Actual state at measurement time (Step 7)

```
jackd -R -P70 -d alsa -P hw:0 -r 48000 -p 96 -n 2
device=hw:0  card=KA1  tier=selected  audible=yes
period=96  requested_period=96  periods=2  rate=48000
MPE_JACK_BUFFER=96  MPE_JACK_PERIODS=2  MPE_SURGE_SAMPLE_RATE=48000
MPE_MIDI_QUANTIZE=beat
```

## The cheap check that killed the original design (Step 1)

The plan was to validate the `period × periods` model against
`jack_port_get_latency_range` / `jack_lsp -l`. That is a **tautology**:

```
JACK reports : system:playback_1  = [ 192 192 ] frames
Surge reports: Surge XT:out_1     = [   0 192 ] frames
The model    : 96 × 2             =   192 frames
```

jackd runs with **no `-I`/`-O`**, and both default to `0`. So JACK's figure is
`period × periods` plus zero — the model's own arithmetic handed back. Surge's
port reports the same because Surge never calls `jack_port_set_latency_range`,
so JACK has no knowledge of Surge's internal delay in either direction.

Neither number could confirm or refute anything. Cost of finding out: one
command, before any window was opened.

## Pre-registration

```
Question:      Does MIDI→audio latency equal period×periods, or does Surge add to it?
Claim class:   rate (single-valued latency), n = 30 events, one stream
Premises:      jack_lsp -l is derived, not measured — VERIFIED 2026-09-01
Instruments:   JACK frame counter, both timestamps from the same clock
Conformance:   positive + negative control, this session — PASS (below)
Impossible if: latency ≤ 0, or > 60 ms → harness rejects, does not report
Prediction:    ~8.0 ms total (one extra Surge period), i.e. the offset is ~2× wrong
Falsifier:     4.0 ± 0.5 ms total → model complete, offset already correct
Cheaper check: jack_lsp -l — considered, RUN, and found tautological (above)
Shortest form: 30 events ≈ 35 s. Run at exactly that.
```

## Conformance gate (Step 0)

| control | method | result |
|---|---|---|
| **negative** | input port left unconnected | **PASS** — halted, `exit 2`, no number emitted |
| **positive** | 20.000 ms injected between timestamp and send | **PASS** — recovered **19.979 ms**, error **0.021 ms = 1 sample** |
| **attack bias** | onset taken at two thresholds (3× and 10× noise floor) | **0.000 ms on all 30 trials** — detector carries no attack bias |
| **noise floor** | 0.5 s silence probe | `0.000000` — thresholds fully defensible |

The pilot (Step 1.5) earned its keep: it **halted on trial 2** because the
patch's release tail was still ringing (peak 0.253) after the fixed 0.25 s
settle. A guessed settle would have attributed each onset to the previous note.
Replaced with a measured wait for silence that halts if the tail never decays.

## Result — cell A, period 96

```
n=30   median 3.3125 ms   min 2.8542   max 4.8333   sd 0.3724
attack 0.000 ms on every trial
ALSA underruns in the journal during the window: 0
```

In frames, sorted:

```
137 142 151 152 155 156 156 158 158 159 159 159 159 159 159 159 159 159 159 160
160 160 160 161 164 164 165 168 | 215 232
```

**Median 159 frames.** The distribution is *not* uniform over a period — 28 of
30 trials fall within 137–168 (spread 31 frames), tightly modal at 159, with
two excursions at 215 and 232. Those two sit roughly one period above the mode,
consistent with occasionally missing a callback deadline rather than with
asynchronous arrival phase.

## What this means for the offset

| leg | frames | ms | status |
|---|---|---|---|
| MIDI in → Surge audio out | **159** | **3.31** | **measured here** |
| Surge out → converter | 192 | 4.00 | JACK-declared, hardware term unmeasured |
| USB transfer + DAC conversion | **0** | **0.00** | declared zero — **Phase 2** |
| **total** | **351** | **7.31** | |
| **what the router currently uses** | 192 | 4.00 | |

**The prediction was right and the offset is wrong.** The model omits Surge's
own 159-frame leg entirely, under-compensating by **3.31 ms — an 83% error** —
and that is a *floor*, since the DAC term is still declared as zero.

## Observer effect (Step 4)

JACK's xrun callback counted 1–4 during each window; `journalctl -u mpe-jackd`
recorded **zero ALSA underruns** across all of them. These are different
instruments — the callback counts graph overruns, the journal counts ALSA
underruns with magnitude — so the probe client perturbed the graph slightly and
nothing became inaudible. Reported either way, per the rule.

Note the reading itself is a **frame delta**, not a rate, so it is not
distorted by the probe's presence the way an xrun count would be.

## Open — needs a decision

**Cell B (period scaling) is not yet run, and it changes config.** Cell A fixes
the offset only for period 96. To make the *dynamic* offset correct at every
period, the scaling of the 159-frame leg has to be known:

- if it is `1.5 × period + c`, then at 256 it is ~399 frames
- if it is a constant 159 frames, the correction is period-independent

These differ by 240 frames (5 ms) at period 256 and cannot be distinguished
from one point. Cell B needs a period change, which restarts the graph and
clears the looper — a **hard stop** pending Mitch's approval.

**Phase 2** (KA1 out → Scarlett 4i4 in, `jack_iodelay`) remains blocked on
physical cabling, and only adds to the 7.31 ms.
