# Review audit — Composer subagent grumpy pass (2026-08-15)

> **Provenance:** Cursor Composer subagent (`composer-2.5-fast`), session 2026-08-15 (America/Toronto).
> **Agent ID:** `979eb46f-71d1-47d2-9977-c66c2e0f45fa`
> **Skill:** `review-audit`
> **Audited artifact:** Manager-session **draft** grumpy review (pasted into subagent prompt at dispatch) — **not** [`grumpy-review-looper-2026-08-15.md`](grumpy-review-looper-2026-08-15.md).
> **See also:** [`review-audit-looper-2026-08-15.md`](review-audit-looper-2026-08-15.md) — adversarial audit of the on-disk fresh-context grumpy review **plus live Pi read-only inspection** (orphan SL / no JACK client).
> **Post-session (same manager turn):** Contradictory footswitch grid tests partially fixed in `tests/test_apc_footswitch.py` per audit finding #1 below.

Adversarial verification of the Composer subagent grumpy review against repo `MPE-Module`, branch `dev`. Read-only.

**Headline:** The subagent grumpy pass is **mostly accurate on code smells and convergence timing**, **wrong on one DECISIONS reading**, and **missed a test contradiction** that matters more than the spec tension it invented. No P0 for eval-only localhost stack.

---

## Work Queue

38 claims across: first impressions, architecture, code quality, code smells, logic, tests, security/DX, priority backlog. Full claim list in subagent transcript (`979eb46f-71d1-47d2-9977-c66c2e0f45fa`).

---

## Claim verification (summary)

| Area | Confirmed | Partial | Incorrect |
|------|-----------|---------|-----------|
| Aug-14 fixes landed | ✅ loop-0 sub, no deferred triggers, re-register | — | — |
| Dual state / optimistic `_tap` | ✅ | — | — |
| Grid occupancy / hold-clear gap | ⚠️ unit-tested in `test_sl_grid_state`; integration gap real | — | — |
| DECISIONS "tempo survives zero clips" | — | — | ❌ canon says **no clips, no grid** |
| `wire-jack-graph` fixed | ✅ | — | — |
| Out-of-order OSC 🔴 | — | ⚠️ plausible, unproven | — |
| 492-line god class | — | ⚠️ file 492 lines; class ~310 | — |

Full per-claim tables with evidence line refs: preserved in agent transcript; key rows below.

### Key confirmed claims

| Claim | Evidence |
|-------|----------|
| Optimistic `_tap()` | `apc_footswitch.py` sets `self.state = STATE_PLAYING` before SL confirms (~314–317) |
| Dead `anchor_phase` import | `sooperlooper-apc-bench.py:28` imported, never called; tempo sent at `:123` |
| `stop_all_loops` stale `sl_state` | `apc_footswitch.py:486–490` sets bench state only |
| Test contradiction | `test_apc_footswitch` vs `test_sl_grid_state` on grid drop when last clip cleared |

### Key incorrect / overstated claims

| Claim | Correction |
|-------|------------|
| "Defining clip delete should leave tempo" | DECISIONS `51–52`: last clip cleared → grid drops |
| "No engine-path grid drop test" | Unit path exists in `test_sl_grid_state.py`; gap is **footswitch integration** via `sync_from_sl` |
| Out-of-order OSC as 🔴 | Downgrade to P2/P3 unless logs show reordering |

---

## What the review missed

1. **Contradictory test expectations (High)** — `test_grid_survives_deleting_the_clip_that_defined_it` vs `test_clearing_the_last_clip_drops_the_grid`. DECISIONS aligns with the latter. **Partially fixed post-audit:** replaced with `test_hold_clear_drops_grid_when_engine_reports_last_clip_off` and `test_deleting_defining_clip_keeps_grid_while_other_clips_remain`.

2. **`_clear_loop()` bypasses occupancy tracking (Medium)** — never calls `note_loop_content`; grid drop waits for SL OFF via listener.

3. **Aug-14 listener re-register fixed (informational)** — `maybe_reregister()` in main loop.

---

## Severity re-assessment

| Issue | Reviewer | Audit rating |
|-------|----------|--------------|
| Optimistic `_tap()` | 🔴 | **High** (not P0 for eval) |
| Grid lifetime / hold-clear | 🔴 | **High** |
| Out-of-order OSC | 🟡 | **Low** |
| God class | structural | **Medium** maintainability |

---

## Prioritized action matrix

| Priority | Issue | Effort |
|----------|-------|--------|
| **P1** | Reconcile hold-clear vs `note_loop_content`; fix contradictory tests | half-day (tests partially done) |
| **P1** | Make `_tap()` send-only | multi-day |
| **P1** | `stop_all_loops`: don't set bench terminal state until SL confirms | quick fix |
| **P2** | Fake-SL OSC stub harness | multi-day |
| **P2** | Serialize/order bench listener updates | half-day |
| **P2** | Use or delete `anchor_phase` | quick fix |
| **P3** | Split `LoopFootswitch` | refactor project |

---

## Disagreements and judgment calls

1. **DECISIONS misread** — grid drops when last clip cleared; defining clip is ordinary *after* establish, not while zero clips remain.
2. **Rewrite vs patch** — send-only `_tap()` before structural split; defer god-class split to P3.
3. **Eval calibration** — no P0; Pi localhost stack, sole operator.

---

## Bottom line

Trust **send-only `_tap()`** and **hold-clear / grid-lifetime gap**. Downgrade out-of-order OSC urgency. Fix contradictory tests before more control-layer work (started in same session). For live-appliance root cause (orphan SL after jackd restart), read [`review-audit-looper-2026-08-15.md`](review-audit-looper-2026-08-15.md) §0 — that finding is **outside** this subagent audit scope.
