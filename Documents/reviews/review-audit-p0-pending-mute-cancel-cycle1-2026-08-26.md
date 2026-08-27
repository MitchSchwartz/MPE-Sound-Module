# Review Audit — P0 pending-mute cancel (cycle 1 of 5)

**Audited review:** `Documents/reviews/grumpy-review-p0-pending-mute-cancel-2026-08-26.md`  
**Branch:** `yolo/p0-pending-mute-cancel` (uncommitted)  
**Date:** 2026-08-26 (America/Toronto)  
**Method:** `git diff` + full read of changed files and referenced tests/spec rows. Tests not executed (python blocked by agent policy; claims verified from source).

---

## Work Queue

### Architecture / design (Grumpy §2)
1. Cancel checks `pending` against engine `sl_state` before `effective_state`
2. `cancel_pending` flag avoids overloading `expect=None`
3. Launch cancel sends no OSC undo; queued `trigger` may still fire at boundary
4. Mute cancel sends `mute_off` — symmetric with spec SP3
5. Launch cancel is spec-adjacent and unverified on hardware

### Code smells (Grumpy §3)
6. Mute-cancel path is coherent (plan → `mute_off` → clear pending → fake engine clears `_at_boundary`)
7. Launch cancel: no SL command; test asserts bench only, not engine after `boundary()`
8. DECISIONS Gate A row says 16 tracks / Scene Launch 1–8 vs rev 2 spec (15 tracks / Scene 1–7)
9. `_pending_since` not reset when `cancel_pending` clears `_pending`
10. `sl_hud_monitor` SCRATCH default duplicates constant (acceptable)

### Logic & edge cases (Grumpy §4)
11. Re-tap pending mute while PLAYING → `mute_off`
12. Re-tap pending mute while OVERDUBBING → handled via `ACTIVE_PLAY`
13. Re-tap pending mute on pad down → no-op
14. Re-tap pending launch while MUTE → bench only
15. Cancel during `_waiting_for_quantize()` → blocked (pre-existing)
16. Cancel during tail capture → not reachable (pre-existing)

### Tests (Grumpy §5)
17. Mute cancel has engine boundary test (`test_second_tap_during_a_quantized_stop_keeps_it_playing`)
18. No test that launch cancel survives `engine.boundary()` without PLAYING

### Summary roll-up (Grumpy §6)
19. G1 P1 — Launch cancel engine gap
20. G2 P1 — DECISIONS Gate A doc drift
21. G3 P2 — Clear `_pending_since` on cancel

---

## Claim Verification

### Architecture — `loop_model.py` / `apc_footswitch.py`

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 1 | Cancel uses engine `sl_state`, not `effective_state` | ✅ Confirmed | ```134:148:scripts/sooperlooper/loop_model.py``` — pending-cancel block runs **before** `state = effective_state(sl_state, pending)` |
| 2 | `cancel_pending` flag on `Plan` | ✅ Confirmed | ```111:112:scripts/sooperlooper/loop_model.py``` adds field; ```798:804:scripts/sooperlooper/apc_footswitch.py``` gates early return; ```842:845:scripts/sooperlooper/apc_footswitch.py``` clears `_pending` without calling `_expect` |
| 3 | Launch cancel sends no OSC undo | ✅ Confirmed | ```144:148:scripts/sooperlooper/loop_model.py``` — `Plan(cancel_pending=True, note="cancel pending launch")` with empty `commands` |
| 4 | Queued `trigger` may still fire at boundary | ✅ Confirmed | ```81:86:tests/fake_sl_engine.py``` — first launch tap sets `_at_boundary[loop] = SL_STATE_PLAYING`; launch cancel sends no verb to pop it; ```103:112:tests/fake_sl_engine.py``` — `boundary()` applies all `_at_boundary` entries |
| 5 | Mute cancel sends `mute_off` | ✅ Confirmed | ```138:143:scripts/sooperlooper/loop_model.py```; ```77:80:tests/fake_sl_engine.py``` — `mute_off` pops `_at_boundary` |
| 6 | Launch cancel unverified on hardware / spec-adjacent for P0 | ⚠️ Partially True | Spec Phase 0 scope is **pending-mute cancel only** (```38:39:Documents/specs/multi-clip-per-track-spec.md```). Rev 2 cancel rules cover launch (```257:260:Documents/specs/multi-clip-per-track-spec.md```) but P0 prerequisite text names mute/stop only (```262:264```). Branch ships launch cancel anyway — real gap, but **not in stated P0 acceptance** |

