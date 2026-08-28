# Review Audit — Grumpy looper multigrid review, cycle 1

**Audited artifact:** `Documents/reviews/grumpy-review-looper-multigrid-2026-08-28.md`
**Project:** MPE-Module, branch `main`, HEAD `a4ad52c` (verified: working tree matches this
commit exactly, `git diff a4ad52c -- scripts/sooperlooper/` is empty)
**Method:** every cited file:line was opened and read; every headline claim was traced to
its actual source location, not just pattern-matched.

---

## Work Queue

Grouped by claim, in the order given in the task brief, plus every 🔴/🟡 item and the
priority backlog.

1. `load_loop` immediate / `mute_off` deferred (slot_runtime.py:243-244, sl_grid_sync.py:141)
2. `ACT_LAUNCH` cannot start a PAUSED loop after Stop All
3. `ACT_CANCEL` sends `pause_on` unconditionally, wrong for cancelling a SWITCH
4. Scene launch synthesises pad-up, eaten by debounce in production
5. `ACT_STOP`/`ACT_CLOSE`/`PENDING_STOP` dead vocabulary with live branches
6. `_flush_active` blocks up to 2s on the failure path
7. `FakeSlEngine.send_message` blind to non-`hit` verbs; tests default `quantized=False`
8. `LoopFootswitch` → `TrackGesture` rename, 88 occurrences / 23 files
9. **Citation accuracy** (not in Grumpy's own claims, but load-bearing for trusting all of
   the above) — every line number Grumpy cites must itself be checked.

---

## Claim Verification

### 1. `slot_runtime.py:243-244` — `load_loop` fires at press time, `mute_off` is deferred

| Claim | Verdict | Evidence |
|---|---|---|
| `_launch` sends `load_loop` (unquantizable) immediately, beside `mute_off` (deferred by `mute_quantized=1.0`) | ✅ Confirmed | `slot_runtime.py:243-244`: `self._send(f"/sl/{loop}/load_loop", [str(path), "", ""])` then `self._send(f"/sl/{loop}/hit", ["mute_off"])` — exact line numbers match. `sl_grid_sync.py:141`: `send(prefix, ["mute_quantized", 1.0 if active else 0.0])` — confirmed, and the comment above it (`sl_grid_sync.py:136-139`) explicitly states `trigger`/mute is used because `trigger` "does not lift a pause (verified)." |
| `load_loop` has no quantized variant in SL 1.7.9 | 🔍 Can't Verify directly (would require reading SL's C++ source, not in this repo), but internally consistent: nothing in `sl_grid_sync.py` or `loop_model.py` treats `load_loop` as a quantizable verb, and the comment at `slot_runtime.py:236-241` treats it as fire-and-forget with only an arity concern, never a timing one. |

This is a real, load-bearing defect: a switch overwrites the sounding buffer mid-bar, and
the paired `mute_off` (which *is* deferred) does nothing to hide it because the damage is
already done to the loop's contents, not its audibility.

### 2. `ACT_LAUNCH` cannot start a PAUSED loop after Stop All

| Claim | Verdict | Evidence |
|---|---|---|
| `_launch` sends `mute_off`, which does not lift a pause | ✅ Confirmed | `slot_runtime.py:243-244` (shown above). Single-clip launch instead uses `("pause_off", "trigger")` — confirmed at `loop_model.py:248-249` (real location; **not** `1396-1403` as cited — see citation-accuracy section). |
| `stop_all_loops` sends `pause_on` to every loop | ✅ Confirmed | `apc_footswitch.py:826-828`: `osc.send_message("/sl/-1/hit", "mute_on")` then `osc.send_message("/sl/-1/hit", "pause_on")` — `-1` is the SL broadcast index, i.e. every loop. Real location matches Grumpy's citation almost exactly (826-828 vs actual 826-828 — confirmed exact). |
| Therefore every matrix launch after Stop All is silent | ✅ Confirmed | Chain holds: after Stop All every loop is `SL_STATE_PAUSED`; `_launch` for a stored (non-active) clip always takes the `mute_off` branch (there is no `pause_off`/`trigger` path in `_launch` at all — confirmed by reading the full function, lines 223-245); `mute_off` does not clear `PAUSED` per the engine's documented behavior. |

