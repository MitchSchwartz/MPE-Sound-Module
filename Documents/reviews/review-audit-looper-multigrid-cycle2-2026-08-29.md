# Review Audit — Grumpy looper multigrid review, CYCLE 2

**Audited artifact:** `Documents/reviews/grumpy-review-looper-multigrid-cycle2-2026-08-29.md`
**Project:** MPE-Module, branch `main`, HEAD `cbec874`
**Continuity:** `Documents/reviews/review-audit-looper-multigrid-cycle1-2026-08-28.md`
**Method:** every cited claim traced to `scripts/sooperlooper/*.py` and `tests/*.py` at HEAD;
the three headline 🔴s reproduced with standalone scripts (shown inline below), not inferred
from reading. The test-honesty section was checked by grep/read across the whole `tests/`
tree, not just the files Grumpy says it read.

---

## Work Queue

1. 🔴-1 — stale OVERDUBBING report re-arms the tail; the exit toggles overdub ON
2. 🔴-2 — Stop All leaves a deferred launch armed; the grace timer restarts the track
3. 🔴-3 — `TailPhase.started_at` on a clock the caller cannot inject
4. 🟡-1 — parked-press resume hands the gesture a down with no up, and a silent-drop-on-None-note claim
5. Test-honesty claim: "no Stop All × matrix test... still doesn't exist" (disputed by builder)
6. Test-honesty flags: `fs._tail.saw_loud` private assertion; a test asserting nothing
7. (new, found in this audit) Test-honesty claim: "no cap test through the gesture" (disputed by evidence)

---

## Claim Verification

### 1. 🔴-1 — stale OVERDUBBING report re-arms the tail (`track_gesture.py:275-282`)

| Claim | Verdict | Evidence |
|---|---|---|
| `sync_from_sl` re-arms `_begin_tail()` on *any* report of `SL_STATE_OVERDUBBING`, not only a transition into it | ✅ Confirmed | `track_gesture.py:275-277`: `if sl_state == SL_STATE_OVERDUBBING: if self._tail is None: self._begin_tail()` — no `prev_sl != SL_STATE_OVERDUBBING` guard. |
| A cap-exit sends `overdub`, and a subsequent stale OVERDUBBING report re-arms and the second cap-exit sends `overdub` again, turning it back ON | ✅ Confirmed, reproduced | Repro script (`TrackGesture` alone, no harness): entered tail, cap-expired (`hits == ['overdub']`), fed one more `sync_from_sl(SL_STATE_OVERDUBBING)`, `in_tail` went back to `True`, second cap-expire produced `hits == ['overdub','overdub']`. Exact reproduction of Grumpy's own transcript. |
| `sync_from_sl` is called on every state auto-update, not only on change, so this is reachable via the real feed cadence | ✅ Confirmed | `sl_bench_listener.py`'s on_update calls `fs.sync_from_sl` on every `/sl/#/get state` reply the bench polls for (`BENCH_STATE_MS`), and nothing in the listener suppresses a repeat of the same value — the bench's own `deliver()` test helper is the only place edge-triggering is added, and that is a *harness* convenience, not something the production listener does. So a state report already in flight before the closing `overdub` hit lands is exactly the race Grumpy describes, and it needs no fault injection — ordinary OSC round-trip jitter is sufficient. |

**Verdict: ✅ Confirmed, exactly as described, and independently reproduced.** This is real and reachable in production, not theoretical — the window is the OSC round-trip inside the state-poll cadence, which is precisely what Grumpy states.

### 2. 🔴-2 — Stop All leaves a deferred launch armed (`track_gesture.py` `stop_all_loops`, `slot_runtime.py`)