### Code smells

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 6 | Mute path coherent end-to-end | ✅ Confirmed | Plan → `_hit("mute_off")` → `cancel_pending` clears bench `_pending`; fake engine removes queued mute at boundary (see tests below) |
| 7 | Launch test is bench-only | ✅ Confirmed | ```843:850:tests/test_apc_footswitch.py``` — asserts `_pending`, `_hits`; no `FakeSlEngine`, no `boundary()` |
| 8 | DECISIONS Gate A row stale vs rev 2 | ✅ Confirmed | ```11:13:Documents/DECISIONS.md``` — "16 tracks", "Scene Launch 1–8"; spec rev 2 (```14:15:Documents/specs/multi-clip-per-track-spec.md```, ```34:34```, ```562:563```) — **15 tracks**, **Scene Launch 1–7** |
| 9 | `_pending_since` not cleared on cancel | ✅ Confirmed | ```842:843:scripts/sooperlooper/apc_footswitch.py``` sets `_pending = None` only; `_pending_since` set only in `_expect` (```214:216```). No other writers. Stale timestamp is inert while `_pending is None` because `_expire_pending` returns immediately (```220:221```) |
| 10 | SCRATCH duplicate constant acceptable | ✅ Confirmed | ```33:34:scripts/sooperlooper/sl_hud_monitor.py``` default `"14"` with comment matching `sl_seam_weld.SCRATCH_LOOP` / `looper_songs.SCRATCH`; fixes prior wrong default `"15"` in diff |

### Logic & edge cases

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 11 | Pending mute re-tap while PLAYING → `mute_off` | ✅ Confirmed | Unit: ```182:186:tests/test_loop_model.py```; footswitch: ```834:841:tests/test_apc_footswitch.py``` |
| 12 | OVERDUBBING covered | ✅ Confirmed | ```12:14:scripts/sooperlooper/sl_loop_states.py``` — `SL_STATE_OVERDUBBING ∈ ACTIVE_PLAY`; cancel condition ```138:138:scripts/sooperlooper/loop_model.py```. **No dedicated OVERDUBBING test** (blind spot, low risk) |
| 13 | Pad down no-op for pending mute cancel | ✅ Confirmed | ```193:195:tests/test_loop_model.py``` |
| 14 | Pending launch re-tap bench-only | ✅ Confirmed | Clears `_pending` (```843:849```) but engine queue untouched (claim 4) |
| 15 | Quantize wait blocks cancel | ✅ Confirmed | ```784:786:scripts/sooperlooper/apc_footswitch.py``` — returns before `plan_gesture`; pre-existing, unchanged in diff |
| 16 | Tail capture blocks cancel | ✅ Confirmed | ```776:780:scripts/sooperlooper/apc_footswitch.py``` — early return on any edge except tail down-cancel; pre-existing |

### Tests

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 17 | Mute cancel boundary test exists | ✅ Confirmed | ```117:137:tests/test_footswitch_against_engine.py``` — two taps, `engine.boundary()`, asserts `SL_STATE_PLAYING` |
| 18 | No launch-cancel boundary test | ✅ Confirmed | Grep across `tests/` — no test pairing launch re-tap with `FakeSlEngine.boundary()` |

---

## Severity Re-Assessment

| ID | Issue | Reviewer | My rating | Δ | Reasoning |
|----|-------|----------|-----------|---|-----------|
| G1 | Launch cancel — no engine cancel / no boundary test | P1 | **P1** (if launch cancel ships in this branch) / **P2** (if deferred) | ↔ / ↓ | Technical claim is solid. P0 spec acceptance is **mute cancel only**; launch cancel is forward-looking. Keeping it without an engine test is a real player-facing lie at the bar, but it is not a P0 gate for the stated Phase 0 deliverable. |
| G2 | DECISIONS Gate A row vs rev 2 spec | P1 | **P1** | ↔ | Same commit introduces the row; wrong track/scene counts will mislead every downstream agent and spike. Quick doc fix. |
| G3 | `_pending_since` not reset | P2 | **P3** | ↓ | No observable bug today — `_expire_pending` keys off `_pending is None` first; next `_expect` overwrites timestamp. Hygiene only. |

---

## What the Review Missed

### M1 — P0 scope vs branch scope (scope)

Branch implements **both** mute-cancel and launch-cancel in `plan_gesture`, but spec Phase 0 names only pending-mute cancel (```38:39:Documents/specs/multi-clip-per-track-spec.md```). Launch cancel expands blast radius without a matching acceptance row or SP spike.

**Suggestion:** Either drop launch-cancel from this P0 branch, or add an explicit Gate A / spec note that bidirectional cancel is in scope for this laptop session.

### M2 — Launch test name overstates coverage