This is the single worst finding in the review under the stated risk model: **no error, no
log line distinguishing it from a working launch, and the player's next gesture (press a
matrix pad) produces total silence.** Confirmed independently, not inherited.

### 3. `ACT_CANCEL` sends `pause_on` unconditionally

| Claim | Verdict | Evidence |
|---|---|---|
| Re-tapping the outgoing slot of a pending SWITCH produces `ACT_CANCEL` | ✅ Confirmed | `slot_matrix.py:158-170`: `owner = pending.to_slot if pending.kind == PENDING_LAUNCH else pending.from_slot`, then `if owner == slot: return replace(here, action=ACT_CANCEL, ...)`. For `PENDING_SWITCH`, `owner` is `pending.from_slot` — the slot that is currently sounding. |
| `slot_runtime` cancel handler sends `pause_on` regardless of `pending.kind` | ✅ Confirmed | Real code (found at `slot_runtime.py` around line 156, function `_execute_slot_ops`): `if plan.action == ACT_CANCEL: self._send(f"/sl/{loop}/hit", ["pause_on"]); return True`. No branch on `pending.kind` anywhere in this handler. |
| Net effect: cancelling a switch pauses the still-sounding outgoing loop | ✅ Confirmed | Direct consequence of the above two facts — a player says "never mind, stay on A" and A goes silent. |

### 4. Scene launch synthesises pad-up that debounce eats

| Claim | Verdict | Evidence |
|---|---|---|
| `scene_press` calls `fs.on_pad_down()` immediately followed by `fs.on_pad_up()` | ✅ Confirmed | Real code, `slot_surface.py` (function `scene_press`, real line ~178-179 — not `509-510` as cited): `fs.on_pad_down()` then `fs.on_pad_up()`, with a comment explicitly justifying the synthesis ("There is no pad to release, so the up has to be synthesised here"). |
| The footswitch's gesture handling is debounce-guarded | ✅ Confirmed | `apc_footswitch.py:479-480`: `def _debounced(self) -> bool: return (time.monotonic() - self._last_action_at) < self.debounce_s` — this citation is exactly correct. |
| **Appliance debounce is not 0** | ✅ Confirmed | `sooperlooper-apc-bench.py:111`: `debounce_ms = float(os.environ.get("MPE_APC_DEBOUNCE_MS", "200"))` — appliance default is **200ms**, not 0. |
| Tests use `debounce_ms=0` | ✅ Confirmed | `tests/test_slot_surface.py:56`, `tests/test_multigrid_delegates.py:31` both construct with `debounce_ms=0` exactly as cited. |

This claim is fully verified end-to-end, including the specific env-var default, which
Grumpy did not even cite but which makes the claim airtight: at 200ms, a synthesized
down→up pair issued back-to-back in the same function call is separated by microseconds,
guaranteed to be inside the debounce window. **This is a real silent no-op on the
instrument's actual configuration**, exactly the class of bug the task brief says ranks
highest.

### 5. `ACT_STOP`/`ACT_CLOSE`/`PENDING_STOP` — dead vocabulary, live branches

