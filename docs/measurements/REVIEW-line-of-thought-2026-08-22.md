# Review of the current line of thought — before compaction

Two findings. The second is more important than anything currently queued.

---

## 1. P7 is measuring the right thing with the wrong statistic

`measure-plan-p7.sh` reads **`dsp_p99`** as primary, over 45 s windows, 3 runs, 2 patches.

**p99 is a tail statistic answering a central-tendency question.** P7 asks *"does DSP cost
scale inversely with clock?"* That is a question about **how long the compute takes**, which
is `dsp_med`. p99 is about scheduling tail behaviour — a different question, and the reason
the windows have to be long.

Sample-count arithmetic at 1024 frames (21.3 ms/period):

| window | periods | p99 tail samples | median |
|---|---|---|---|
| 45 s | ~2100 | ~21 | rock solid |
| 25 s | ~1170 | ~12 (thin) | rock solid |

`dsp_med` is already emitted by `measure-latency-run.sh` alongside `dsp_p99`/`dsp_max` — no
harness change needed, just read the column that answers the question.

### Shortest useful version

- **Primary statistic `dsp_med`**; keep p99/max recorded as secondary, not as the readout.
- **25 s windows**, not 45.
- **Keep 3 runs per cell** — between-run variation on this box is restart-to-restart and
  cannot be recovered from within-window variance.
- **Patches: Crystals @ 3 (oscillator-bound) + Duduk @ 3 (filter-bound).** That two-cell
  contrast *is* the experiment. **Cloud Horn is a third oscillator-bound point** — it adds a
  confirmation, not a contrast, and can be dropped or made optional.
- xruns should be 0 everywhere at these confirmed-clean counts. That is a **sanity check that
  the run was valid**, not the measurement. Windows do not need xrun-hunting length.

**2 patches x 3 runs x 2 phases x ~45 s/run = ~9 min + 2 reboots ~= 13 min** — shorter than
the current 17 min *and* it includes the filter cell the current script lacks entirely.

---

## 2. The queue has drifted off the actual goal

Restated mid-arc: **"reducing instrument-only playing to the lowest possible latency."**

**P7 and P8 do not buy latency. They buy polyphony headroom**, which converts to latency only
if it is then spent on a smaller buffer. **Buffer size is the direct lever. Compute is the
indirect one.** We are currently queued to spend an evening on the indirect one.

### The arithmetic nobody has put together

`V1-VERDICT-no-fixed-cost-2026-08-21.md:18` measured the fixed per-callback cost:
**a = 0.13 ms.**

| buffer | period | fixed cost as % of deadline |
|---|---|---|
| 1024 | 21.3 ms | **0.6%** |
| 512 | 10.7 ms | **1.2%** |
| 256 | 5.3 ms | **2.5%** |

Fixed cost is negligible at every buffer. **So DSP load *fraction* is close to
buffer-independent — and therefore so is the voice ceiling.** Halving the buffer halves the
work and the deadline together.

**This is not just theory.** `V9-REVIEW-2026-08-22.md:75` — Crystals @ 512x3: sustained clean
**3**, overrun @ 5. Crystals @ 1024 is confirm-verified clean at **3**. *Same ceiling, half
the deadline.*

### What that is worth

| config | total latency |
|---|---|
| 1024x3 | 64.0 ms — what shipped |
| 1024x2 | 42.7 ms — Gate 1, tonight's soak |
| 512x3 | 32.0 ms |
| **512x2** | **21.3 ms** |
| 256x3 | 16.0 ms |

**512x2 is half of what tonight's soak is validating, and the evidence says it may cost
nothing in polyphony.** P7's overclock buys ~11% of compute. Dropping 1024x2 -> 512x2 buys
**50% of the latency**. These are not the same size of prize.

### Why this was abandoned, and why that reason is now dead

"Crackle at 512" drove the retreat to 1024. **The poly governor was ON and stealing sounding
voices during every one of those tests** — confirmed later by ear (governor off -> pops gone).
The governor is off now. **The reason 512 was abandoned has been independently refuted and
never re-tested.**

### Caveats — state them, do not skip them

- The 512 evidence is **ramp-derived**, and V10-b proved the ramp under-counted xruns. It is
  **screening-grade and probably optimistic.** It must be redone on the confirm harness.
- Smaller buffers shrink the cushion in absolute ms — but W1 established **every xrun on this
  appliance is a graph overrun, not an ALSA underrun; the ring has never drained.** The risk
  of a smaller buffer is the shrinking *deadline*, not the shrinking cushion. The ceiling
  question is therefore the correct question to ask.
- One voice of headroom matters more at 512 than at 1024, because the deadline is tighter.
  Confirm at the known-clean counts, do not assume the ceiling transfers.

### Proposed cell — V11

Confirm harness, governor off, stock 1800 MHz. **512x2 and 256x3** at confirm-verified counts
(Crystals @ 3, Cloud Horn @ 5, Duduk @ 3). Identical shape to V9-d, which is already written
and known to work. **~15 min.**

**Run this before P8, and arguably before P7.** If 512x2 holds, it is the largest single win
in the entire arc and it costs nothing but a config change — no rebuild, no overclock, no
thermal risk, no C++.

---

## Standing corrections carried forward

- **Unison theory is dead, twice.** `param0` on Twist(10)/String(9) is an **engine selector**,
  not a unison count. Crystals = engines 4/4/6, unison 1. The census's `unison_voices` column
  is fabricated — see `HANDOVER-census-unison-fix.md`. Do not rebuild a cost theory on it.
- **Oscillator count does not predict cost.** Duduk is floor-3 class on **one** unmuted
  oscillator with filters 11/20. `filter1 >= 10` on 12/53 patches (23%) vs any Twist on 2/53
  (4%). **Filters are the more common cost centre.**
- **Ramp-derived ceilings are screening only** unless taken after the V10-b fix. Never use the
  ramp for a before/after comparison.
- **The recurring failure mode on this appliance is an instrument that reads clean when it is
  blind** — V8-b auto-pick, peak-meter shutdown, V10-b ramp probe, census unison column. Four
  occurrences. Check the instrument before trusting the reading.
