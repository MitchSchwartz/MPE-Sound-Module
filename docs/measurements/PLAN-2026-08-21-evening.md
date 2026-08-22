# Plan — after the cushion model and the counter audit (2026-08-21 evening)

**Supersedes** `Documents/specs/queue-2026-08-21-evening.md` and the remaining cells in
`PROMPT-find-the-600us.md` (Steps 0-3 are done; step 4 is in flight; 4b is dropped).

## Why the plan changed

Two results in the last hour moved the ground:

1. **`cushion-model-2026-08-21.md`** — the ring buffer level is **self-restoring**.
   `fill_after(k) = fill_0 + d_0 - d_k`: it depends on the *current* delay only, not on
   history. So producer lateness cannot accumulate, and emptying a 42.7 ms cushion would
   need a **single** 42.7 ms stall. Worst measured: **429 us**.
2. **`xrun-counter-audit-2026-08-21.md`** — our xrun counter is an **event count with no
   magnitude**, and it **conflates ALSA underruns with JACK graph overruns**. The second
   does not require the buffer to drain at all.

Together: **we do not currently know what our primary metric measures.** Everything else is
downstream of fixing that.

## Phase A — make the metric mean something (do first; mostly free)

| # | task | Pi time | blocks |
|---|---|---|---|
| A1 | Harness captures `journalctl -u mpe-jackd` per window; report ALSA `xrun of at least N msecs` lines **and** magnitudes beside the probe count | 0 (offline) | everything |
| A2 | Retro-check: do journals survive for T5 / T11 / T13 / Scarlett runs? If so, recompute the type-(a)/type-(b) split for free | ~5 min, read-only | — |
| A3 | Confirm the installed jackd's xrun-callback conditions against its actual source/version | ~5 min | the two-condition claim |

**Gate A.** For any past or new window:

| probe count vs jackd ALSA lines | conclusion |
|---|---|
| equal, with magnitudes | genuine underruns — drain model applies, cushion matters |
| **jackd lines = 0** | **all graph overruns** — cushion size is irrelevant; this is a **compute** problem, not a latency-path problem |
| mixture | the split is the number that matters; report it every run from here |

**If Gate A returns "all graph overruns," Phases B and C are unnecessary** and the work moves
wholesale to Phase D. Check before spending Pi time.

## Phase B — observe the buffer directly (~30 min)

Stop inferring buffer state from a counter. Read it.

`/proc/asound/card<N>/pcm0p/sub0/status` exposes `hw_ptr` and `appl_ptr`; their difference
**is** the fill level. Poll at 10-20 Hz, log beside the xrun counter. Resolve the card index
live — it moved 6 -> 2 after the Step 3 reboot.

| # | cell | Pi time |
|---|---|---|
| B1 | fill telemetry, `1024 x 3` cond A | ~15 min |
| B2 | fill telemetry, `256 x 3` cond A | ~15 min |

**Gate B — read the trace shape:**

| trace | model | consequence |
|---|---|---|
| flat, sub-ms dips, xruns anyway | **P3** counter artifact | go to Phase D |
| descending sawtooth, resets at xrun | **P2** constant clock mismatch | rate-matching work; cushion matters |
| random walk down to zero, no cadence | **P2'** feedback/rate-matching noise | control-loop work; cushion matters |
| flat then one cliff | **P1** giant stall | capture what ran; back to RT tuning |

If B1 hasn't started when step 4 runs, **fold the poll into step 4's window** — read-only,
negligible cost, one window saved.

## Phase C — sweep the cushion, not the period (~30 min)

Every cell to date swept **period** at fixed `n=3`, moving deadline and cushion together.
Invert it: **fix period at 1024** (deadline 21.3 ms — the load Surge already meets with zero
xruns) and vary only `MPE_JACK_PERIODS`. No reboot, no kernel change.

| `n` | cushion | total latency | P2 predicts | P2' predicts | P3 predicts |
|---|---|---|---|---|---|
| 2 | 21.3 ms | **42.7 ms** | 2.0x | 4.0x | flat |
| 3 | 42.7 ms | 64.0 ms | baseline | baseline | flat |
| 4 | 64.0 ms | 85.3 ms | 0.67x | 0.44x | flat |
| 6 | 106.7 ms | 128.0 ms | 0.40x | 0.16x | flat |

**Linear vs quadratic vs flat** is a real discriminator — the first test shape in this
project that separates its candidate models by curve rather than by a single comparison.

**`n=2` has never been tested** — every doc is x3, x6 or x8. It may simply refuse to open
(`snd-usb-audio` conventionally wants >=3); that is a 30-second answer. **If it holds, it is
21.3 ms off the shipping latency for free**, with no change to the compute deadline.

## Phase D — the compute question (~15 min, needed regardless)

`dsp_p99 ~= 92%` was recorded at 256 under `midi-load`, against 34.8% (1024) and 37.5% (512)
under the standard 75-voice load. **Different loads — the comparison is not yet valid.**

| # | cell | Pi time |
|---|---|---|
| D1 | DSP p99 at 1024 / 512 / 256, **identical** load, cond A | ~15 min |