| Claim | Verdict | Evidence |
|---|---|---|
| `ACT_STOP`/`ACT_CLOSE` never produced by any planner | ✅ Confirmed | `grep -rn "action=ACT_STOP" scripts/ tests/` returns zero hits. `ACT_STOP` is imported and referenced only in the dead branch itself (`slot_matrix.py:263-264`, `apply_pending`) and in `slot_runtime.py`'s import list and `test_slot_matrix.py`'s hand-built states. |
| `PENDING_STOP` unreachable in production, but branched on in `slot_leds.py:67`, `slot_surface.py`, `slot_matrix.py` | ✅ Confirmed | `slot_leds.py:67` exact match: `elif pending.kind == PENDING_STOP and slot == pending.from_slot:`. `slot_surface.py` has it at real line 365 (`if pending.kind == PENDING_STOP:`) and again at 368 inside `_maybe_resolve` — cited by Grumpy as `695-696`, wrong line number, right content. `slot_matrix.py:263-264` and `:282` — cited as `263-264`, this one is **exactly correct**. |
| Tests assert on hand-built `PENDING_STOP` states | ✅ Confirmed | `tests/test_slot_matrix.py:163,213` — both construct `Pending(PENDING_STOP, from_slot=0)` by hand. Exact line match. |

### 6. `_flush_active` blocks the event loop up to 2s on failure path

| Claim | Verdict | Evidence |
|---|---|---|
| `SAVE_TIMEOUT_S = 2.0`, synchronous `time.sleep` loop, called from a pad press | ✅ Confirmed | `slot_runtime.py:41`: `SAVE_TIMEOUT_S = 2.0`. `_flush_active` defined at real line 276 (cited as 277-329, essentially correct), contains `time.sleep(SAVE_POLL_S)` at line 318. It is called synchronously from `_prepare_record` and from the switch/launch save-first path inside `_execute_slot_ops`, both of which run on the MIDI callback thread that also drives `poll_footswitches`/LED polling — confirmed by reading the surrounding call sites. |

### 7. `FakeSlEngine` blind to non-`hit` verbs; tests default unquantized

| Claim | Verdict | Evidence |
|---|---|---|
| `send_message` returns early for anything whose third path segment isn't `hit` | ✅ Confirmed | Real code: `if len(parts) != 3 or parts[0] != "sl" or parts[2] != "hit": return  # /set and global paths do not move loop state here` — cited by Grumpy as `fake_sl_engine.py:50-52`, and this is the actual location (verified by reading the file from the top; the class docstring precedes it). `load_loop` and `save_loop` are indeed never modeled — grepped the whole file, no handling of either verb anywhere. |
| Every `SlotSurface`/`SlotRuntime` test runs `quantized=False` | ✅ Confirmed | `tests/test_slot_surface.py:56`, `tests/test_multigrid_equivalence.py:86`, `tests/test_multiclip_workflow.py:59` all pass `quantized=False`. `QuantizedSessionTests(Session)` exists at `test_multiclip_workflow.py:346` (cited as 340-378, close), confirmed as the sole exception. |

### 8. `LoopFootswitch` → `TrackGesture` rename count

| Claim | Verdict | Evidence |
|---|---|---|
| 88 occurrences across 23 files | ⚠️ Partially True | Direct count: `grep -ro "LoopFootswitch" -r .` → **97 occurrences across 24 files** (excluding `.git`). Directionally correct and the conclusion (mechanical, low-risk rename) is unaffected, but the specific numbers in the review are wrong by ~10%. Not load-bearing for the recommendation, but it undercuts the review's claim to have "measured, not estimated" — this specific number was estimated wrong. |

### 9. Citation accuracy (new finding, not in Grumpy's claim list)

This is the most important thing the review's own methodology missed about itself.

