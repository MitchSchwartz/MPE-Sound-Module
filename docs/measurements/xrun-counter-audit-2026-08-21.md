# Audit: what our xrun counter actually counts (2026-08-21)

Offline audit of `native/mpe-xrun-probe/mpe-xrun-probe.c`. No Pi time used.

## What the probe does

```c
jack_set_xrun_callback(g_client, on_xrun, NULL);
...
static int on_xrun(void *arg) { atomic_fetch_add(&g_xrun_count, 1UL); return 0; }
```

It is an **event counter on JACK's xrun callback**. Nothing more.

## Finding 1 — we have no magnitude, only counts

The probe's own header says it:

> `Xrun callback: event count only (jack_get_xrun_delayed_usecs is 0 on JACK2/ALSA).`

**Every "xruns/min" figure produced in this project is an undifferentiated event count.** A
1 us overrun and a 40 ms underrun both increment by exactly 1. We have never had magnitude
data, and the arithmetic in `cushion-model-2026-08-21.md` turns entirely on magnitude.

## Finding 2 — the callback aggregates structurally different events

JACK2 invokes the xrun callback for **at least two distinct conditions**:

| # | condition | did the hardware buffer empty? |
|---|---|---|
| **a** | ALSA returns `EPIPE` — a genuine playback underrun | **yes** |
| **b** | the client graph fails to complete within the period — "client too slow" | **no** |

**These are not the same event and our counter cannot tell them apart.**

**This is the reconciliation the cushion model needed.** A type-(b) xrun requires only that
Surge overran its 21.3 ms compute deadline. **It does not require the 42.7 ms cushion to
drain at all.** The factor-of-100 gap between the 42.7 ms cushion and the 429 us worst
measured stall stops being a paradox the moment type (b) is on the table.

It also resolves the T11 anomaly — *"at 64 frames the callback never missed its deadline
while 6% of periods underran."* The probe measures **inter-callback period jitter and
`frames_since_cycle_start`**, not whether the graph finished computing. "Callback woke on
time" and "graph overran the period" are fully compatible. They were never in tension; we
were reading two different quantities as if they were one.

And it makes the `dsp_p99 ~= 92%` reading at 256 far more alarming: at 92% of deadline, type
(b) events are *expected*, and they would be counted as xruns indistinguishably from drain.

**Caveat:** this is reasoned from JACK2's documented behaviour, not from reading the
installed jackd's source in this session. Confirm against the version on the appliance
before treating the two-condition split as established.

## Finding 3 — jackd already reports magnitudes and we discard them

jackd's own stderr emits lines of the form:

```
ALSA: xrun of at least 0.123 msecs
```

That is a **type-(a) event with a magnitude attached** — exactly what we lack. Grep of
`scripts/measure-latency-run.sh` shows **no capture of jackd's stderr or journal** anywhere
in the harness. This data has existed all along and has been thrown away on every run.

## The discriminator — free, and available from existing runs

For any window, compare:

| probe `XRUN_COUNT` | jackd "xrun of at least" lines | reading |
|---|---|---|
| N | N, with magnitudes | genuine ALSA underruns — the drain model applies |
| N | **0** | **all type (b)** — graph overruns; cushion size is irrelevant |
| N | M < N | mixture — and the split is the number that matters |

If past logs retained the journal, **this can be settled with no new measurement.**

## Actions

1. **Harness change:** capture `journalctl -u mpe-jackd` for the window alongside the probe
   log, and report **both** counts and the ALSA magnitudes. One-line addition; makes every
   future run strictly more informative.
2. **Re-read history:** if the journal survives for T5 / T11 / T13 / the Scarlett runs,
   recompute the split retrospectively.
3. **Reinterpret with care.** Do not retract prior findings wholesale — the comparisons were
   internally consistent, since every cell used the same counter. But **any conclusion that
   depended on xruns meaning "the buffer emptied" is now unsupported**, including parts of
   the runway and alignment reasoning.
4. Keep the fill-level telemetry (`/proc/asound/card<N>/pcm0p/sub0/status`) from
   `cushion-model-2026-08-21.md`. It measures buffer state directly and is immune to this
   entire class of ambiguity.

## Status of P3

`cushion-model-2026-08-21.md` listed P3 ("the counter increments on something that is not an
underrun") as a candidate. **It is confirmed as a real mechanism** — type (b) exists and is
counted. What remains unknown is the **split**: what fraction of our measured xruns are
graph overruns rather than drains. Finding 3 gives that number for free.