`test_re_tap_cancels_pending_launch_without_second_trigger` (```843:850:tests/test_apc_footswitch.py```) verifies no **second** `trigger` OSC hit on re-tap. It does **not** prove the **first** queued `trigger` is cancelled. Name implies engine safety; test only covers command deduplication on the bench side.

### M3 — `mute_off` while engine still PLAYING (pre-boundary)

On re-tap, `sl_state` is still `PLAYING` (mute queued, not landed). `FakeSlEngine.mute_off` (```77:80```) pops `_at_boundary` without requiring `SL_STATE_MUTE` first — matches SL “cancel queued mute” semantics and is what the boundary test relies on. Grumpy did not call this out; behavior is correct.

---

## What the Review Got Right (And Why It Matters)

**Mute cancel at the right layer.** Checking `pending` against raw `sl_state` before `effective_state` fixes the launch-on-re-tap failure mode Grumpy describes — with `pending=STOPPED`, `effective_state` would read Stopped while the engine is still playing, and a naive re-tap would queue launch instead of abort.

**Engine proof for the P0 path.** `test_second_tap_during_a_quantized_stop_keeps_it_playing` exercises the full bench → fake engine → boundary loop for mute cancel. That is the SP3 spike acceptance pattern (```542:542:Documents/specs/multi-clip-per-track-spec.md```).

**Launch cancel honesty gap.** If this ships to Pi without an undo verb or boundary test, the pad LED will show idle/play intent while `_at_boundary` still holds PLAYING — the player hears a launch they thought they cancelled. Fake engine makes this reproducible without hardware.

**DECISIONS drift is immediate.** The new Gate A row contradicts rev 2 on the same day the spec was corrected. Agents loading DECISIONS over the spec will rebuild wrong scene/track assumptions.

---

## Prioritized Action Matrix

| Priority | Issue | Verdict | Effort | Depends On |
|----------|-------|---------|--------|------------|
| **P0** | *(none)* — mute cancel path tested through fake engine boundary; no data-loss or security surface in diff | — | — | — |
| **P1** | Launch cancel: bench clears `_pending` but queued `trigger` still fires at boundary — no engine test | ✅ Confirmed | Half-day (add `test_footswitch_against_engine` launch re-tap + `boundary()`; spike undo verb if fake model insufficient) | — |
| **P1** | DECISIONS Gate A row: "16 tracks" / "Scene Launch 1–8" vs rev 2 **15 tracks** / **Scene 1–7** | ✅ Confirmed | Quick fix | — |
| **P2** | Scope: launch cancel in P0 branch without spec Phase 0 acceptance — defer or document | ⚠️ Partially True | Quick fix (delete launch branch + tests, or amend spec/Gate A) | — |
| **P2** | Launch unit test name implies engine safety; only checks no second OSC hit | ✅ Confirmed (missed by Grumpy) | Quick fix | P1 engine test or defer launch cancel |
| **P3** | Reset `_pending_since` when `cancel_pending` clears `_pending` | ✅ Confirmed | Quick fix | — |
| **P3** | `sl_hud_monitor` could import `SCRATCH` instead of duplicating default `"14"` | ✅ Confirmed | Quick fix | — |
| **P3** | Add OVERDUBBING pending-mute cancel test (code path already covered by `ACTIVE_PLAY`) | 🔍 New | Quick fix | — |

---

## Disagreements and Judgment Calls

**G1 severity for P0 merge.** Grumpy rates launch cancel P1 unconditionally. For a branch whose **named** deliverable is pending-mute cancel, the mute path is shippable as-is. Launch cancel is the P1 item **only if** this branch is treated as shipping bidirectional cancel. Preferred path: **defer launch cancel** from P0 (remove ```144:148:scripts/sooperlooper/loop_model.py``` + launch tests) and land mute cancel + SCRATCH default + DECISIONS fix; track launch cancel under multi-clip SP with an engine spike.

**G3 elevation.** Reviewer P2 is fair; audit drops to **P3** — no failure mode found with current `_expire_pending` / `_expect` flow.

**Positive findings not in action matrix.** Grumpy’s “good shape” assessment holds: small diff, pure function in `loop_model.py`, minimal bench wiring, `cancel_pending` is the right seam. No objections.

**First impressions (launch half unproven).** ✅ Confirmed — the twitch is warranted; mute half is proven, launch half is not.

---

## Counts

| Metric | Value |
|--------|-------|
| **P0** | **0** |
| **P1** | **2** (launch cancel engine gap; DECISIONS doc drift) |
| **Artifact** | `Documents/reviews/review-audit-p0-pending-mute-cancel-cycle1-2026-08-26.md` |

---

*Audit complete — read-only; product code unchanged.*