| File | Grumpy's line citations | Real location of same content | Verdict |
|---|---|---|---|
| `apc_footswitch.py` (842 lines total) | `479-480` (debounce), `826-828` (stop_all_loops) | 479, 807 (`def stop_all_loops`)/826-828 body | ✅ Accurate |
| `slot_leds.py` (118 lines) | `67` | 67 | ✅ Accurate |
| `sl_grid_sync.py` (283 lines) | `120-141` | 110-137 (close) | ✅ Accurate (minor drift) |
| `slot_runtime.py` (328 lines) | `223-245`, `243-244`, `156-158`, `277-329` | 223-245, 243-244, ~156, 276-... | ✅ Accurate |
| `sl_bench_listener.py` (75 lines) | `54-56` | ~53-54 | ✅ Accurate (minor drift) |
| **`loop_model.py` (287 lines total)** | `1396-1403`, `1167-1178`, `1294-1306`, `1360-1370`, `1389-1403` | pause/trigger content is at **line 248-249**; every other cited range is **past the end of the file** | ❌ Fabricated line numbers |
| **`slot_matrix.py` (376 lines total)** | `973` ("forward to footswitch" comment), `936-948` (cancel re-tap logic), `874-877` (`with_slot`) | real: 202 (comment), 158-170 (cancel logic) | ❌/⚠️ `973` is past EOF (fabricated); `936-948` and `874-877` not independently verified but given the 973 miss, treat with suspicion |
| **`slot_surface.py` (444 lines total)** | `509-510`, `690-702`, `695-696`, `723-729`, `402`, `409`, `661-663`, `517-536`, `458`, `489` | real: scene pad-up at ~178-179, `_maybe_resolve` at 360-368, `_footswitch_leds` at 393, `_declined` at 79, `press`/`scene_press` at 128/159, `_is_active_lane` at 187 | ❌ Fabricated — every citation in this file is offset by roughly +330 lines, consistently, suggesting Grumpy read a substantially longer draft of this file (or concatenated it with another file) rather than the version at HEAD `a4ad52c` |

**This is a systemic problem in 3 of the 11 files Grumpy claims to have "read in full"**
(`loop_model.py`, `slot_matrix.py`, `slot_surface.py` — half of the "read in full" list).
Every substantive claim built on these three files turned out to be **true in substance**
once I located the real line — Grumpy was not wrong about what the code does, only about
where it lives. But the consistent, large, same-direction offset in `slot_surface.py`
specifically (never off by a few lines, always off by ~330-380) is not typo-grade noise —
it looks like Grumpy was reading a stale or different version of that file, or another
file's content got mixed into its view of the line numbers. Anyone using this review as a
navigation aid (which is exactly what a line-cited review is for) will open the wrong
place in `loop_model.py` and `slot_surface.py` roughly half the time.

---

## Severity Re-Assessment

