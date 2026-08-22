# Agent prompt — W1: the four-instrument window

Copy everything below the line.

---

## What this is

Every xrun figure this project has produced is an **event count with no magnitude**, from a
callback that **conflates two different failure modes**. Before measuring anything else, we
fix the instrument. W1 does that *and* answers the compute question in the same pass.

**Do not optimise anything. Do not tune anything. This is a measurement task.**

## Read first — do not re-derive or re-litigate

| doc | what it settled |
|---|---|
| `docs/measurements/xrun-counter-audit-2026-08-21.md` | our counter is event-count-only; JACK fires it for **ALSA underruns** *and* **graph overruns** |
| `docs/measurements/cushion-model-2026-08-21.md` | buffer fill is **self-restoring** (`fill(k) = fill_0 + d_0 - d_k`); producer lateness cannot accumulate |
| `docs/measurements/PLAN-2026-08-21-evening.md` | the plan, the four instruments, and the joint readings |
| `docs/measurements/find-600us-2026-08-21.md` | Steps 0-4: swap ruled out, URB rate inverted, cyclictest max 429 us under load |

**Already dead — do not test, do not revisit:** `lowlatency=N`; aligned periods
(240/480/1008); URB queue depth; swap / SD major faults; Sound Blaster IRQ cell 1c.
**Parked:** `irq/30` priority, `isolcpus`, `nohz_full`, PREEMPT_RT — producer-lateness
levers, and the cushion arithmetic says that term is not binding. Do not bundle them in.

## The core arithmetic this window tests

At `1024 x 3` the cushion is 2048 frames = **42.7 ms**. The worst stall ever measured on
this box is **429 us**. Emptying the buffer by producer lateness would need a **single**
42.7 ms stall — **100x larger than anything observed.** Yet xruns are counted.

Either they are not drain events, or something we have never measured is far larger than we
think. **W1 distinguishes these.**

---

## Harness changes required first (offline, no Pi)

Two instruments are missing. Add both. **Instrument 2 is a permanent harness improvement,
not a one-off for this run.**

### Instrument 2 — jackd's own xrun magnitudes

jackd already prints lines of the form `ALSA: xrun of at least 0.123 msecs`. The harness
captures **no jackd stderr or journal anywhere** (verified: `scripts/measure-latency-run.sh`
has no `journalctl` and no stderr capture).

Capture `journalctl -u mpe-jackd --since <window start> --until <window end>` for every
window. Parse out each `xrun of at least N msecs` line. Report **the count AND every
magnitude** (min / median / max / full list if small).

### Instrument 3 — direct fill telemetry

`/proc/asound/card<N>/pcm0p/sub0/status` exposes `hw_ptr` and `appl_ptr`.
**`appl_ptr - hw_ptr` is the buffer fill level in frames.**

Sample at **10-20 Hz** for the whole window; log `timestamp, appl_ptr, hw_ptr, fill_frames,
state`. Use a plain `while` loop reading the proc file — **no per-sample subprocess forks**
(CPU doctrine: no forks in periodic loops on the appliance). Convert to ms and to
percent-of-buffer in post-processing, not in the loop.

**Resolve the card index live from `/proc/asound/cards`.** It moved **6 -> 2** after the
Step 3 reboot. Echo what you resolved before using it. Do not hardcode.

**Sanity-check the poller before trusting it:** at idle with a stream up, `fill_frames`
should sit near the nominal buffer and never exceed `period x nperiods`. If it does, the
pointer arithmetic is wrong (wrap-around) — fix it before running W1.

---

## W1 — the instrumented ladder (~20 min)

**Condition A. Identical load at every buffer size — this is the one variable that must not
move.** Step 2's `dsp_p99 ~= 92%` used `midi-load` while the 34.8% figure did not, which is
why that comparison was invalid. State explicitly in the deliverable which load you used.

| cell | period x nperiods | total latency |
|---|---|---|
| W1-a | 1024 x 3 | 64.0 ms |
| W1-b | 512 x 3 | 32.0 ms |
| W1-c | 256 x 3 | 16.0 ms |

