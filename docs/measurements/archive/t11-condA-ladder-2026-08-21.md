# T11 — condition A buffer ladder (2026-08-21)

`~/t11-condA.log`. Condition A (synth only — no sooperlooper, session or watchdog),
n=15 per cell, 60 s runs, n=3 periods. Trap-5 assert fired and passed on every cell
(`jackd period=N ok`), so the buffer really was applied.

## Result — a cliff, not a slope

| period | deadline | total buffer | xruns/60 s | clean /15 |
|---:|---:|---:|---:|---:|
| 512 (prior) | 10.67 ms | 32 ms | **0.13** | 14/15 |
| 256 | 5.33 ms | 16 ms | **12.1** | 0/15 |
| 128 | 2.67 ms | 8 ms | **677.6** | 0/15 |
| 64 | 1.33 ms | 4 ms | **2776** | 0/15 |

**512 -> 256 is a ~93x regression for a 2x latency gain.** The instrument-only floor on
this device is 512. There is no low-latency instrument mode to ship below it.

## The callback is not the cause, and now that is proven

| period | deadline | callback late p99 | callback late max | max as % of deadline |
|---:|---:|---:|---:|---:|
| 256 | 5333 us | 230 us | 562 us | 10.5% |
| 128 | 2667 us | 242 us | 792 us | 29.7% |
| 64 | 1333 us | 353 us | 917 us | **68.8%** |

**Lateness is nearly constant** — 562 -> 792 -> 917 us, under 2x across the range — while
xruns grow **230x**. And at 64 frames the worst callback in fifteen minutes still landed at
69% of its deadline. **It never missed.** Yet 2776 xruns/min is ~46 per second against 750
periods per second: **6% of periods underran while every callback finished on time.**

A drain that empties the ring while the producer always meets its deadline is not a
producer problem. This is below JACK.

The constant ~500-900 us also identifies itself: it does not scale with the buffer, so it
is a fixed system property (IRQ + scheduler latency, consistent with the 209-320 us
cyclictest floor). It becomes a larger *fraction* as the period shrinks, but it never
becomes the binding constraint.

**This answers T10 for condition A**: not scheduling. The USB path.

## Why the USB path, concretely

The Sound Blaster Play! 3 is **full speed**: one USB frame per millisecond, 48 samples per
frame at 48 kHz. Expressing the ladder in frames rather than milliseconds:

| period | total buffer | in USB frames |
|---:|---:|---:|
| 512 | 32 ms | 32 |
| 256 | 16 ms | 16 |
| 128 | 8 ms | 8 |
| 64 | 4 ms | **4** |

`snd-usb-audio` keeps a queue of URBs in flight; the host controller schedules them a
frame or more ahead. At four frames of total buffering there is essentially no room to
keep that queue populated. The cliff sits exactly where the transport runs out of room,
not where the CPU does.

DSP at 256 was ~33% median — the Pi is not short of compute at any of these sizes.

## What this changes

1. **The instrument-only low-latency goal is blocked by the interface, not by software.**
   Every optimisation still available — the single-client architecture, thread pinning,
   more affinity work — addresses a term that is already not the limit at condition A.
2. **The Scarlett moves from optional to critical path.** It is USB 2.0 high speed:
   125 us microframes instead of 1 ms frames, eight times the scheduling granularity, and
   asynchronous clocking with a feedback endpoint. It is the only lever measured or
   proposed that acts on the term that is actually binding.
3. **512 x 3 condition A (0.13/min, 14/15) is the honest low-latency claim on this
   hardware** — 32 ms, synth only, no looper.

## Two tests that are now worth more than they were

**T7a redirected to condition A.** The cushion hypothesis was written for the looper case.
It matters far more here, and there is a decisive pairing available:

> **128 x 6 (8 ms period budget, 16 ms total) against 256 x 3 (16 ms total).**
> Identical total buffer, identical USB frame count, different period.

If they produce the same xrun rate, **total buffer is the whole story** and period size is
irrelevant — which confirms the transport model and means small periods are reachable by
raising the period count. If 128 x 6 is much worse, period size matters independently.
Add **64 x 8** (10.7 ms total) as the aggressive cell.

**T12 alignment — do not skip it, and fix the gate.** The original gate ("64 fails while
128 passes") assumed a boundary that does not exist; both fail. But **no power-of-two
period aligns to the 1 ms frame** — 256 = 5.33 frames, 128 = 2.67, 64 = 1.33 — so every
cell measured so far straddles frame boundaries. The clean test is:

> **192 x 3 (4 frames exactly, 12 ms total) against 256 x 3 (5.33 frames, 16 ms total).**

If the *smaller, aligned* period beats the larger misaligned one, alignment is a real
mechanism and the whole ladder has been tested on the wrong grid. Also worth **96 x 3**
(2 frames) against 128 x 3.