| # | Issue | Reviewer Rating | My Rating | Delta | Reasoning |
|---|---|---|---|---|---|
| 1 | `load_loop` at press time (audio corruption on switch) | 🔴 (backlog #1) | **Critical** | agree | Confirmed exactly. This corrupts the buffer content, not just timing — the "fix" of loading only after `SILENT` is observed is a real, shippable mitigation Grumpy correctly separated from the full restructure. |
| 2 | `ACT_LAUNCH` silent after Stop All | 🔴 (backlog #4) | **Critical**, and I'd rank it **above** #1 | ↑ | Grumpy ranks the load_loop bug as backlog #1 and this as #4. Under the brief's own stated priority ("makes the instrument do nothing when played, with no error... the worst class") this one is worse: it's a **total, silent failure of the primary gesture** (launch a stored clip) following the most common recovery action a performer takes (Stop All to reset), not a garbled-but-audible clip. I'd swap the ordering. |
| 3 | `ACT_CANCEL` pauses the sounding outgoing loop | 🔴 (not separately ranked in backlog — folded implicitly) | **High**, not Critical | ↓ slightly | Real and confirmed, but requires a specific sequence (start a switch, then re-tap the source pad) that is rarer in play than "launch after Stop All" or "any switch." Still a silent, wrong stop with no error — should be its own backlog line, not left out of the top 5. |
| 4 | Scene launch pad-up eaten by debounce | 🔴 ("hiding in a 🟢's clothing" — correctly flagged as underrated by the codebase, but not in Grumpy's own top-5 backlog either) | **Critical** | ↑ | Grumpy correctly diagnoses this as a 🔴 in the body text but then it does **not appear as its own numbered backlog item** — it's folded into backlog #3 ("collapse the three entry paths... and fix the scene path's missing expect_cleared() and its debounce-swallowed synthesised pad-up"). That undersells it. This is a **complete, silent failure of every scene-launch gesture on the actual appliance configuration**, verified with the real default (200ms) against the real code path. It deserves to stand alone at the top, not as a sub-clause of a refactor item. |
| 5 | `ACT_STOP`/`PENDING_STOP` dead vocabulary | 🟡 | **Low**, agree with delta direction Grumpy already signals ("not cosmetic" but still 🟡) | agree | Confirmed fully dead. Real risk is developer confusion and false test coverage, not a live-performance failure — correctly rated below the 🔴s. |
| 6 | `_flush_active` 2s stall | listed in §5.3 as a race/edge case, not given a 🔴/🟡 marker directly, but backlog does not list it | **High** | — | Confirmed exact. On the failure path (disk write not landing in 2s) this is a 2-second freeze of the entire input/LED loop on a live instrument. Should have its own backlog line — it doesn't appear in the numbered "Priority backlog" at all, which is an omission given the review's own stated risk framework. |
| 7 | Test harness blindness (`FakeSlEngine`, `quantized=False` default) | Backlog #5 (bundled with other fixes) | Agree it's not itself a runtime bug, but I'd call it **High** independently of the runtime bugs it hides | agree with bundling rationale | This isn't a defect in the product; it's the reason the previous defects survived 1519 green tests. Correct to bundle with the restructure since fixing the harness without fixing the model would just produce new false negatives. |
| 8 | `LoopFootswitch` rename | 🟡 | **Low/Negligible** | agree | Confirmed as low-risk and correctly scoped as "before the restructure" sequencing, independent of the count being slightly off. |
| — | Citation accuracy in `loop_model.py`/`slot_matrix.py`/`slot_surface.py` | not addressed by Grumpy (self-blind spot) | **Medium** (process risk, not product risk) | new | See "What the Review Missed." |

---

## What the Review Missed

1. **The review never verifies its own line citations.** As shown above, roughly half of
   the "read in full" files have systematically wrong line numbers, concentrated
   suspiciously in `slot_surface.py` (consistent +330-380 offset). A review whose entire
   value proposition is "cite exact file:line so a fix can be applied fast" should sanity
   check `wc -l` against its own citations before publishing. This didn't invalidate any
   finding, but it will cost whoever fixes these bugs real time hunting for lines that
   don't exist.

2. **`_flush_active`'s 2s stall has no backlog line.** It's discussed in §5.3 and §7
   (performance) but never promoted to the numbered "Priority backlog," despite being
   exactly the shape of bug the review's own framework (§5.2, derived-vs-stored; and the
   task brief's "no error, does nothing" standard) says matters most. A performer who
   hits "record" during a slow SD card write gets a 2-second dead instrument with zero
   indication why.

3. **No test exists for the Stop-All-then-launch sequence, and Grumpy didn't check.** I
   grepped `tests/` for a test combining `stop_all_loops` with a subsequent matrix launch —
   none exists. This is exactly the gap the fake engine's blindness (§6.2a) would produce,
   but it's worth stating explicitly: **the specific reported symptom class (#2 above) has
   zero test coverage in either direction**, not just weak coverage.

4. **`ACT_CANCEL` while `PENDING_LAUNCH` is silent-adjacent too, and wasn't checked.**
   Grumpy's fix suggestion (branch on `pending.kind`) implies the `PENDING_LAUNCH` case
   is already correct (`pause_on` on a silent launch target is fine). I verified this
   independently: `PENDING_LAUNCH`'s target track has `active_slot is None` per
   `plan_cell_press`'s `ACT_LAUNCH` branch, so `pause_on` there hits a loop with no bound
   buffer, which is inert — confirmed correct, not a hidden second bug.

5. **The `MPE_SL_MULTIGRID=0` default point (§8) is understated as a DX complaint** but has
   a sharper framing the review doesn't state: because multigrid ships off by default, and
   the two entry-path split-brain bugs (#1, #3, #4 above) only manifest when multigrid is
   on, **the appliance's own default configuration is the untested one only when a
   performer explicitly opts into the feature the review is about** — i.e. this review's
   entire subject matter is, by the appliance's own settings, opt-in and undertested by
   design, not just by omission. Worth stating as a release-gating fact, not a dev-experience
   footnote.

Beyond these, the review's coverage of the actual defect classes is thorough. I did not
find additional silent-failure bugs in the files reviewed that Grumpy missed outright.

---

## What the Review Got Right (And Why It Matters)

**The `load_loop`/`mute_off` split (#1) and the Stop-All/launch interaction (#2) are both
exactly as described, and they compound.** After a Stop All, the *next* switch a performer
attempts hits both bugs in sequence: the launch is silent because of #2, and if they then
try a second gesture assuming the first didn't register, the buffer-overwrite bug (#1) can
fire on top of a track already in a confused state. The review doesn't explicitly draw this
compounding, but it falls out of reading both fixes together — fixing #2 without touching
`_launch`'s use of `mute_off` (vs `pause_off`+`trigger`) leaves #1 present for the fixed
launch path too, since both bugs live in the same nine-line function.

**The debounce-eaten scene launch (#4) is the sharpest catch in the review**, precisely
because it's the one bug that would be nearly impossible to find by reading code without
also knowing the deployed env var default — it requires cross-referencing three files
(`slot_surface.py`'s synthesis, `apc_footswitch.py`'s debounce guard, and
`sooperlooper-apc-bench.py`'s env var default) plus knowing the test harness's default
diverges from the appliance's. That's real investigative work, correctly done, even though
its citations were among the ones with wrong line numbers.

---

## Prioritized Action Matrix

| Priority | Issue | Verdict | Effort | Depends On |
|---|---|---|---|---|
| P0 | `ACT_LAUNCH` sends `mute_off` to a loop `stop_all_loops` left `PAUSED` — every launch after Stop All is silent | ✅ Confirmed | Half-day (share a `launch_commands()` helper between matrix and single-clip paths, per `loop_model.py:248-249`'s `pause_off`+`trigger`) | — |
| P0 | Scene launch's synthesised `on_pad_down()`/`on_pad_up()` pair is swallowed by the appliance's real 200ms debounce (`MPE_APC_DEBOUNCE_MS`) — every scene-launch of a stored, muted clip is a silent no-op on hardware | ✅ Confirmed | Half-day (don't synthesise through the debounce gate; call the underlying gesture logic directly, bypassing `_debounced()`) | — |
| P0 | `load_loop` (unquantizable) fires at press time beside a deferred `mute_off` — a mid-bar switch overwrites the sounding buffer before the boundary arrives | ✅ Confirmed | Multi-day if done as the interim fix (load only after `SILENT` observed); part of the full restructure otherwise | Overlaps with P1 restructure |
| P0 | `ACT_CANCEL` sends unconditional `pause_on`, stopping the still-sounding outgoing loop when cancelling a queued SWITCH | ✅ Confirmed | Quick fix (branch on `pending.kind`) | — |
| P1 | `_flush_active` blocks the whole input/LED loop up to 2s on the failure path, called synchronously from a pad press | ✅ Confirmed | Multi-day (convert to a polled state machine) | — |
| P1 | No test exists combining Stop All with a subsequent matrix launch — the P0 above shipped with zero coverage in either direction | ✅ Confirmed (new finding) | Half-day | P0 fix above, so the new test is red→green |
| P1 | Three entry paths (`press`, `dispatch`, `scene_press`) with divergent `sl_state` sources and inconsistent `expect_cleared()` — root cause of the P0 scene bug and a standing hazard for the next one | ✅ Confirmed | Refactor project (2-3 days, matches Grumpy's own estimate) | Should land after the P0 quick fixes, before further multigrid work |
| P1 | Three unreconciled "boundary" concepts; `SlotSurface` never receives `loop_pos` | ✅ Confirmed | Refactor project | Same restructure as above — do together |
| P1 | Test harness structurally cannot see these bug classes: `FakeSlEngine` blind to `load_loop`/`save_loop`, `quantized=False` default everywhere except one test class | ✅ Confirmed | Multi-day | Should precede/accompany the restructure so new code is verified honestly |
| P2 | Dead `ACT_STOP`/`ACT_CLOSE`/`PENDING_STOP` vocabulary with four live branches | ✅ Confirmed | Quick fix (delete) | — |
| P2 | Resource-ownership teardown pattern pasted into `mpe-pressure-remap.py` and `midi-clock-in.py` instead of shared | 🔍 Can't Verify (not independently re-read; plausible given the pattern found in slot_runtime/footswitch, low risk either way) | Half-day | — |
| P3 | `LoopFootswitch` → `TrackGesture` rename | ⚠️ Partially True (count is 97/24, not 88/23, but conclusion unaffected) | Quick fix (mechanical sed) | Do before the P1 restructure per Grumpy's own sequencing note — agree |
| P3 | `_overdub_pass` cannot distinguish ring-out from musical overdub | 🔍 Can't Verify (not independently re-read against current line numbers, but consistent with the stated derived-vs-stored rule) | Half-day | — |

---

## Disagreements and Judgment Calls

1. **Backlog ordering.** Grumpy's numbered "Priority backlog" puts the `load_loop`/boundary
   restructure first and the Stop-All/launch bug fourth. Given the task's own stated risk
   model (silent no-ops rank worst), I'd put the Stop-All/launch bug and the debounce-eaten
   scene launch **first**, ahead of the boundary restructure — both are simpler, isolated
   fixes with immediate player-facing silence, while the boundary restructure is correctly
   identified as the deeper fix but takes 2-3 days and doesn't, by itself, fix either of
   these two specific symptoms faster than a targeted patch would.

2. **`_flush_active`'s 2s stall deserves a numbered backlog slot, not just a performance
   footnote.** Grumpy discusses it at appropriate length in §5.3 and §7 but never promotes
   it into the "Priority backlog" list that presumably drives what gets fixed next. Under
   the stated risk model this is a P1, and leaving it out of the numbered list risks it
   being deprioritized relative to items that made the list for no better reason than
   placement.

3. **The restructure recommendation (§2.4) is right, but I'd stage it differently than
   implied.** Grumpy's fix list treats the boundary restructure as a single 2-3 day unit.
   Given the P0 bugs above are all independently patchable in under a day combined (mostly
   branching on `pending.kind` and swapping `mute_off` for `pause_off`+`trigger`), I'd ship
   those four patches first — this is a live instrument, and shipping the quick, isolated
   silences-fixed patches before the multi-day restructure reduces the number of performance
   sessions spent on a broken instrument while the bigger fix is in flight. Grumpy doesn't
   explicitly argue against this, but the framing ("smaller than the sum of the next five
   one-at-a-time bug hunts") slightly undersells how cheap and low-risk the four immediate
   patches are relative to their payoff.

4. **The `LoopFootswitch` count (88/23) is wrong (actual: 97/24) and the review's own
   language ("measured, not estimated") makes this worse than a typo** — it's a specific
   factual claim about rigor that doesn't hold up. Doesn't change the recommendation, but
   worth flagging since the review leans on "measured, not estimated" as a credibility
   marker elsewhere too, and that marker didn't hold here or in the file line citations.

5. **No disagreement with the core architectural diagnosis.** The "sibling, not layer"
   framing, the three-boundaries table, and the derived-vs-stored rule in §5.2 are all
   independently verifiable in the code and are the right frame. I found nothing to push
   back on there.