| Claim | Verdict | Evidence |
|---|---|---|
| `stop_all_loops` does not touch `SlotRuntime` at all | ✅ Confirmed | Read the full function, `track_gesture.py:912-947`. It sends `mute_on`/`pause_on`/tempo-reset OSC and loops over `gestures` calling `fs.awaiting_quantize = False` / `fs._expect(STATE_STOPPED)` / `fs._sync_led()`. No reference to any `SlotRuntime`/`SlotSurface` object anywhere in the function or its signature. |
| `_deferred` and `Track.pending` survive Stop All, so `poll_pending` → `_maybe_resolve` → `expire_deferred` keeps running | ✅ Confirmed | `slot_runtime.reset()` (the only thing that clears `_deferred`/`_awaiting`) is called from `SlotSurface.reset()` (`slot_surface.py:466-467`), which is wired to Reset All, not Stop All (per the bench's own comment structure and Grumpy's citation, consistent with cycle-1's audited architecture). `slot_surface.poll_pending()` (`slot_surface.py:309-339`) iterates `self._rt.tracks()` unconditionally and calls `_maybe_resolve` for any track with `pending is not None` — no check for paused/stopped state anywhere in the loop. `_maybe_resolve` (`slot_surface.py:444-464`) calls `self._rt.expire_deferred(track_index)` whenever `has_deferred` is true, again with no state gate. |
| `expire_deferred` fires purely off elapsed wall time (`self._now() - at < DEFERRED_LAUNCH_GRACE_S`), regardless of engine state | ✅ Confirmed | `slot_runtime.py:330-342`: the only condition is the elapsed-time check; no `sl_state` parameter exists in the method at all. |
| End-to-end: Stop All, then 5s later the track auto-restarts | ✅ Confirmed, reproduced | Repro script: constructed a `SlotRuntime`, queued a switch (`has_deferred` → `True`), advanced the injected clock past `DEFERRED_LAUNCH_GRACE_S` **without calling anything Stop-All-shaped on the runtime** (faithfully modelling that `stop_all_loops` never touches it), then called `expire_deferred(0)` directly: it returned `True` and sent `load_loop` + `pause_off` + `trigger` — the exact restart Grumpy describes. |

**Verdict: ✅ Confirmed, exactly as described, and independently reproduced.** This is the most serious finding in the cycle: the panic-button gesture does not cancel in-flight runtime intent, and the failure mode (an unattended restart 5 seconds after the player believes everything is silenced) is silent and specifically timed to look like nothing happened for a beat before it does.

### 3. 🔴-3 — `TailPhase.started_at` uses `time.monotonic()`, not the injected clock

| Claim | Verdict | Evidence |
|---|---|---|
| `_tail_clock()` hardcodes `time.monotonic()`, while `poll_tail`/`sync_in_peak` accept an injected `now` | ✅ Confirmed | `track_gesture.py:367-368` (`_tail_clock`) vs. `:391-393` (`sync_in_peak(..., now=...)`) and `:405-419` (`poll_tail(..., now=...)`). |
| This makes the cap untestable through the gesture, and the cap has **no wiring coverage** | ❌ **Incorrect** | `tests/test_gesture_against_engine.py:90`: `fs.poll_tail(now=fs._tail.started_at + fs._tail.cap_s + 0.01)` — this is a cap test through the actual `TrackGesture`/`FakeSlEngine` wiring (`test_pad_never_goes_solid_green_before_the_engine_confirms`), and it passes (`1 passed` when run in isolation). It works around exactly the asymmetry Grumpy flags — by reading `fs._tail.started_at` (which *was* set from the real clock) rather than trying to inject an arbitrary `now` at construction — which is precisely how a test author would route around a hardcoded clock. Grumpy's own inventory never lists `test_gesture_against_engine.py` as read, sampled, or excluded; it appears to have been missed entirely, not judged and dismissed. |
| Not a live bug: production always passes `now=None` on both sides, so both use `monotonic()` | ✅ Confirmed | No call site anywhere in `scripts/sooperlooper/` passes a non-`None` `now=` to `poll_tail`/`sync_in_peak` except in tests. Grepped the full tree. |

**Verdict on severity: this is not P0.** Grumpy's own text already concedes "this is not a live bug," and the "untestable, so it shipped a bug with no coverage" argument — the only thing that would justify a P0 despite that concession — is false: a cap test exists, passes, and would have failed had `poll_tail`'s cap logic been broken the way §🔴-1 or a hypothetical off-by-one would break it. What remains is a legitimate **type/DX inconsistency** (one clock injectable at the call site, one hardcoded at construction, on the same object) that is worth cleaning up so future tests don't have to route around it via a private attribute — that is a P2, not a P0 and arguably not even a P1.

### 4. 🟡-1 — parked-press resume hands the gesture a down with no up (`slot_surface.py:329-333`)

| Claim | Verdict | Evidence |
|---|---|---|
| `poll_pending`'s resume path calls `self._hand_to_gesture(plan, tap=False)`, which resolves to `fs.on_pad_down()` with no matching `on_pad_up()` | ✅ Confirmed | `slot_surface.py:329-333` (resume call site) → `_hand_to_gesture` (`slot_surface.py:201-242`): `if tap: fs.synthesised_tap() else: fs.on_pad_down()`. `tap=False` is passed explicitly at the resume call site. |
| The gesture is left latched (`_pad_down = True`, `_hold_fired = False`) with no real pad held | ✅ Confirmed | `on_pad_down()` (`track_gesture.py:730-735`) unconditionally sets `self._pad_down = True`; nothing in the resume path or elsewhere calls `on_pad_up()` for this synthetic press. `hold_blink_pending`-equivalent (`_hold_led_lock`, `poll_hold`) both read `self._pad_down`, so the pad's hold-blink and hold-timer machinery now runs against a press nobody is making, and (per `poll_hold`, `track_gesture.py:774-786`) after `hold_s` it will fire `_cancel_recording()` or `_clear_loop()` on a track the player is not touching — exactly Grumpy's stated consequence. |
| Second defect: `note=None` resolves via `note_for_cell`, and a bank change during the parked wait makes that return `None`, so the whole handoff silently returns, after `_prepare_record` already moved the binding | ✅ Confirmed | `_hand_to_gesture`: `if note is None: note = self._view.note_for_cell(...)`; `if note is None: return` (`slot_surface.py:218-221`) — a bare `return`, no log, no repaint. `_prepare_record` (`slot_runtime.py:387-396`) moves `active_slot` to the new slot unconditionally before this handoff runs, so the runtime's bookkeeping and the engine's actual state diverge silently in exactly the sequence Grumpy describes. |

**Verdict: ✅ Confirmed, both sub-claims.** This is a real, demonstrable defect on the resume path — not as severe as the two 🔴s (it requires the parked-press-during-save window, which is narrower than "every Stop All" or "every stale OVERDUBBING report"), but it is a real latch, and Grumpy's P2/P1 sibling framing (bundled into the `_awaiting`/`_deferred` ownership fix) is reasonable. I'd keep it at P1, not upgrade it — the window (a save in flight when the resume fires) is materially narrower than the two 🔴s.

### 5. Test-honesty claim: "no Stop All × matrix test... still doesn't exist" — **the builder is right, Grumpy is wrong**

| Claim | Verdict | Evidence |
|---|---|---|
| "No Stop All × matrix test. The cycle-1 audit filed this as its own P1... It still does not exist" | ❌ **Incorrect** | `tests/test_multiclip_workflow.py:426-476`, class `StopAllThenLaunchTests`, added in commit `e0ffeae` (`test(slots): make the harness able to see the bugs that shipped`). It contains exactly two tests: `test_restarting_the_active_clip_after_stop_all` (explicitly docstringed by the builder as "the GESTURE path, not the launch path — kept as a separate scenario... naming which path it exercises stops it being mistaken for coverage of LAUNCH_COMMANDS") and `test_a_second_clip_launches_after_stop_all`. |
| Does the second test actually exercise the matrix **launch** path (`ACT_LAUNCH`/`ACT_SWITCH` → `_defer_launch`/`_launch`/`LAUNCH_COMMANDS`), not just the gesture's `ACT_FORWARD` lane? | ✅ Confirmed it does | Traced the fixture: `record_clip(0)` then `record_clip(1)` (both default `track=0`) leaves `active_slot == 1` (the second recording, per `_prepare_record`'s binding-move rule). `stop_all()` pauses the track. `SL_STATE_PAUSED = 14` is **not** in `ACTIVE_PLAY` (`sl_loop_states.py:12-14`), so a subsequent `tap(0)` (slot 0, not the active slot 1) goes through `plan_cell_press` → `ACT_SWITCH`/`ACT_LAUNCH` → `_execute_slot_ops`'s `else` branch ("nothing is sounding... the launch is immediate") → `_launch()`, which is exactly the function that emits `LAUNCH_COMMANDS` (`pause_off`, `trigger`) — the code this test's own docstring says it exists to guard. Confirmed by reading `_execute_slot_ops` (`slot_runtime.py:300-305`) and `_launch` (`:399-423`) directly, not inferred from the docstring. |

