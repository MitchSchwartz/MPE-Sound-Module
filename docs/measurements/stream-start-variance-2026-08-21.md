# Stream-start variance — the axis every CI got wrong (2026-08-21)

**Status:** measured finding · **Confidence:** the per-run arrays below; mechanism (USB frame phase) = experiment (T12).

## Finding

The xrun rate is **fixed when the ALSA/JACK stream opens** and holds for the life of that
stream. Within a stream it is stable to about **±1/min**. Between streams it can move by
**an order of magnitude**.

Each `measure-latency-run.sh` invocation:

1. Calls `set-surge-audio.sh` → **restarts jackd** → opens **one** stream
2. Runs `n` consecutive 60 s windows on **that same stream**

So **`n=15` has never been 15 independent samples.** It is 15 correlated observations of
one draw. Every confidence interval in this effort is a **within-stream** interval.
**Stream-start variance is ~10× larger and has never been sampled.**

Same failure shape as the journal xrun counter and the meter restart bug: a number with a
tight error bar, where the error bar measures the **wrong axis**.

## Evidence — 256×3 condition A, per-run xruns (not means)

```
T11  256   10 16  8 15 20 12  8  6 12 20  4 10 14 12 14   mean 12.10
T13  256    4  0  2  2  0  0  1  0  0  2  2  4  2  4  0   mean  1.53
hyg  256   16  4  6 10  4  4 12  4 12 10  6  6  8  6  9   mean  7.80
```

- T13 cell: **never exceeds 4**
- Post-hygiene cell: **never below 4**
- **No overlap** between the three invocations
- Each cell is tight around its own mean; means are **8.3σ**, **6.1σ**, and **2.8σ** apart

These are **three different rates**, not three samples of one.

## Phase 0 cannot be evaluated on delta yet

| config | pre-hygiene | post-hygiene | interpretation |
|---|---|---|
| 512×3 A | 0.13, 14/15 | 0.13, 14/15 | identical — but rate near zero, so stream-to-stream swing is **invisible** |
| 1024×3 D8 | 0.00, 15/15 | 0.20, 12/15 | both **single-stream draws**; spread swamps hygiene delta |

Phase 0 fixes were real (617× pressure-remap restarts, broken remap, unpinned meter,
saturated CPU0). **The delta is not measurable** until the protocol controls for stream
starts.

## Shipping claim

**Withdrawn.** `0.00, 15/15` was one stream. `0.20, 12/15` was one stream. Neither is the
ship number. Commercially: a config whose mean looks fine but whose stream-to-stream spread
reaches 12/min is **not shippable** — the user gets one stream per power-on and cannot
re-roll.

## Correct protocol

**Sample stream starts, not minutes.**

| parameter | old (wrong) | new |
|---|---|---|
| unit of replication | 60 s window | **jackd restart** (new stream) |
| design | 1 stream × 15 windows | **N streams × k windows** |
| report | one mean ± within-stream CI | **within-stream** and **between-stream** variance separately |

Implementation:

- **Today:** `scripts/measure-stream-sample.sh` — N harness invocations (each restarts
  jackd at entry), k runs each, one log file per stream (tags would collide in one file).
- **Not sufficient:** `--restart-between` in `measure-latency-run.sh` — that restarts the
  **looper condition stack only**, not jackd (see `_restart_condition_stack`).

Example:

```bash
sudo ./scripts/measure-stream-sample.sh \
  --buffer 256 --condition A --streams 10 --runs-per-stream 3 \
  --output ~/256-A-streams.log
```

## Primary experiment — T12 (USB frame alignment)

256 frames = 5.33 USB frames → period boundaries sit at a **fixed phase** against the 1 ms
grid, set by stream-open time. Misaligned periods have phase freedom; aligned periods do not.

**Run 192×3 (exactly 4 USB frames) vs 256×3, ten streams each.** If between-stream
variance collapses at 192, that is the mechanism — and the buffer ladder has been on the
wrong grid from the start.

See [`Documents/specs/usb-runway-levers.md`](../../Documents/specs/usb-runway-levers.md) ·
[`scripts/measure-t12.sh`](../../scripts/measure-t12.sh) (must be rewritten for stream
sampling before execution).

*Last updated: 2026-08-21 (America/Toronto)*
