# Router-hop latency — classic MIDI plan, phase 0

**Platform:** Raspberry Pi 5 (`pi5`), 2026-08-28. Idle: `mpe-looper-session` and
`mpe-surge` inactive, `surge-watchdog` active. Instrument:
[`scripts/spike-router-hop-latency.py`](../../scripts/spike-router-hop-latency.py).

**Question.** [`docs/CLASSIC-MIDI-PLAN.md`](../CLASSIC-MIDI-PLAN.md) puts a Python
router between controller and Surge for devices that today reach Surge directly.
What does that hop cost? There was a precedent (the ROLI pressure remap already
traverses a Python daemon) but no number.

## Result — hop cost +0.053 ms p50, +0.115 ms p99

200 samples, alternating arms, 100 each.

| Route | p50 | p99 | max |
|---|---|---|---|
| direct (`src → dst`) | 0.024 ms | 0.059 ms | 0.069 ms |
| hopped (`src → forwarder(translate) → dst`) | 0.077 ms | 0.174 ms | 0.198 ms |
| **delta** | **+0.053 ms** | **+0.115 ms** | +0.129 ms |

`translate()` in isolation: **2.87 µs/message** (20 000 note-on/note-off pairs).
So the hop is ALSA transport, not the translation — the pure logic is ~5% of it.

**Verdict: negligible.** 0.053 ms is ~1% of one 64×2 JACK period (5.33 ms) and
two orders of magnitude below where MIDI latency becomes perceptible. Phase 0
passes; the router is not gated on latency.

## Method

Two source ports, both connected once and left alone, alternating which sends:

```
direct   src-direct -----------------------> dst
hopped   src-hop --> forwarder(translate) --> dst
```

Both arms share the receive callback and the clock, so constant costs cancel.
Alternating rather than one block per arm means a CPU burst or governor step
cannot land on a whole arm and be read as the hop.

Dedicated virtual ports throughout: nothing connects to Midi Through, so Surge
hears nothing and the instrument is untouched.

## The first run was wrong, and how it showed

The first version used **one** source port and ran `aconnect` immediately before
each direct sample, disconnecting after. That put ALSA connection setup inside
the direct arm:

| Route | p50 | p99 | max |
|---|---|---|---|
| direct (with connection churn) | 0.036 ms | 0.489 ms | 1.734 ms |
| hopped | 0.084 ms | 0.213 ms | 0.249 ms |

which reported the hop as **-0.276 ms at p99** — the hop apparently making
things faster. A negative cost for adding work is the tell; the direct arm's
max of 1.734 ms against the hopped arm's 0.249 ms is the same fact seen from the
other side. Connection churn is not part of what was being measured. Fixed by
holding both routes open for the whole run, after which both distributions are
tight and the delta is positive at every percentile.

Recorded because the biased numbers looked plausible enough to publish.

## Limits of this result

- **Idle machine.** Surge and the looper session were not running. Under a full
  graph the hop may cost more; this is a floor, not a worst case.
- **Synthetic source.** Virtual ports, not a USB controller — it excludes USB
  and driver latency, which are common to both arms in production anyway.
- **One message at a time**, 8 ms apart. It does not characterise a dense chord
  or a pitch-bend sweep, where the router emits several messages per input.
  Bend broadcasts to every active member channel, so worst-case fan-out is
  15 output messages for one input — unmeasured.
