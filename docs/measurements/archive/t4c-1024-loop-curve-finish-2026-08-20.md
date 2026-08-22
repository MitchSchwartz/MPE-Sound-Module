# T4c — 1024 loop curve finish: loops8 + loops16 (2026-08-20)

**Pi:** `raspberrypi2` · commit `4067b45` · log `~/t4c-1024-finish.log`

Condition **B** (+ sooperlooper), 1024×3, n=15 per cell, `--no-restore-buffer` between cells.

## loops8

| run | 1–15 xruns |
|---|---|
| values | all **0** |

**Mean 0.00** · **15/15 clean**

## loops16

| run | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | 14 | 15 |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| xruns | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | **1** | 0 | 0 | **1** | 0 | 0 |

**Mean 0.13** · **13/15 clean**

## Full 1024 curve (T4 partial + T4c)

| playing loops | mean xruns/60 s | n | clean |
|---:|---:|---:|---|
| 0 | **0.00** | 15 | 15/15 |
| 4 | **0.00** | 15 | 15/15 |
| 8 | **0.00** | 15 | 15/15 |
| 16 | **0.13** | 15 | 13/15 |

## Product claim (**measured**)

**"16 loops at 64 ms" holds at 1024×3** for this rig: 60 runs across loop counts 0–16,
**58/60 clean**, **2 xruns total** (both at loops16). Mean at max load **0.13** — same
order as condition A at 512 without looper.

Shippable spec sentence with honest footnote: zero xruns through 8 loops; at 16 loops
13/15 clean (mean 0.13).

512 curve answered separately — [`t4a-512-loop-curve-2026-08-20.md`](t4a-512-loop-curve-2026-08-20.md).

*Last updated: 2026-08-20 (America/Toronto)*