**This test class is exactly the coverage cycle-1's audit asked for**, and it demonstrably exercises the matrix launch path, not just the gesture lane. Grumpy's claim that this coverage "still does not exist" is factually wrong — it exists, by name, with a docstring explaining precisely why it was added and which cycle-1 bug it targets.

**What Grumpy is right about, adjacent to this:** neither `StopAllThenLaunchTests` test covers the *different*, cycle-2-introduced bug (🔴-2 above) — a launch **queued before** Stop All is pressed, left deferred, and restarting 5s later via `expire_deferred`. Both existing tests start the matrix press *after* `stop_all()`, when the track is already `PAUSED` (not `ACTIVE_PLAY`), so neither ever populates `_deferred` at all — they can't, because the immediate-launch branch is taken, not `_defer_launch`. So: **the specific gap "no test for a deferred launch orphaned by Stop All" is real and is what §4🔴-2 walked through**, but that is not the same claim as "no Stop All × matrix test exists," and Grumpy's test-honesty section conflates the two. This should be corrected in the write-up rather than accepted as stated.

### 6. Test-honesty flags — private-attribute assertion and a no-op test

| Claim | Verdict | Evidence |
|---|---|---|
| `test_a_peak_reaches_the_gesture` asserts `fs._tail.saw_loud` (private) instead of the observable `hit overdub` | ✅ Confirmed, fair | `tests/test_tail_integration.py:113`: `self.assertTrue(fs._tail.saw_loud, "the peak never arrived")`. The test could equivalently drive one more quiet peak and assert `osc.hits().count("overdub") == 1`, which would survive a refactor of `TailPhase`'s internals. Fair, not a nitpick — it is exactly the kind of test that would need rewriting (not just re-running) if `_tail`'s shape changed, for no product reason. |
| `test_a_peak_for_an_unbound_loop_does_not_raise` asserts nothing | ✅ Confirmed, fair | `tests/test_tail_integration.py:115-116`: two lines, `listener = SlBenchStateListener({})`; `listener.on_update("/x", 7, "in_peak_meter", 0.9)` — no `assert*` call at all. It is a smoke test wearing a test's clothes, exactly as Grumpy says. Not a nitpick: a test with no assertion always "passes," including after a regression that makes it silently swallow an exception it should have let through (or vice versa) — this is a real, if minor, quality gap. |

