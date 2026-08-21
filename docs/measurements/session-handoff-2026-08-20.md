# Session handoff — 2026-08-20 (consolidated)

**Branch:** `plan/post-t4-adjust` · **Status: active — I3 blocker first**

Single rollup. Superseded queue in [`next-tasks-2026-08-20.md`](../../Documents/specs/next-tasks-2026-08-20.md) §Adjustments.

---

## BLOCKER — baseline moved and stayed moved

| measurement | A @ 512×3, xruns/60 s | clean |
|---|---:|---|
| baseline, n=15, pre-E1 | **0.13** | 14/15 |
| E1 three-core, n=15 | 0.80 | |
| I3 after revert, n=5 | **0.80** | 2,0,0,0,2 |
| **I3 revised, n=15** | *pending* | |

The revert restored **config**, not the **number**. Everything quoting A = 0.13 — including
"512 usable without the looper" — is blocked until I3@n=15 lands.

**Named hypothesis (guess):** I2 fixed `_meter_xruns` (`|| echo 0`). Old harness may have
under-counted; **0.13 may be partly a measurement artifact.** If I3 → 0.80, baseline is
0.80 and docs revise (honest reading: IRQ work **4.20 → 0.80**, not → 0.13).

Config diff: [`i3-config-diff-2026-08-20.md`](i3-config-diff-2026-08-20.md).

---

## T4a @ 512 — answered, do not re-run

[`t4a-512-loop-curve-2026-08-20.md`](t4a-512-loop-curve-2026-08-20.md) — non-monotonic,
no tier. **Cancelled:** remaining 512 runs.

## T4c @ 1024 — product claim (pending)

| loops | mean (n=15) |
|---|---:|
| 0 | **0.00** |
| 4 | **0.00** |
| 8 | *pending* |
| 16 | *pending* |

30 consecutive zeros with loops playing — strongest result in the investigation. Finish
loops8 + loops16 only (`measure-loop-curve-1024-finish.sh`).

## T5 — blocked

Soak waits on I3 + decided shipping config.

---

## Revised queue (~70 min)

| # | task | status |
|---|---|---|
| 1 | I3 n=15 + config diff | **blocker** |
| 2 | T4c 1024 loops8 + loops16 | after I3 |
| — | T4 512 remainder | **cancelled** |
| 3 | T5 | blocked |

---

## Code landed (reference)

| Item | Commit |
|---|---|
| I2 harness fix | `e4c32fe` |
| T6 rig sweep | `f543f38` |
| T4/T5 scripts | `f543f38` |
| post-t4-adjust plan | `e6e373f` |

*Last updated: 2026-08-20 (America/Toronto)*
