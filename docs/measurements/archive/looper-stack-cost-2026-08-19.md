# Looper stack cost, re-measured with the graph probe fixed — 2026-08-19

**Work order task 6. This supersedes the 2026-08-18 numbers, which are void.**

Those numbers were taken while `surge-watchdog`'s `jack_lsp` probe was the dominant xrun
source (35/min), and the run that blamed the looper had stopped the looper *and both
watchdogs* together — so it attributed three components' cost to one. They are the reason
the stack is currently opt-in, and they cannot support that decision.

## Method

`raspberrypi2`, `jackd -R -P70 -s -d alsa -P hw:1 -r 48000 -p 1024 -n 3`.
Deterministic load via `scripts/midi-load.py` (3 voices), started 8 s before each window
so the start transient falls outside it. `scripts/xrun-corr.sh 60` samples `xruns=` from
`meter.state` and DSP once per second. **n = 3 runs x 60 samples per condition.**

Conditions are cumulative — each adds one component to the one above, which is precisely
what the void run failed to do.

## Results — 1024 x 3

| condition | runs | xruns/60 s | DSP median | p90 | max | Δ median vs above |
|---|---:|---:|---:|---:|---:|---:|
| A — all off (baseline) | 3 | **0, 0, 0** | 19.14% | 21.16 | 22.64 | — |
| B — `mpe-sooperlooper` only | 3 | **0, 0, 0** | 24.64% | 26.95 | 27.81 | **+5.50** |
| C — `+ mpe-looper-session` | 3 | **0, 0, 0** | 24.79% | 27.03 | 29.44 | +0.15 |
| D — `+ sl-watchdog` | 3 | **0, 0, 0** | 25.09% | 27.50 | 30.35 | +0.30 |

180 DSP samples per condition.

## Findings

**1. The whole cost is the SooperLooper engine, and it is 5.5 points of DSP.** That is a
real cost and it is not surprising — it is a JACK client doing work in the graph. What it
is *not* is the 30-point catastrophe the void numbers implied.

**2. The merged looper session is free.** +0.15 points between C and B, against a
run-to-run spread of ±2 points. That is indistinguishable from zero, and it is the number
Phase 3M criterion 47 was asking for from the other direction: the merged process does not
cost more than the two it replaced — it costs nothing measurable at all.

**3. `sl-watchdog` is free.** +0.30, same argument.

**4. Zero xruns in all twelve runs.** At 1024 x 3 with a baseline of 19% DSP there is
enough headroom that no condition can be made to fail, which means **this run establishes
cost but cannot establish safety.** Stated plainly because the distinction is the entire
lesson of the void measurement: an experiment that returns the same answer in every
condition has not tested anything.

## What this does not settle

~~**The 512 x 3 comparison is still missing**~~ **Done 2026-08-19 on `b9bf98e`** — see
below. B10 (feel) still cannot be delegated.

Two further traps worth recording, both of which produced a clean exit code and no data:

- `xrun-corr.sh` writes to `~/xrun-corr.out`, **not stdout**, and truncates it per run.
  The first attempt redirected its stdout: twelve runs, zero readings, exit 0. Every run
  is now copied out immediately and asserted non-empty.
- `set-surge-audio.sh` without `sudo` fails on `/etc/mpe/mpe.env` and **carries on**, so a
  real `jackd` command line into its own output. Assert the period; do not assume it.

## Results — 512 x 3 (A vs D, re-taken 2026-08-19)

`b9bf98e` on `raspberrypi2`. Same load protocol as above (`midi-load.py` 75, 8 s warm-up,
`xrun-corr.sh 60`, n = 3). Only **A (all off)** vs **D (full stack)** — the comparison
that gates whether the stack is safe at the buffer the instrument is played at.

| condition | runs | xruns/60 s | DSP median | p90 | max |
|---|---:|---:|---:|---:|---:|
| A — all off (baseline) | 3 | **2, 0, 0** | 38.52% | 39.45 | 48.85 |
| D — full stack | 3 | **7, 24, 29** | 38.70% | 41.81 | 69.64 |

**Cost is unchanged at 512** — DSP medians sit in the same ~39% band with or without the
stack. **Safety is not:** baseline is clean (one blip in run 1), full stack produces
material xruns in all three runs under the same deterministic load. That matches why the
stack stayed opt-in: the problem was never the 30-point cost fiction from the void run, it
was crackle at this buffer under playing load.

Buffer restored to **1024 × 3** after the run. Log: `/tmp/d15-512x3-20260819-223937.log`
on the Pi.

## Bearing on D15 and on `install-units.sh`

D15 (the SooperLooper adopt/kill gate) must no longer inherit the void numbers. On **cost**
grounds the stack is affordable: 5.5 points of DSP at 1024 x 3, all of it the engine, with
the Phase 3M merge and the watchdog free. At **512 x 3 under load**, the same cost holds but
the **full stack is not xrun-clean** (7 / 24 / 29 xruns per 60 s vs 2 / 0 / 0 baseline).

**Recommendation held, not acted on.** Returning the stack to `ENABLED` still needs B10
(feel), which cannot be delegated — and the 512 safety numbers above are a reason to keep
the opt-in default even after B10 passes, until whatever drives those xruns is understood.
The opt-in default now stands on measured evidence at both buffers, not on a number that
was wrong.
