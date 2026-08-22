# Session handoff — 2026-08-20 (consolidated)

**Status: SUPERSEDED (2026-08-21). Do not cite.** Frozen at I3/T4c; its branch and queue
are both stale, and it predates the T5 soak and T9. Current state lives in
[`README.md`](README.md) -- Low-latency arc, and the queue in
[`next-tasks-2026-08-20.md`](../../Documents/specs/next-tasks-2026-08-20.md).

**Branch:** `plan/post-t4-adjust` (historical)

Single rollup. Superseded queue in [`next-tasks-2026-08-20.md`](../../Documents/specs/next-tasks-2026-08-20.md) §Adjustments.

---

## BLOCKER — baseline moved and stayed moved

| measurement | A @ 512×3, xruns/60 s | clean |
|---|---:|---|
| baseline, n=15, pre-E1 | **0.13** | 14/15 |
| E1 three-core, n=15 | 0.80 | |
| I3 after revert, n=5 | 0.80 | 2,0,0,0,2 |
| **I3 revised, n=15** | **0.13** | 14/15 clean — **blocker cleared** |

The revert restored **config**, not the **number**. Everything quoting A = 0.13 — including
"512 usable without the looper" — is blocked until I3@n=15 lands.

**Named hypothesis (guess):** I2 fixed `_meter_xruns` (`|| echo 0`). Old harness may have
under-counted; not disproved in theory, but **I3@n=15 = 0.13** under the fixed harness.
Baseline stands; no doc revision for IRQ win (4.20 → 0.13).

Config diff: [`i3-config-diff-2026-08-20.md`](i3-config-diff-2026-08-20.md). Result:
[`i3-n15-e1-revert-2026-08-20.md`](i3-n15-e1-revert-2026-08-20.md).

---

## T4a @ 512 — answered, do not re-run

[`t4a-512-loop-curve-2026-08-20.md`](t4a-512-loop-curve-2026-08-20.md) — non-monotonic,
no tier. **Cancelled:** remaining 512 runs.

## T4c @ 1024 — product claim (pending)

| loops | mean (n=15) | clean |
|---|---:|---|
| 0 | **0.00** | 15/15 |
| 4 | **0.00** | 15/15 |
| 8 | **0.00** | 15/15 |
| 16 | **0.13** | 13/15 |

**Product claim holds.** 60 runs @ 1024, loops playing: 58/60 clean, 2 xruns total (loops16
only). See [`t4c-1024-loop-curve-finish-2026-08-20.md`](t4c-1024-loop-curve-finish-2026-08-20.md).

## T5 — blocked

Soak waits on I3 + decided shipping config.

---

## Revised queue (~70 min)

| # | task | status |
|---|---|---|
| 1 | I3 n=15 + config diff | **done** — 0.13, 14/15 |
| 2 | T4c 1024 loops8 + loops16 | **done** — loops8 0.00, loops16 0.13 |
| 3 | T5 | unblocked on I3; still needs decided shipping config |

---

## Code landed (reference)

| Item | Commit |
|---|---|
| I2 harness fix | `e4c32fe` |
| T6 rig sweep | `f543f38` |
| T4/T5 scripts | `f543f38` |
| post-t4-adjust plan | `e6e373f` |

*Last updated: 2026-08-20 (America/Toronto)*