Per cell, capture **all four instruments simultaneously**:

| # | instrument | report |
|---|---|---|
| 1 | probe `XRUN_COUNT` | events per minute |
| 2 | jackd `ALSA: xrun of at least N msecs` | **count and magnitudes** |
| 3 | fill level `appl_ptr - hw_ptr` @ 10-20 Hz | min / median / p1 / max, **and the trace shape** |
| 4 | `dsp_p99` | percent of period deadline |

Also record `throttled=0x0` and `meter_live` per window as usual.

### Report this table

| cell | #1 events/min | #2 ALSA count | #2 magnitudes | #3 fill min | #3 shape | #4 dsp_p99 |
|---|---|---|---|---|---|---|

### Interpretation — state which row you are in, explicitly

| pattern | conclusion |
|---|---|
| #1 = N, **#2 = 0** | **all graph overruns.** The cushion was never in play. This is a **compute** problem; the entire latency-path line of work is a wrong-term pursuit |
| #2 > 0 with magnitudes, #3 drains to match | genuine underruns — the drain model applies and cushion size matters |
| #2 > 0 but **#3 stays flat** | **contradiction — chase it.** Something is misreporting; say so loudly rather than picking a side |
| **#3 flat + #4 near 90%** | **P3 and compute-bound together** — retires the ~600 us line |

### Fill-trace shape (instrument 3)

| trace | model |
|---|---|
| flat, brief sub-ms dips, xruns anyway | **P3** — counter artifact |
| descending sawtooth, resets at each xrun | **P2** — constant clock mismatch |
| random walk wandering to zero, no cadence | **P2'** — rate-matching / feedback noise |
| flat then a single cliff to zero | **P1** — a real giant stall; capture what ran |

### Expected, so you can be surprised properly

`dsp_p99` was **34.8% at 1024** and **63-89% at 256**, both condition A. If W1 reproduces a
steep climb, **256 is compute-bound**: per-callback fixed costs (graph traversal, parameter
smoothing, block setup) do not shrink with the buffer, so they amortise over fewer samples.
At ~11% headroom, graph overruns are expected rather than hypothetical.

**Say so if it does not reproduce.** A flat ladder would mean the 63-89% reading was an
artifact and would put the latency path back in play.

---

## W0 — 30-second check, any idle moment

Does ALSA accept `1024 x 2` on this device at all? `snd-usb-audio` conventionally wants
`nperiods >= 3`; if it refuses, jackd simply fails to start and the answer took 30 seconds.

**If it opens, that is 64.0 ms -> 42.7 ms of shipping latency with no change to the compute
deadline** — Surge still gets 21.3 ms for 1024 frames, exactly as it does today. Report only
whether it opens; do not measure it yet. That is W2.

---

## Rules

1. **One variable per cell.** The load must be identical across W1-a/b/c. If you cannot hold
   it, stop and say so rather than proceeding.
2. **No config, kernel, or priority changes.** W1 is measurement only. `irq/30` stays at
   FF 50.
3. **No commands against the Pi while a window is open** — including read-only ones. Batch
   between windows. Instrument 3's poller is part of the run, not an interruption.
4. **No subprocess forks inside the polling loop.**
5. **Resolve the card index live.** Never hardcode it.
6. **Report n.** Three streams cannot establish distribution shape — Step 4 was misread that
   way. If a claim needs shape, say how many streams support it.
7. If a result refutes something in the docs above, **name the doc and say so plainly.**

## Deliverable

`docs/measurements/w1-instrumented-window-2026-08-21.md`, on a branch off `dev`:

- the four-instrument table, per cell
- **which interpretation row you landed in**, stated explicitly
- the fill-trace shape per cell, with the raw trace kept as an artifact
- the W0 answer (opens / refuses)
- a **"what this retires"** section — every line of work now dead, named
- anything you could not measure, and why

**Do not propose next steps beyond stating what W1's outcome implies.** The plan is in
`PLAN-2026-08-21-evening.md`; W1's job is to decide between its branches, not to add new
ones.