**Verdict: both fair**, and correctly rated 🟢/low-priority by Grumpy rather than inflated.

### 7. (New finding, not in Grumpy's own claims) — Grumpy's stated "read in full" / "sampled" inventory does not include the file that falsifies §6.3

Grumpy's method note lists `test_tail_integration.py` and `test_tail_phase.py` as read in full, and does not list `test_gesture_against_engine.py` anywhere (not read, not sampled, not excluded). That file is exactly where the actual cap-through-the-gesture test lives. This is the same class of self-blind-spot cycle-1's audit found in Grumpy's line-citation drift — a review whose value is "I checked X and it's missing" needs to search the whole tree for X, not just the files it already opened for other reasons. Recommend Grumpy widen its test-discovery method (`grep -rn "poll_tail\|saw_loud\|OVERDUBBING" tests/` before writing a "does not exist" claim) in future cycles.

---

## Severity Re-Assessment

| # | Issue | Reviewer Rating | My Rating | Delta | Reasoning |
|---|---|---|---|---|---|
| 1 | Stale OVERDUBBING re-arms tail, exit toggles overdub ON | 🔴 / P0 (backlog #2) | **Critical / P0** | agree | Confirmed and reproduced exactly. Silent, indefinite, and lands on the take-closing path every performance exercises. |
| 2 | Stop All leaves a deferred launch armed; restarts 5s later | 🔴 / P0 (backlog #1) | **Critical / P0, and I agree it should rank above #1** | agree with Grumpy's own ranking | Confirmed and reproduced exactly. This is the single worst finding this cycle: it defeats the one gesture whose entire job is "make it stop, unconditionally," and it does so silently and on a delay that makes it hard to connect cause to effect on stage. |
| 3 | `TailPhase.started_at` clock mismatch / "cap has no wiring coverage" | 🔴 / P0 (backlog #3) | **P2** | ↓↓ | The clock-injection asymmetry is real and worth fixing, but the specific claim that makes it P0-worthy ("no wiring coverage," "it makes the cap untestable through the gesture") is false — `test_gesture_against_engine.py:90` is exactly that test, and it passes. What remains is a design-consistency cleanup (inject the clock at construction, drop the per-call `now=`), which is real but not urgent — nothing ships broken because of it. |
| 4 | Parked-press resume: down-with-no-up + silent drop on bank-change | 🟡 / not separately ranked (folded into backlog #5) | **P1** | agree with Grumpy's implicit rating | Confirmed both sub-claims. Narrower window than #1/#2 (requires a save in flight when the resume fires), so P1 not P0, matching how Grumpy itself treated it (not in the top-3 backlog). |
| 5 | "No Stop All × matrix test... still doesn't exist" | Stated as fact in §6.3 | **Incorrect as stated; the underlying gap it's reaching for is real** | — | See Claim 5 above. The cycle-1 P1 this refers to (a matrix launch *after* Stop All) is covered by `StopAllThenLaunchTests`. The gap that actually produced §4🔴-2 — a launch *deferred before* Stop All, orphaned by it — has no test and is correctly the reproduction ground for #2 above; that specific, narrower claim should replace the broader one in any fix to this write-up. |
| 6 | `saw_loud` private assertion; no-op test | 🟡/🟢 (test-honesty section) | agree | agree | Both fair, both low priority, correctly not overstated. |

---

## What the Review Missed

1. **The `StopAllThenLaunchTests` misattribution (Claim 5).** Already covered above — this is the most consequential miss because it affects how a reader should read the whole test-honesty section: three of five bullets in §6.3 are solid (re-arm test, abort-path table, private-attribute/no-op tests), one is outright wrong (Stop-All-matrix), and one is wrong on its central factual premise while gesturing at something real (the cap test).

2. **Nobody has written the actual missing test for §4🔴-2** (queue a switch, then Stop All, then advance past the grace, assert zero OSC / assert `has_deferred` is false). Grumpy's own fix list (backlog #1) proposes exactly this test but the write-up's test-honesty section doesn't distinguish it from the already-existing `StopAllThenLaunchTests` class, which could lead whoever picks up the fix to believe the wrong thing is missing.

3. **No test exists for the re-arm bug (§4🔴-1) either** — confirmed independently (grepped `tests/` for two consecutive `sync_from_sl(SL_STATE_OVERDUBBING)` calls or any test that calls `sync_from_sl` twice with the same value across a tail boundary; found none). Grumpy's own §6.3 already says this ("No re-arm test. Feeding two consecutive OVERDUBBING reports is a three-line test") — confirmed correct, no dispute.

4. **`FakeSlEngine`'s `deliver()` / test harness pattern is itself edge-triggered** (`test_multiclip_workflow.py`'s `Session.deliver()` only calls `sync_from_sl` when `self._last_state.get(loop) != state`), which is realistic for testing production's `sl_bench_listener` behavior in general, but it also means **the harness cannot produce the exact repeated-state race that causes §4🔴-1** without a test author deliberately calling `fs.sync_from_sl(...)` twice by hand outside the `deliver()` helper (as my repro script did). This is worth noting as a second-order harness gap beyond what Grumpy's §6.1–6.3 already covers: even a well-intentioned author reaching for `self.deliver()` twice in a row inside `test_multiclip_workflow.py` would not reproduce §4🔴-1, because `deliver()`'s own dedup (`_last_state`) suppresses the repeat. A test for this bug has to bypass the `Session` harness and drive the gesture directly, the way `test_gesture_against_engine.py` and my repro do.

Beyond these points, Grumpy's coverage of the actual defect classes in this cycle is thorough and its reproductions (§6.2, its own worktree check) are real and correctly executed — I did not find additional live defects in the files it examined that it missed outright.

---

## What the Review Got Right (And Why It Matters)

**§4🔴-1 and §4🔴-2 are both exactly as described, independently reproduced, and correctly identified as this cycle's worst findings.** Their compounding is worth stating explicitly, as cycle-1's audit did for that cycle's pair: a performer's most likely response to §4🔴-1 (an unexplained overdub) is to hit Stop All — which is exactly the gesture §4🔴-2 shows does not fully work. A player chasing down one silent bug lands directly in the other.

**§6.2's worktree-based verification of the builder's "these tests fail against pre-fix code" claim is exactly the right kind of rigor** — disabling the deferral and running the affected suites rather than trusting the commit message. That discipline is why the four genuinely-discriminating tests it found (`test_the_hold_survives_until_the_grace_runs_out` in particular) can be trusted, and it is the same discipline this audit tried to bring to Grumpy's own test-honesty claims — which is exactly where this audit found Grumpy's one real miss (Claim 5).

---

## Prioritized Action Matrix

| Priority | Issue | Verdict | Effort | Depends On |
|---|---|---|---|---|
| P0 | Stop All leaves a deferred launch armed in `SlotRuntime`; `expire_deferred` fires 5s later and restarts the track | ✅ Confirmed, reproduced | Quick fix — add `SlotRuntime.abandon_pending()` (clears `_deferred`, `_awaiting`, every `Track.pending`) and call it from the bench's Stop All branch | — |
| P0 | A stale OVERDUBBING report re-arms the tail; the cap then sends `overdub` again, turning it back ON with nothing to turn it off | ✅ Confirmed, reproduced | Quick fix — guard `_begin_tail` on `prev_sl != SL_STATE_OVERDUBBING` | — |
| P1 | Parked-press resume calls `on_pad_down()` with no matching `on_pad_up()`, latching the gesture; silently drops the handoff if the bank moved and the note resolves to `None` | ✅ Confirmed | Quick fix (`tap=True`) + log-not-silent-return for the off-view drop | — |
| P1 | `_awaiting`/`_deferred` survive `_clear`, `forget_active_slot`, and (for `_awaiting`) bank change | ✅ Confirmed (from cycle-2 review, independently spot-checked for `set_view`) | Half-day — one `abandon(track)` called from each site, plus a table test over (abort path × parked state) | Subsumes the Stop-All fix above |
| P1 | Write the actual missing test: queue a switch, Stop All, advance past `DEFERRED_LAUNCH_GRACE_S`, assert zero OSC and `has_deferred() is False` | ✅ Confirmed missing (distinct from the already-existing `StopAllThenLaunchTests`) | Quick fix | The P0 fix above, so it's red→green |
| P1 | Write the missing re-arm test: two consecutive `sync_from_sl(SL_STATE_OVERDUBBING)` calls across a cap exit send exactly one `overdub` | ✅ Confirmed missing | Quick fix | The 🔴-1 fix above |
| P2 | `TailPhase`'s clock is injectable at the call site (`now=`) but hardcoded at construction (`_tail_clock`) — inconsistent with `SlotRuntime`'s constructor-injected `now` | ✅ Confirmed, downgraded from Grumpy's P0 | Half-day — inject at construction, drop per-call `now=` | — |
| P2 | `test_a_peak_reaches_the_gesture` asserts private `fs._tail.saw_loud` instead of the observable `hit overdub` | ✅ Confirmed, fair | Quick fix | — |
| P3 | `test_a_peak_for_an_unbound_loop_does_not_raise` asserts nothing | ✅ Confirmed, fair | Quick fix (assert the peak was dropped, or delete) | — |
| P3 | `_defer_launch`'s `retrigger_only` branch is unreachable (second copy of dead vocabulary cycle-1 flagged once already) | ✅ Confirmed by cycle-2 review, not independently re-verified line-by-line but consistent with cycle-1's established unreachability argument | Quick fix (delete) | — |
| P3 | `boundary()` still named for a thing it doesn't do, now worse since `expire_deferred()` exists specifically to call it "when no boundary arrived" | ✅ Confirmed (naming only) | Quick fix (rename) | — |

---

## Disagreements and Judgment Calls

1. **P0-3 does not belong at P0.** Grumpy's own text concedes "this is not a live bug" and then keeps it in the numbered 🔴 backlog anyway, justified by "it makes the cap untestable... and it has no wiring coverage." That justification is factually wrong (§6.3 verification above) — a cap test through the gesture exists and passes. I moved this to P2. This is the one place in the cycle-2 review where the severity is meaningfully inflated relative to what the evidence supports, not merely a stylistic disagreement.

2. **The "no Stop All × matrix test" claim in §6.3 should be replaced, not repeated, in any follow-up.** Grumpy is describing a real gap (no test for a launch deferred *before* Stop All), but states it as if the cycle-1-requested test doesn't exist at all, when it does (`StopAllThenLaunchTests`, two tests, one of which explicitly documents which path it does *not* cover). Whoever picks up this backlog should write the narrower test I specify above, not assume `test_multiclip_workflow.py` needs a new test class from scratch.

3. **No disagreement with the core diagnosis of §4🔴-1 and §4🔴-2**, nor with Grumpy's overall framing that this codebase keeps producing "a state machine with an entry condition and no latch." Both reproduced exactly as described and both independently confirmed as reachable via realistic production timing, not fault injection.

4. **No disagreement with the 🟡-1 (parked-press) finding or its P1-adjacent treatment**, nor with the fair, correctly-scoped 🟢/test-honesty flags in §6.3 for `saw_loud` and the no-op test.