**Gate D:**
- **flat ~35% across all three** -> 92% was a load artifact; the latency path stays live
- **climbing toward ~92% at 256** -> **256 is compute-bound.** Per-callback fixed costs
  (graph traversal, parameter smoothing, block setup) do not shrink with the buffer. No
  amount of IRQ or scheduler tuning recovers a buffer with 8% headroom. The levers become
  voice count, patch complexity, or accepting 512/1024 as the floor.

Gate D matters whichever way Gate A lands, and it directly explains Mitch's ear test
(*512 crackles on heavy patches*).

## Parked — and why

| item | reason |
|---|---|
| `irq/30` at FF 90 (cell 4b) | targets producer lateness; the cushion arithmetic says sub-ms lateness cannot empty a 42.7 ms buffer |
| `isolcpus`, `nohz_full`, PREEMPT_RT | same — aimed at the wrong term until Gate B says otherwise |
| `lowlatency=N` | killed by Step 1 (rate inverted) |
| aligned periods 240/480/1008 | killed by the Scarlett (bimodality was adaptive clock lock) |
| Sound Blaster IRQ cell 1c | hypothesis already dead; no value |

Not wrong as engineering — aimed at a term the arithmetic says is not binding. Revisit only
if Gate B returns P1 or P2/P2'.

## In flight

**Step 4** — Scarlett `256 x 3` cond A, 3 streams x 3 runs vs **69.7/min**. Finish it: the
Step 3 bundle (`threadirqs` + v3d blacklist) is already applied and shipping, so we need to
know whether it helped or hurt regardless of what the model says. **Read a result above
69.7/min as the cost of threading every interrupt, not as a failure.**

## Product position, unchanged for now

**`1024 x 3` ships** — 64.0 ms, zero xruns, reproducible. `512` crackles on heavy patches;
`256` is unusable on both interfaces. The Scarlett earns its place on I/O grounds (MIDI DIN,
phantom power, real outputs) but **buys no latency on this Pi**.

The nearest credible improvement is **`1024 x 2` at 42.7 ms** (Phase C) — a third off,
without touching the period, which is the variable that has failed every time it moved.

## Time

| phase | Pi time |
|---|---|
| A | ~10 min (A1 offline) |
| B | ~30 min |
| C | ~30 min |
| D | ~15 min |
| step 4 (in flight) | ~12 min |
| **total** | **~1 h 40 min**, gated — A can cancel B and C entirely |

---

## Amendment after Step 4 (2026-08-21, late)

### Withdrawn: the Step 1 alignment closure

`scarlett-verdict-2026-08-21.md` closed the alignment question on *"bimodality vanished on
the async Scarlett, therefore the Sound Blaster's stream-start lottery was adaptive clock
lock, not frame phase."*

**Step 4 shows bimodality on that same async Scarlett** — stream 01 at 105/min against
streams 02-03 at 26-34/min. **The evidence that closure rested on is gone. Withdraw it.**

Two things stop this from reopening the line:

1. **n=3 streams cannot establish distribution shape.** One-high/two-low out of three draws
   is unremarkable from a bimodal *or* a wide unimodal distribution. Step 4 supports neither
   claim, and its 55.0/min mean is not a reliable estimate from three streams.
2. **The arithmetic argues against alignment regardless.** At high speed a microframe is
   125 us = **6 samples**. On the Scarlett: 256/6 = 42.67, 512/6 = 85.33,
   **1024/6 = 170.67** — *every* buffer we run is misaligned, **including the one with zero
   xruns**. If frame phase were the driver, 1024 would not be clean.

**Status: alignment moves from "closed" to "unsupported, still unpromising."** Do not spend
Pi time on it. Phase B telemetry shows the mechanism directly instead of by inference.

### Strengthened: the compute-bound hypothesis

`dsp_p99` at **256 x 3 cond A** (no `midi-load`): **63-89%**.
Prior at **1024 x 3 cond A**: **34.8%**.

**This is the matched-condition comparison Phase D was meant to obtain — and two of its
three points already exist.** A 2-2.5x efficiency loss from 1024 -> 256 is what fixed
per-callback costs predict: graph traversal, parameter smoothing and block setup do not
shrink with the buffer, so they amortise over fewer samples.

At p99 = 89% there is **11% headroom**. Type-(b) graph overruns (see
`xrun-counter-audit-2026-08-21.md`) are not a hypothesis at that point — they are expected.

### Revised order

**Fold Phase A into Phase D's window.** One pass gives the DSP ladder *and* the
`appl_ptr - hw_ptr` trace at each buffer size — strictly more informative than either alone.
A flat fill trace alongside p99 = 89% is **P3 and compute-bound in the same picture**.

| # | cell | Pi time |
|---|---|---|
| **D+A** | DSP p99 **and** fill telemetry at 1024 / 512 / 256, identical load, cond A | ~20 min |
| **B** | nperiods sweep 2/3/4/6 at period 1024 | ~30 min |
| **—** | `1024 x 2` open-check: does ALSA accept it at all? | **~30 s**, any idle moment |

Step 4's own read stands as recorded: mean below baseline but shape unstable at n=3, so
**`threadirqs` cost is neither confirmed nor ruled out.** Do not re-run it to settle that —
under the cushion model and the counter audit, xruns/min may not be the right term at all.
Settle the term first (D+A), then decide whether the question is worth re-asking.
