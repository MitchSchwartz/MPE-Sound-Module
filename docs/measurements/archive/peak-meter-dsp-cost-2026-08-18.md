# The Python peak meter costs ~30 points of peak DSP headroom

*Measured on `raspberrypi2`, 2026-08-18, at `512 x 3 @ 48000` (10.67 ms deadline).*

**Question:** Mitch reported audible crackle while playing after a deploy, at a buffer
size that had been fine before. Was it the Phase 3M looper merge, the buffer, or something
else?

## Protocol

Three 90 s windows while playing. One streaming `jack_cpu_load` client (not a fork per
sample — see [`DECISIONS.md`](../../Documents/DECISIONS.md) 2026-08-18), plus per-process
jiffies from `/proc/<pid>/stat` fields 14-17. **`surge-xt-cli` CPU is the matched-load
control**: without it the comparison is meaningless, because playing intensity varies.

## Result

| Run | Meter | surge CPU (control) | DSP median | DSP p90 | DSP max | samples >70% |
|---|---|---|---|---|---|---|
| A | **on** | 39% | 59.0 | 81.2 | **91.9** | **26** |
| B | off | 11% | 18.0 | 18.4 | 18.7 | 0 |
| C | **off** | 39% | 43.5 | 51.1 | **61.2** | **0** |

**Run B is discarded.** Surge measured 11% — its idle figure — so that window contained
little playing. It was initially read as a dramatic win; it was a confound. Runs A and C
are the honest comparison: control matched at 39%, one variable changed.

## What it shows

Removing `mpe-peak-meter` from the graph moves peak DSP from **91.9% to 61.2%**, and
samples above 70% from **26 to zero**.

**The distribution identifies the mechanism.** Median moves 15 points; p90 and max move
30. Steady CPU consumption shifts a distribution uniformly. This barely lifts the floor
and collapses the tail — the signature of *intermittent blocking*, where the realtime
callback waits on the GIL held by the touch UI's `SCHED_OTHER` draw loop (28-30% of a core
in every run). It explains why the crackle was sporadic rather than constant.

The meter's callback thread was `SCHED_FIFO 65` under `LimitRTPRIO=95` throughout.
**Realtime priority does not grant the GIL** — priority is necessary and not sufficient.

## Why a ring buffer would not have fixed it

The blocking precedes the callback's first instruction: `port.get_array()` must acquire
the GIL. A Python JACK callback cannot be made RT-safe by buffer discipline, because the
wait happens before any of our code runs. Only a compiled client, or not being a JACK
client at all, closes it.

## Not the cause

- **PR #72 (Phase 3M looper merge):** `looper-session` 6%, `sooperlooper` 0% in all runs.
- **Buffer size:** `512 x 3` has comfortable headroom without the meter. The buffer change
  (1024 -> 512, written to `/etc/mpe/mpe.env` at 19:41 during the deploy — see spec Q5)
  removed the margin that had been hiding this, but did not create it.

## Caveat on the instrument

`jack_cpu_load` samples at ~1 Hz and reports a smoothed value. Crackle happens inside a
10.67 ms period, so individual overruns are invisible to it. Residual crackle was still
reported at DSP max 61%, and this method cannot see it. The honest counter is xruns, and
the shipping configuration (`jackd -s`) does not report them — spec **Q10**.

## Actions

- `MPE_PEAK_METER=0` set on the appliance (live mitigation).
- Phase 5 promoted from orthogonal to blocking for low-latency operation.
- Criterion 34 must be met by a compiled meter, never by a cheaper Python callback.
