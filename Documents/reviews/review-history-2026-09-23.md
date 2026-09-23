# Review history — loops without an index

Created 2026-09-23 when these loops' cycle files were cut. Each section carries one file's findings.


## kimi

### `grumpy-review-kimi-2026-08-13.md`


1. **Verify and fix the jackd duplex-open conflict with the `usb-host-session` mic bridge and session capture** — add playback-only (`-P`) or an explicit `-P/-C` split to `scripts/start-jackd.sh:43-44` if capture must stay free, then run the physically-blocked 5b/14 soak rows. This is a probable shipped-profile regression (mic return to host dies with `EBUSY`) introduced by Phase 1 and never exercised.
2. **Do not merge `yolo/jack-drop-alsa-fallback` without the Gate C soak** — the branch converts every jackd failure into a silent instrument by design; 2\* (mask-at-boot → `state=failed` → unmask promotes), 2b/2b2 at the new 5 s settle, DAC replug from `state=failed` (15), and stale-`MPE_AUDIO_ENGINE` inertness (12) are all unverified on hardware. The spec already mandates this; treat any pressure to skip it as the actual incident.

---

*Finding tally: 🔴 2 · 🟡 5 · 🟢 6+ (minor nits grouped). Test suite: 440 pass / 0 fail (`mpe test local all`, 2026-08-13).*

### `review-audit-kimi-2026-08-13.md`


| Priority | Issue | Verdict | Effort | Depends On |
|----------|-------|---------|--------|------------|
| **P0** | Hold `yolo/jack-drop-alsa-fallback` merge until Gate C soak runs (2\*, 2b/2b2 @ 5 s settle, 15-from-`failed`, 12 stale-env) — spec-mandated, unverified hard-failure path | ✅ | Pi bench session (~half-day with Mitch) | Mitch + hardware |
| **P0** | jackd duplex open: add playback-only `-P` (after `-d alsa`) to `start-jackd.sh:43-44`, then run the blocked 5b/14 soak rows | ✅ | Quick fix + Pi verification | Physical rewire (already the 5b/14 blocker); decide whether any future client needs capture on the same card — record in spec |
| **P1** | Commit + amend `Documents/specs/looper-jack-client-spec.md`: §D.5 (no ALSA operator choice), criterion 11 (drop `MPE_AUDIO_ENGINE` from verification; fix grep to catch all guard symbols), D.5.2 (current guard message), D.5.3 (current `looper_guard_blocked` signature) | ✅ | Quick fix (one commit, four edits) | None |
| **P1** | Criterion 16 UI drift: point `surge_audio.py` read-back/labels at `MPE_JACK_BUFFER`/`MPE_JACK_PERIODS` with ×periods latency math; keep seeding `MPE_SURGE_BUFFER_SIZE` (calibration + MIDI offset still read it) but stop presenting it as the playing latency; add tests for the JACK enum validators | ✅ | Half-day | None |
| **P1** | Watchdog probes: probe once per reconcile pass, count wall-clock not iterations, and wrap the bare `jack_lsp` at `audio-engine.sh:432` in `timeout 3` (M1) | ✅ + M1 | Quick fix | None |
| **P2** | Failed-reason downgrade guard in `mpe_engine_state_write` callers (keep `supervisor-exhausted`) | ✅ | Quick fix | None |
| **P2** | Comment-hygiene pass: `99-usb-audio.rules:1-5`, `uac2-stall-watchdog.sh:4-6`, `engine-guard.sh:7` date + "nine files" (M4), `set-surge-audio.sh:72` dead `_old_buffer`; CHANGELOG 438→440; README Pi 5 vs Pi 4B disambiguation | ✅ | Quick fix | None |
| **P2** | Spec the budget-reset rule (one sentence in the D3 cooldown section) and the 96 kHz enum decision | ✅ | Quick fix | None |
| **P2** | Consolidate the looper predicate to one bash home + Python twin with extended parity test | ✅ | Quick fix | Naturally lands with Phase 2 Task 12 |
| **P3** | Mixin attribute-ownership pass on `touch_browser_app.py` (19 mixins); document who owns `engine_hud_rect` etc. | ✅ | Half-day | Before next big UI feature |
| **P3** | Cache one Surge `--list-devices` listing per boot across jackd-prestart + start-surge-cli (crosses unit boundary — hand a file, not a variable) | ⚠️ | Half-day | Optional; boot-time win only |
| **P3** | `start-surge-cli.sh` optimistic `ok` publish (M2): verify SURGE_PID survived the first 2 s before writing `state=ok` | Missed | Quick fix | None |


## looper-composer-subagent

### `grumpy-review-looper-composer-subagent-2026-08-15.md`


1. **Complete grid establishment OSC contract** — `eighth_per_cycle` from `bars`, `anchor_phase(bpm)`, test.
2. **Collapse tap authority to SL state** — refactor `_tap()`; demote bench `self.state`.
3. **Fix grid occupancy + align tests with "no clips, no grid"** — `_clear_loop` / `reset_all_loops`; tests (partially addressed post-audit in `tests/test_apc_footswitch.py`).
4. **Add fake SL engine integration tests**
5. **Scrub stale clock docs** — README, `LooperClockMonitor` docstring, spike banner

### `review-audit-looper-composer-subagent-2026-08-15.md`


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


## looper-consolidation

### `grumpy-review-looper-consolidation-2026-09-07.md`

**Counts: 9 CLOSED · 13 OPEN · 1 DECLINED · 1 UNTRACKED.**

1. **Make `looper_timing`'s totality non-vacuous** (F1). Derive or cross-check
   `ACTIONS` against `binding_table.ACTIONS`. Until then the import-time
   invariant proves only that two literals in one file match, and `SCENE_LAUNCH`
   / `SCENE_STOP` are dead rows asserting behaviour the code does not have.
2. **Route `RECORD_START` through `when()`** (F2), and re-point the D0 test at
   `plan_gesture`'s output rather than at the table. Right now the one row the
   module was written for is the one row nothing consults, and the test that
   claims to force a matching edit will not.
3. **Fix the README's fifteen-versus-sixteen** (F3) and extend
   `test_docs_do_not_claim_authority.py` to the three files the audit says it
   fixed and the test does not cover. Eight days, two reviews, one edit that
   added prose above the error.
4. **Give the fake a clock, or retire it against a recording** (Step 0 #2/#12).
   `tests/engine/` proves the axis is now measurable; 2,100 tests still run
   against a double that takes the answer as a parameter, and
   `test_fake_engine_fidelity.py` still has no duration row.
5. **Record the flake** in `test_audio_engine.py::test_planned_promote_sync_path`
   before `ci_gate.py` starts eating deploys. One reproduction under
   `--count`, or a quarantine list, so the next occurrence is data rather than a
   rerun.

### `review-audit-looper-consolidation-cycle1-2026-09-07.md`


| Priority | Issue | Verdict | Effort | Depends On |
|----------|-------|---------|--------|------------|
| P1 | Route `RECORD_START` through `when()`; add `sounding` to `plan_gesture`; re-point the D0 test at `plan_gesture`'s output, not the table (F2) | ✅ | Half-day | — |
| P1 | Make `_assert_total()` non-vacuous: derive/cross-check `ACTIONS` against `binding_table.ACTIONS`; delete or wire up `SCENE_LAUNCH`/`SCENE_STOP`, or document why they're intentionally unreachable (F1) | ✅ | Half-day | — |
| P1 | Restore the exact ordered 4-element assertion in the Stop-All restore test; make the "lifted" loop assert which controls appear, not just their values (F6) | ✅ | Quick fix | — |
| P1 | One helper that sends the whole `engine_controls()` dict, used by `stop_all_loops`, `looper_songs`'s stop path, and `apply_freeform` alike (F7) | ✅ | Half-day | — |
| P1 | Fix `sl_grid_sync.py:204`'s hardcoded `eighth_per_cycle: int = 8` default to use `EIGHTH_PER_CYCLE`; unify `sl_grid_state.eighth_per_cycle` to read the same source, per Grumpy's own recommendation to own it in `sl_grid_state` (F12) | ✅ | Half-day | — |
| P1 | sed `16`→`15` in `scripts/sooperlooper/README.md` (6 sites); rewrite the "rows 1-7 reserved... future" line; extend `test_docs_do_not_claim_authority.py` to cover this file plus `README.md` and `docs/CODE-MAP.md` (F3) | ✅ | Quick fix | — |
| P2 | Fix the double-import: `tests/test_looper_timing.py` should `import looper_timing as timing` (bare), matching production, or `conftest.py` should alias the two (F5) | ✅ | Quick fix | — |
| P2 | Widen the AST guard: `CONTROLS` from `engine_controls(grid=True).keys()` (derived, not typed); match `ast.List | ast.Tuple`; add a negative control (inject an offence, assert the guard fires) (F8) | ✅ | Half-day | — |
| P2 | Add `enforced_by` to `Moment`; add the `GRID_BOUNDARY` ⇒ `BY_BENCH` invariant or document why `RECORD_START` is the exception (F4) | ✅ | Quick fix | — |
| P2 | Unify `Session.grid`'s two producers into one predicate exported from `sl_grid_state` (F9) | ✅ | Quick fix | — |
| P2 | Record/quarantine the `test_planned_promote_sync_path` flake; separately confirm whether it reproduces under `unittest discover` (the runner the gate actually uses), not just `pytest` (F10) | ⚠️ | Quick fix | — |
| P2 | Keep a finding ledger going forward — this document plus the audited review's own ledger are most of the fix already; the remaining work is discipline, not code (F13) | ✅ | Quick fix (process) | — |
| P3 | Name/justify the `DEFERRED_LAUNCH_GRACE_S = 5.0` value specifically (it is already named and its mechanism already documented — only the magnitude is unjustified) (F11) | ⚠️ | Quick fix | — |
| P3 | Delete `scripts/sooperlooper/gesture_engine.py` — zero callers anywhere, confirmed by grep across the whole repo (new finding) | ✅ | Quick fix | — |
| P3 | Fix `Documents/specs/multi-clip-per-track-spec.md` SP1/SP2/SP4 rows to note `slot_matrix_spike.py` no longer exists, matching the SP7 row edited in the same diff (new finding) | ✅ | Quick fix | — |

---


## looper-multigrid

### `grumpy-review-looper-multigrid-2026-08-28.md`


1. **🔴 Move `load_loop` behind the boundary** (`slot_runtime.py:223-245`). Queue it as the
   pending's effect and emit it from `SlotRuntime.boundary()`. This is the reported bug.
2. **🔴 Give `SlotSurface` a real boundary.** Route `loop_pos` in
   `sl_bench_listener.py:54-56`, resolve on `detect_loop_wrap`, delete the
   `sl_state in ACTIVE_PLAY` test at `slot_surface.py:698`.
3. **🔴 Collapse the three entry paths** into one `_apply(plan, sl_state, note)`
   (`slot_surface.py:458/489`, `slot_runtime.py:104`), and fix the scene path's missing
   `expect_cleared()` and its debounce-swallowed synthesised pad-up
   (`slot_surface.py:509-510`).
4. **🔴 One `launch_commands()`** shared by the matrix and the footswitch, so a launch after
   Stop All sends `pause_off`+`trigger` instead of a `mute_off` a paused loop ignores
   (`slot_runtime.py:243` vs `loop_model.py:1400`).
5. **🔴 Make the test suite able to see this class:** quantized by default in `Session`;
   `load_loop`/`save_loop` modelled (with arity validation) in `FakeSlEngine`; `loop_pos`
   driven into the surface; appliance-default `debounce_ms` in harnesses.

### `grumpy-review-looper-multigrid-cycle2-2026-08-29.md`


1. **🔴 Stop All leaves a deferred launch armed; it fires 5 s later and un-pauses the track.**
   Add `SlotRuntime.abandon_pending()` (clears `_deferred`, `_awaiting`, `Track.pending`)
   and call it from the bench's Stop All branch. Test: Stop All → advance past the grace →
   assert zero OSC. *(quick fix)*
2. **🔴 A stale OVERDUBBING report re-arms the tail; the cap then toggles overdub ON and
   leaves it on.** Guard `_begin_tail` on a transition (`prev_sl != SL_STATE_OVERDUBBING`),
   and gate it on an intent flag set when this object sent the closing `overdub`. Test: two
   consecutive OVERDUBBING reports after a cap exit send exactly one `overdub`. *(quick fix)*
3. **🔴 `TailPhase.started_at` uses `time.monotonic()` while `poll_tail`/`sync_in_peak` take an
   injected `now`.** Move the clock to constructor injection, drop the per-call `now=`, and
   write the missing cap integration test — the cap is the only exit that survives a dead
   meter and it has no wiring coverage. *(half-day)*
4. **🟡 The parked-press resume hands the gesture a `down` with no `up`, and drops the handoff
   silently if the bank moved.** Use `tap=True`; log the off-view drop instead of returning.
   *(quick fix)*
5. **🟡 `_awaiting`/`_deferred` survive clear, forget-active-slot, and bank change.** One
   `abandon(track)` called from each, plus a table test over (abort path × parked state).
   *(half-day, subsumes #1)*

### `review-audit-looper-multigrid-cycle1-2026-08-28.md`


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

### `review-audit-looper-multigrid-cycle2-2026-08-29.md`


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


## looper-ownership

### `review-audit-looper-ownership-cycle1-2026-08-28.md`


Duplicates merged across lenses. Severities are the skeptics' corrected values, not the finders' originals.

| Sev | Finding | File:line | Status | Action |
|---|---|---|---|---|
| **P0** | `repaint_scenes(force=True)` survived the `force=` removal; `run_bench` raised `TypeError` before the event loop under `MPE_SL_MULTIGRID=1`, leaving the APC blank and the process dead. Filed twice (intent-surface + regression-runtime lenses); one defect. | `scripts/sooperlooper-apc-bench.py:413` | **CONFIRMED — already fixed at HEAD** by `7b3d857`; guard added (`tests/test_bench_call_sites.py`) | **No action.** Verified: line 413 now reads `slot_surface.repaint_scenes()`; `grep force scripts/sooperlooper-apc-bench.py` returns nothing. See §2a. |
| **P1** | Immediate launch into silence moves `GridState.phase_zero_at` without zeroing the engine's phase — the one path wired past `apply_established_grid`. Bench and engine boundaries then differ by the launch offset; `mute_on` and any new take under `sync=1`/`quantize=1` land on the engine's stale line. | `scripts/sooperlooper-apc-bench.py:400` → `scripts/sooperlooper/slot_runtime.py:507` | CONFIRMED structurally (grep: `mark_phase_zero` has exactly two call sites — `sl_grid_sync.py:299` inside the seam, and the bare bench lambda). **Consequence size UNVERIFIED** — depends on whether SL defers `pause_off`/`trigger` under `sync=1`, which nobody has measured. | **Fix.** Route the launch through `apply_established_grid(..., arm_loops=False)`, exactly as `stop_all_loops` does. Do **not** delete the `mark_phase_zero` call — the bench's new downbeat is the *correct* one per spec §4; the missing half is the engine's. Add a caller-count test on `mark_phase_zero` mirroring the existing `establish_grid_clock` one. |
| **P1** | `_ACTION_TO_BRANCH` collapses `slot_press`/`slot_release` → `"slot"`, so the sweep's `for down in (True, False)` axis asserts nothing that can distinguish the edges. 1536 subtests — the branch's headline "routing is unchanged" evidence — stay green with every pad's press and release swapped. | `tests/test_binding_table.py:656` | **CONFIRMED by mutation** (skeptic reproduced: swap `binding_table.py:410-411` → `36 passed, 1714 subtests passed`, unchanged). Verified the dict at HEAD. | **Fix before pruning anything.** Give the map edge-distinct values and teach `_old_chain_branch` to return the edge-aware tuple. Re-run the swap mutation; it must go red. Note the branch's own negative control (`:844`) mutates the *test fixture*, not the production table — which is why this gap survived a deliberate vacuity check. |
| **P2** | After a song load, the bench's `GridState` still holds a grid established earlier in the session, and the first Stop All pushes it back over the song's. Engine cycle jumps to a take no longer in the session; the only log line reads identically either way. | `scripts/sooperlooper/track_gesture.py:1136` | CONFIRMED (code read at HEAD). **Pre-existing** — `main` did the same via a raw `/set tempo`. Needs the ordering record → load → Stop All. | **Fix or defer.** Correct fix is a `restore` path across the process seam (bench polls `/get tempo` + `/get eighth_per_cycle` after a song-load event, or a `looper.song.loaded` event carrying bpm/bars/cycle_s). Minimum: `stop_all_loops` must not send a grid the engine may have replaced. |
| **P2** | `contested_leds()` returns 72 lamps naming `apc_transport` as a grid-pad and scene writer. False since `8106513` deleted `clear_unwired_surfaces()`/`repaint()`. `track_gesture` on all 64 pads is also false under multigrid. The test pins the stale answer both ways. | `scripts/sooperlooper/control_registry.py:323`, `:359`; `tests/test_control_registry.py:429` | CONFIRMED (measured: `len(contested_leds()) == 72`, `grid_r0_c0.led_writers == ('slot_surface','track_gesture','apc_transport')`). Ledger only — nothing at runtime reads `led_writers`. | **Fix.** Reconcile the column with stage 2 and rewrite the test to the layer semantics `LedCompositor.contention()` actually uses. The no-second-writer invariant stays separately enforced by `test_led_compositor.py:148`. Matters because the handoff sends you to `CONTROLS` as the panel's source of truth during the device pass. |
| **P2** | `ActiveLaneOwnershipTests._colour_of_active(self, states, leds)` discards `states`; `matrix_colours` no longer takes it. The class named for the "no second opinion" guard cannot detect that guard's violation. | `tests/test_slot_leds.py:57` | **CONFIRMED by mutation** (skeptic re-added `sl_states`, wired the real call site at `slot_surface.py:581`, over-painted an `LED_OFF` active cell: 1748 passed, identical to baseline). **Not branch-introduced** — `main`'s `matrix_messages` took `sl_states` and never read it. | **Fix.** Drop the dead parameter, delete `test_the_engine_state_does_not_override_it` (five byte-identical calls), add one surface-level test that sets `_sl_states[0] = SL_STATE_PLAYING` against a gesture returning `LED_OFF` and asserts the compositor's `LAYER_SURFACE` entry is `LED_OFF`. |
| **P2** | `out.send_message.assert_not_called()` asserts on an object now bound as the gesture's **compositor**, not its midi_out. Vacuous. Mutation: deleting `self._multigrid or` from `track_gesture.py:682` leaves this file at 2 passed. | `tests/test_multigrid_gesture.py:36` | **CONFIRMED by mutation.** Branch-introduced: file is byte-identical to `main` while `bind`'s second parameter changed meaning. | **Fix — one word.** `send_message` → `submit`. (The ownership property itself stays pinned by `test_led_compositor.py::LayerOwnershipTests::test_the_gesture_and_the_surface_never_both_paint_the_clip_row`, the sole failure under the mutation.) |
| **P2** | `if __name__ == "__main__": unittest.main()` sits mid-file, so a direct run reports `Ran 4 tests / OK` instead of 8 — including under a mutation that breaks the lint it guards. | `tests/test_periodic_loop_lint.py:86` | **CONFIRMED by mutation** (narrowed `SEARCH_ROOTS` + reverted `_is_periodic_loop`: direct run "Ran 4 / OK", discover run "FAILED (failures=3)"). Pre-existing pattern. | **Fix.** Move the sentinel to EOF in the four files that silently under-report: `test_periodic_loop_lint.py` (4 vs 8), `test_midi_router.py` (10 vs 25), `test_looper_session.py` (11 vs 15), `test_systemd_units.py` (21 vs 24). The other ten such files raise `ModuleNotFoundError`, which announces itself. CI (`unittest discover`) collects all 8, so the real gate holds. |

### 2a. The P0, and why it matters more than its status suggests

The finding is correct and is already closed — but read `7b3d857`'s own message before filing it away:

> The appliance crashlooped on arrival this morning while the deploy reported PASS and 1739 tests passed.

That is the only measured hardware fact this branch has produced, and it is a crash. Two things follow. First, `MPE_SL_MULTIGRID=1` on the appliance is confirmed by consequence, not just by `/proc/…/environ`. Second, the branch shipped a guard test for exactly this migration — `tests/test_led_compositor.py:192 test_no_paint_method_takes_a_force_flag` — which walks `ast.FunctionDef` parameter lists and never call sites, so it read green over a fatal crash. `tests/test_bench_call_sites.py` now closes the call-site half honestly (its own docstring says it proves callability and nothing about behaviour, ordering or timing). I would still extend the `force` guard to walk `ast.Call` keywords, so a surviving caller cannot pass it again.

### 2b. Refuted, and why — the record of skepticism

Fifteen claims were killed. Do not re-litigate; noted so the record shows the review argued with itself.

| Claim | Killed because |
|---|---|
| `sl_grid_sync` subdivision constant drifts from `BEATS_PER_BAR` | The comparison the claim said doesn't exist does exist (`test_the_seam_sends_the_bar_count_not_a_default` asserts the wire value against `cycle_s`); divergence needs `BEATS_PER_BAR != 4`, which is set nowhere and is canon-declared "noted, not built". |
| Compositor hard-refuses velocities before rule 4 can warn | The button probe uses `scripts/probe-apc-buttons.py`, its own port, with the session stopped — it never goes through the compositor. Rule 4's warn-only branch is reachable and tested at `test_led_compositor.py:535`. |
| `FlushLedger.poll` `KeyError` across OSC threads | A live job implies the slot is occupied; `_maybe_mark_recorded` refuses to register on an occupied slot, so the racing `drop` never fires. |
| `sl-restart` emit swallowed by the watcher bootstrap | Every engine-start path emits before a grid could exist; `restart-sooperlooper.sh:100` also re-runs `apply_grid_sync` unconditionally. |
| `apply-player-env-parity.sh` deletes the ear-approved `MPE_SL_TAIL_RATIO` | Inverted. 0.01 is the *unmeasured* number this branch struck out; 0.032 (−30 dB) is what your ear approved and it lives in `tail_phase.py:53`, untouchable by any env rewrite. |
| `smoke-16-loops.sh` PASS line now lies | Backwards — `main` hard-coded "16" over a 15-loop engine; the branch's edit removed a falsehood. |
| `test_scene_row_for_note` orphaned by the branch | It was already callerless on `main` (an unused import, not a call); the branch deleted a dead import. |
| Compositor's fake sink can't drop a message | `LinkHealth.poll()` runs every loop iteration before `pump()`; the lie window is ~2 s, not the session. The escalation rested on an unmeasured rtmidi premise. |
| Three `test_apc_bench` tests assert the compositor's private cache | `believes()` *is* the wire, deduplicated — one write site, immediately before `send_message`. The mutation "proof" excluded `test_led_compositor.py`, the file that owns the invariant (it produces 4 failures + 5 errors). |
| `has_a_reader` counts markdown as a reader | Recomputed with `.md` stripped: every key still has a real code reader. The check demonstrably fires — re-adding `MPE_SL_SEAM_MERGE_SAMPLES` turns the file red. |
| 371 subtests assert unconstructible conditions | They fail — at *import*, harder than an assert. Injecting `Evidence(MEASURED, "  ")` gives a collection error that takes the whole file down. |
| `shift_first` axis costs 768 subtests to test one note | Mutating `Binding.layers()` proves the shift sweep is the *only* thing that catches a per-layer index regression. Deleting it would create this project's cardinal defect. |
| 73 hold-threshold subtests exercise 3 inputs | The redundancy is what makes the check total; deduplicating would blind it to a new `(hold_env, timing_owner)` pair. It has a working negative control. |
| 242 registry subtests loop over generated tables | Measured: a single-pad rebind produces exactly 2 failures naming that pad. It discriminates and localizes. The stated 64-failure scenario produces **zero** failures. |
| Two `CapabilityTests` classes duplicate four refusals | For two of the four, the compositor test never reaches `check_colour` at all — the vocabulary table refuses first. Different gates. |

---


## looper-process

### `grumpy-review-looper-process-2026-09-07.md`


1. **Real-engine harness.** SooperLooper 1.7.9 + `jackd -d dummy` in a Debian bookworm
   container; a CI job; the real `SlOscSession` driving it. First ten tests are the ten most
   expensive `MEASURED` facts, executed. This is the one change that alters what a regression
   *is*. Everything else is downstream.
   *Done the same day:* `tests/engine/` (Debian trixie, like the Pi, with the liblo 0.32
   patch), the `engine` CI job, ten claims executed and green, and the production grid code
   (`GridState.establish`, `apply_established_grid`, `stop_all_loops`) driving the engine
   directly. First contact already corrected one comment: a `record` hit while WAIT_START is
   ignored by the engine, not a cancel (`test_gesture_against_engine.py` said cancel).
2. **No engine claim without an executable.** A comment saying `MEASURED` or `used to` about
   SooperLooper must name the test that proves it, or it is a hypothesis and says so. Enforce it
   the way `control_registry` enforces note numbers — a test that greps.
3. **Deploy gate on the appliance.** `looper-deploy.sh` runs, before and after restart:
   `sl-health`, `smoke-16-loops`, one record-one-cycle-check-length, one Stop All + verify, one
   fader move + still-playing. Red blocks the deploy. These scripts mostly exist.
   *Partly done the same day:* the gate that exists is on **CI**, not on the appliance —
   `scripts/ci_gate.py` refuses any commit without a green `unittest` + `engine` + `shell-tests`
   run, from `looper-deploy.sh` after the reset and from `mpe looper deploy` before it
   (mpe-cli branch `feat/deploy-ci-gate`, unmerged). The on-appliance smoke run listed here
   is still to do.
4. **Make the record survive.** Persistent journal on every image (done on SD this morning;
   put it in `bootstrap-pi5-looper.sh` so it cannot be forgotten), and log every fader emit and
   every echo adoption with values and the decision. Last night's regression was invisible in
   three hours of journal and the journal itself was then erased by a reboot; between them that
   forbids diagnosing anything.
   *Done the same day:* persistent journal in `bootstrap-pi5-looper.sh`; every fader move and
   every echo adoption logged by the bench (`MPE_APC_FADER_LOG=0` to silence);
   `LoopMix.seed_from_engine` returns its verdict; one real-engine test on `wet`.
5. **One rule, one table, one test.** Grid lifecycle and Stop All each get a table in
   DECISIONS.md (gesture → engine state → outcome) and an engine-backed test that pins it. Tests
   are never inverted again; a policy change is a spec edit plus a new test, and the old one is
   deleted.

Not on the list, deliberately: the volume bug. It should be found by item 4 and fixed under
items 1 and 3, or it will be fixed the way the last 116 were.


## opus

### `grumpy-review-opus-2026-08-13.md`


1. **🔴 Commit `Documents/specs/looper-jack-client-spec.md`.** 869 lines of the repo's most detailed design work is untracked (`git status` → `??`) — invisible to CI, to every other session, and to `dev`, and one `git clean -fd` from gone. This is a five-second fix and it is first for a reason.
2. **🔴 Run the Gate C soak before merging `yolo/jack-drop-alsa-fallback` to `dev`.** The spec blocks its own merge on this in bold, and lists the five scenarios in order (`jack-audio-engine-spec.md:452-459`): criterion 2\* masked-jackd boot → `state=failed` + promote-on-unmask; 2b/2b2 retest at the new 5s settle; criterion 15 replug from `state=failed`; criterion 12 with a stale `MPE_AUDIO_ENGINE=alsa` line; D5 guard boot. Every Gate B PASS row for the failure paths tested code that no longer exists.
3. **🔴 Fix the udev `remove`-event blind spot in `config/99-usb-audio.rules:16-19`.** `ATTR{}` cannot match on `remove` (sysfs is already unlinked), so the `Loopback` / `UAC2` / `UAC2Gadget` / `vc4hdmi` skips apply to `add` only, and `modprobe -r snd_aloop` in `calibration_teardown.py:35` restarts jackd after every calibration run — the precise scenario line 19's comment forbids. Filter on kernel-supplied `ENV{}` properties, or move the card-identity check into `restart-audio-graph.sh` where it is one filter instead of four and testable from a laptop.
4. **🔴 Stop the watchdog from destroying Surge's user defaults during a jackd outage** (`scripts/surge-watchdog.sh:97-105`). Post-amendment, `start-surge-cli.sh` exits 1 by design when there is no graph server, so `surge-xt-cli` reliably reaches `failed` ~100s into any DAC problem, and this arm then moves the user's accumulated defaults to `.corrupted_<timestamp>` — misattributing a cable fault to file corruption and accumulating junk files that nothing ever cleans up. Gate the arm on `mpe_engine_state_get state` / `reason`: skip it when the failure is already explained by `no-server` or `no-jack-device`.
5. **🔴 Write a spec for the "analog-esque mixer" before more engine work lands.** Half the stated product goal has no design surface: the only "mixer" in the tree is a 20-line `MixerChannel` dataclass plus per-patch Vol/Tail/Touch parameter faders, and the sole real audio-mixing work is Phase 2's NumPy kernel — gated on a measurement whose kill criterion permits "stop, Phase 1 + a HAT." Decide now whether the mixer rides on the JACK graph (which would make it a Phase 2 dependency and change the looper's insert topology in `looper-jack-client-spec.md` §B.3) or is a separate product surface. This is the question that will invalidate the most work if answered late.

### `review-audit-opus-2026-08-13.md`


| Priority | Issue | Verdict | Effort | Depends On |
|---|---|---|---|---|
| **P0** | Run the Gate C soak (5 scenarios, `jack-audio-engine-spec.md:452-459`) before `yolo/jack-drop-alsa-fallback` merges to `dev` (4.2) | ✅ | multi-day (Pi time) | Nothing — the scenarios are already written in order |
| **P0** | Fix the udev `remove` blind spot — move the card-identity check into `restart-audio-graph.sh` against `/proc/asound/cards`; do **not** use the `ENV{}` option as written (4.3 / B5) | ✅ | half-day | Nothing |
| **P0** | Gate the watchdog's corrupt-defaults arm on `mpe_engine_state_get state`/`reason`, and move the `mv` *after* the cooldown decision (4.4 + MISSED-2) | ✅ | quick fix | Nothing |
| **P1** | Commit `Documents/specs/looper-jack-client-spec.md` **and** amend its §D.5 / criterion 11 off the retired `MPE_AUDIO_ENGINE` in the same commit (4.1 + MISSED-5) | ✅ | quick fix | Nothing |
| **P1** | Route `SurgeMonitor.restart_surge()` through the supervisor cooldown, or disable the UI Restart action when `engine.state` is `failed` with `reason=no-server`/`no-jack-device` (MISSED-1) | ✅ | half-day | Shares the state-read helper with the P0 watchdog fix |
| **P1** | Make criterion 2\*'s failure path testable for real — extract the engine-resolution + state-publish sequence into a sourceable function in `audio-engine.sh` and test that (4.5) | ✅ | half-day | Best done before Gate C so the soak and the test check the same code |
| **P1** | Add shellcheck over `scripts/` + `config/*.service` to CI, glob the shell tests, install from `requirements.txt` (4.9) | ✅ | half-day | Nothing |
| **P2** | Move the MIDI clock state file to `/run/mpe/` and drop the poll to 0.5s (4.8) | ✅ | quick fix | Coordinate with the `midi-clock-in` daemon's writer |
| **P2** | Bound `$HOME/surge-watchdog.log` — rotate, cap, or drop the file and rely on the journal (MISSED-3) | ✅ | quick fix | Nothing |
| **P2** | Explicit `systemctl start mpe-jackd.service` + bounded readiness wait in `restore_mpe_audio_services()` (4.10) | ✅ | quick fix | 4.3 first |
| **P2** | Back off `RestartSec` or throttle logging after N consecutive prestart failures (4.14) | ✅ | quick fix | Nothing |
| **P2** | Rename `looper_clock_monitor.py` → `midi_clock_monitor.py` (4.12) | ✅ | quick fix | Land before `yolo/looper-phase0` merges |
| **P2** | Decide whether the mixer rides on the JACK graph or is a separate surface, and write it down somewhere in the repo (mixer finding, reframed) | ⚠️ | half-day (decision, not code) | Mitch — this is a product call, not an engineering one |
| **P3** | Delete the dead `getattr` chain at `touch_browser_draw.py:484-489` and `:503-505` (4.7) | ✅ | quick fix | None — `_layout()` provably runs first |
| **P3** | Delete the three dead imports: `touch_browser_draw.py:450`, `engine_state_monitor.py:6`, `looper_clock_monitor.py:6`; hoist `:478` (4.11 + MISSED-4) | ✅ | quick fix | Nothing |
| **P3** | Give `mpe_jack_rt_priority()` a bounded allowlist like its siblings (MISSED-6) | ⚠️ | quick fix | Nothing |
| **P3** | Reconcile `RECONCILE_BUDGET=15` with the 5s settle, or comment why they differ (C8) | ✅ | quick fix | Post-Gate-C, when real recovery numbers exist |
| **P3** | Update `CHANGELOG.md:71` to 440, or stop quoting exact counts (4.13) | ✅ | quick fix | Nothing |
| **P3** | Protocol for the mixin `self` surface; start extracting cohesive state (4.6) | ✅ | refactor project | Only worth starting if mypy lands |

---


## ownership

### `review-audit-ownership-cycle1-2026-08-30.md`


Reported severities re-rated against live/latent status and blast radius.

| P | Finding | Verdict | Effort | Status |
|---|---|---|---|---|
| **P0** | Silent-session launch → `TypeError`, process death | ✅ | quick | **fixed `7c57107`** |
| **P0** | Arrow/scene note collision → banking dead, tracks 9–15 unreachable | ✅ | half-day + Mitch | structural half tonight |
| **P0** | Ring-out cap = one bar, spec says one cycle (exposed by `d06fb08`, which does **not** touch `tail_phase.py` — `cap_for` went stale underneath it) | ✅ | quick | **fixed `5e12100`** |
| **P0** | `SlotRuntime.reset()` leaves `_flush` alive → silent take loss, model says clean | reported | half-day | audit pending |
| **P0** | Banking while holding a pad unlinks another track's clip | reported | quick | audit pending |
| **P0** | `reopen_apc` repaints then erases 56 of 64 pads + 8 scene buttons; four private caches make it permanent | ✅ | half-day | Stage 2/3 |
| **P0** | Scene launch buttons dark since session start under multigrid (ctor ordering) | ✅ | quick | Stage 3 |
| **P0** | `player-env-parity.pi5.env` reinstates `MPE_SL_LOOPS=16` + `SCRATCH=14` | reported, **latent** | quick | bootstrap is last writer today |
| **P1** | Grid establishment gated on PLAYING; take closes into OVERDUBBING | ✅ | half-day | needs device pass |
| **P1** | `loop_mix`'s "nothing else writes wet" is false — `looper_songs.py:677` | reported | half-day | audit pending |
| **P1** | `_tail` shared across OSC threads and main loop, unlocked | reported | half-day | audit pending |
| **P1** | Song load resets engine cycle to one bar; manifest never stores `bars` | reported | half-day | audit pending |
| **P1** | Dead tail-seam constants shipped in `mpe.env.example` as live | ✅ | quick | fold into Stage 1 |
| **P1** | Fact base has zero callers; capability rule unenforceable | ✅ | half-day | **Stage 1 deliverable** |
| **P1** | `test_periodic_loop_lint` passes; 10 of 12 evasions get through | reported | half-day | audit pending |
| **P2** | Scene-note test fixture uses 7 notes; production wires 8 | reported | quick | hides the D2 race |

---

### `review-audit-ownership-cycle2-2026-08-30.md`

**P0. Silent take loss. Worse than reported.**
**P0, same family, compounds the above.**
- VERIFIED — `_flush` is keyed by `loop`, but the unit of work is `(loop, slot)`
- VERIFIED — `reset()` does not clear `_flush`
- VERIFIED — `_end_tail` has a real TOCTOU, and the failure is audible
- VERIFIED — `wet` has two writers; the README says it has one
- VERIFIED — a song cannot restore its own grid
- VERIFIED — `player-env-parity.pi5.env` disagrees with `MAX_USABLE_LOOPS`


## ownership-clock-tail

### `grumpy-review-ownership-clock-tail-2026-08-30.md`


1. **P0-2** — ring-out cap is one *bar*, spec says one *cycle*; clips 2+ get their tails cut
   at cycle/bars. Audible on every session since `d06fb08`.
2. **P0-3** — grid establishment gated on `PLAYING`, so it waits out the entire ring-out; any
   clip started in that window is free-form and drifts permanently.
3. **P0-1** — `looper_songs.py:649` reloads every song with `eighth_per_cycle = 8`; the
   manifest never stored `bars`.
4. **P1-2** — `_tail` shared across the OSC and bench threads; the lost race sends a second
   `overdub` (a toggle, therefore ON) or kills the bench with an `AttributeError`.
5. **P1-1 / P1-3 / P1-5** — three separate ways bench phase and engine phase diverge with no
   symptom until multigrid ships.

---


## ownership-config-drift

### `grumpy-review-ownership-config-drift-2026-08-30.md`


`sl_limits.py` is genuinely excellent and the modules that import it are honest.
The problem is that **the clamp it exists to enforce is bypassed on both of the
paths that actually run in production**, and the value it clamps is written into
the appliance's live env file by three different scripts with three different
opinions. `config/platform/player-env-parity.pi5.env` sets `MPE_SL_LOOPS=16` *and*
reinstates `MPE_SL_SCRATCH_LOOP=14` — the two keys `bootstrap-pi5-looper.sh` was
written to delete, in a comment that says deleting them cost real damage. The
health check written specifically to catch the phantom uses the clamped value and
therefore cannot see the fault. And a test named for the phantom asserts a constant
that no runtime code path reads. This is not a documentation problem; it is a
number with five homes, one of which is a loaded gun, and every instrument pointed
at it reads "fine" either way.

Second finding of equal weight: `loop_mix`'s central claim — *"nothing else ever
writes `wet`"* — is **false**, and the second writer lives in the other process.

Third, and the most on-the-nose: `device_facts.py` was built to end the failure where
one unmeasured sentence became load-bearing in five docstrings. Five modules now cite
two of its fact ids that **do not exist**, and all five describe a question as open
that closed MEASURED the day before. Nothing detects this, because `fact()`,
`refuse_with()` and `unmeasured()` have zero callers anywhere in the repo. The fact
base has a home and provenance; it still has no way to be wrong.

---


## ownership-led

### `grumpy-review-ownership-led-2026-08-30.md`


The modules are better than the system. Every individual file here was written by
someone thinking hard about one problem; nobody was ever assigned the problem of *the
surface as a whole*, and the result is a control panel with six mayors. The spec's D2
is correct and understates the scope — it is 64 pads, not just the scene column, and
the resolution mechanism is worse than call order because it depends on private cache
history. Stage 2 and stage 3 are the right treatment and should be done together;
splitting them leaves a compositor that still has `clear_unwired_surfaces` shooting
through it.

Do not let "1600 tests green" stand in for "the panel is right". It was green through
all four 🔴s, and it is still green now that one of them is fixed — which tells you the
suite is not watching this dimension at all, in either direction.

**Priority backlog**

1. **🔴 F2 — done (`7c57107`), and correctly staged as its own revertible commit.**
   What remains: wire `grid_boundary` into `SurfaceCase.setUp` so the launch path is
   tested end to end rather than by injecting `_grid_wait`. The crash is gone; the
   blindness that hid it is not.
2. **🔴 F1 — delete `clear_unwired_surfaces` and its three call sites (stage 3), or
   gate it on multigrid today.** Until then, every APC re-enumeration silently erases
   the matrix with no recovery.
3. **🔴 F3 — give the Stop All note one owner.** Cheapest correct answer: stop passing
   the full eight-note column to `TransportButtonLeds`, and fix
   `resolve_scene_launch_notes`' docstring, which currently states the opposite of what
   the function does.
4. **🔴 F4 — measure the mk2 arrow notes** (`--dump-midi`, four presses, Mitch's
   morning, two minutes). Until then banking is dead and F1 has no recovery path.
5. **🟡 F5 / F6 — one diff, at the wire.** Move diffing into the compositor, delete
   `_painted`, `_scene_painted`, `_last_vel`, `_led_last` and every `force=` flag they
   forced into existence. Then write the "one wire, two writers" test so the class
   cannot come back.
6. **🟡 F9 / F9a — fix the five broken `device_facts` citations, and delete the yellow
   suggestion in `scene_row_led`.** Cheap, in scope per charter §6, and F9a is a
   standing instruction to ship something now provably impossible. Add the
   citation-resolves invariant so it cannot rot again — the mechanism built to stop
   restatement drift has itself drifted, which is the failure worth pinning.

---


## ownership-lifecycle

### `grumpy-review-ownership-lifecycle-2026-08-30.md`


The individual modules are better than most production audio tooling I have
read. The composition is not owned by anyone, and every 🔴 in this review is a
composition bug: two objects writing the same LEDs in an order nobody chose
(F1), a recovery script and an event consumer that were never introduced (F2), a
cache with no invalidation seam (F3), a `BaseException` crossing a thread
boundary into an `except Exception` (F4).

The pattern is consistent enough to name. **This codebase reliably gets the
"what" right and the "when" wrong.** Every module knows what the correct value
is; the defects are all about *at what moment, relative to what else*. That is
what an ownership refactor is for, and it is why the charter's diagnosis —
"a bug that returns is a bug whose owner was never decided" — is correct.

The three things I would fix before touching anything else: make
`reopen_apc` leave a surface that is actually painted (F1); make `sl-restart`
tell the bench it happened (F2); and give `SlOscSession.last` an invalidation
seam so it cannot outlive the engine (F3). The first strands the surface; the
second and third strand the *truth about* the surface, which is worse.

**Priority backlog**

1. **F1** — delete `clear_unwired_surfaces`; one owner per note. Regression test
   asserts no `LED_OFF` lands last on 8–63 / 112–119 after `reopen_apc` under
   multigrid.
2. **F2** — emit `looper.engine.started` from `restart-sooperlooper.sh`; share
   the verify-and-emit tail with `wire-sooperlooper-graph.sh`.
3. **F3** — engine epoch on `SlOscSession`; clear + re-`get` on restart; expire
   `cached()` entries so absence looks like absence.
4. **F4** — `except BaseException` in `_hud_thread_main`; call `verify_or_exit`
   on all three HUD entry points or none.
5. **F11 + the loop refactor** — one `service_tick(now)`, one owner of
   re-registration, and make the repair report itself.

---


## ownership-notes

### `grumpy-review-ownership-notes-2026-08-30.md`

**Count: 7 note-defining constants outside `apc_panel.py`, naming 19 distinct note numbers**

`apc_panel.py` is **mostly holding the line, and the exceptions are worse than the spec
says.** The file itself is excellent: measured facts, the vertical-flip trap written down
once, import-time asserts that actually run (lines 82–85), and pure functions instead of
call-site arithmetic. Every module that consumes it does import from it rather than
re-deriving — `apc_transport` genuinely does route `scene_row_for_note`,
`scene_launch_index_to_row` and `scene_row_to_launch_index` straight through to `apc_panel`
instead of re-implementing them. `track_gesture` uses `pad_note()` everywhere. That is real
discipline and it should be said plainly.

What breaks is the boundary. The spec (D1) names **three** note literals outside
`apc_panel`. There are **seven named note-defining constants outside it, covering nineteen
distinct note numbers**, plus a hardcoded range in the probe and a duplicated note formula
in `slot_surface`. Two of the constants the spec missed — `ARROW_NOTES_MK2` and
`ARROW_NOTES_MK1` — are not merely "defined in the wrong file". `ARROW_NOTES_MK2` **collides
head-on with the mk2 scene column**, and the collision has silently disabled the entire
banking layer on the device that is plugged in right now.

The headline: **on the attached APC mini mk2, the Up/Down/Left/Right bank buttons do
nothing, and tracks 9–15 cannot be reached from the surface.** The banner printed at
startup claims otherwise. Nothing in 126 green tests notices.

---


## ownership-track-state

### `grumpy-review-ownership-track-state-2026-08-30.md`


The locked model is *engine truth plus intent that expires*. `TrackGesture` +
`loop_model` implement that model well — `loop_model.py` is genuinely good code and
`sl_limits.py`-grade in its commentary. **The multigrid layer does not implement it.**
`SlotRuntime` and `SlotSurface` were built as a sibling of the gesture layer rather than
a caller of it, and they reintroduced, one for one, the bugs the 2026-08-15 decision
closed:

| The 2026-08-15 decision fixed | The matrix layer has it back |
|---|---|
| a parallel `self.state` written when a command is *sent* | `Track.pending`, written at press, **never expires** |
| "a queued launch blinking green forever" | reproduced below, 5 minutes and counting |
| a pad lit solid from a command, not from the engine | `Slot(...)` registered by inference from `ACTIVE_PLAY` |
| intent that expires | `Track.pending` has no timeout; `expire_deferred` *fires* on expiry |

Nine live stores answer "what state is track N in." Two of them (`_sl_states`,
`_loop_lens`) exist only because the surface did not want to ask the gesture. One
(`Slot.sl_state`) is written and never read. One (`matrix_messages(sl_states=…)`) is a
dead parameter still being threaded through as if it mattered.

`Documents/specs/multi-clip-integration-plan.md` already prescribes the fix — **one
`TrackColumn` per loop index** — and names the file: `scripts/sooperlooper/track_column.py`.
That file does not exist. The plan's own stop-doing list, rule 3, says *"No new occupancy
inference without a corresponding engine event or explicit close."* `_maybe_mark_recorded`
is exactly that inference, and the plan says in so many words to **delete** it. It is
still there and it is still load-bearing.

**Six defects reproduced from a cold start below.** Each has a runnable sequence. The
191-test looper suite passes with all six present.

```
$ python3 -m pytest tests/test_slot_surface.py tests/test_slot_runtime.py \
    tests/test_slot_matrix.py tests/test_track_gesture.py \
    tests/test_multigrid_equivalence.py tests/test_multigrid_delegates.py \
    tests/test_multigrid_gesture.py tests/test_multiclip_workflow.py \
    tests/test_loop_model.py -q
191 passed in 0.50s
```

That is not a criticism of the tests as tests. It is the measurement the charter asked
for: **every one of these bugs lives in the seam between two owners, and every test is
written inside one owner or the other.** `poll_grid_wait` is tested at
`tests/test_slot_runtime.py:577`; it is never once called through `SlotSurface`, which is
its only production caller — and its only production caller crashes on it.

---


## p0-pending-mute-cancel

### `grumpy-review-p0-pending-mute-cancel-2026-08-26.md`


| | |
|---|---|
| **Good** | Correct fix location; mute cancel tested end-to-end through fake engine; minimal bench diff |
| **Bad** | Launch cancel may lie to the player if SL still fires queued trigger |
| **Smells** | DECISIONS Gate A row stale vs rev 2 spec; `_pending_since` not reset |

### Severity roll-up

| ID | Sev | Item |
|----|-----|------|
| G1 | 🟡 P1 | Launch cancel — no engine cancel; add boundary test or defer launch cancel from P0 |
| G2 | 🟡 P1 | DECISIONS Gate A row — align with rev 2 (15 tracks, scene 1–7) |
| G3 | 🟢 P2 | Clear `_pending_since` on cancel_pending |

### `review-audit-p0-pending-mute-cancel-cycle1-2026-08-26.md`


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


## test-integrity

### `grumpy-review-test-integrity-2026-08-30.md`


The green is mostly real. This is a better suite than the charter's framing led
me to expect: `test_slot_runtime.py`, `test_apc_link.py`, `test_looper_health.py`
and `test_multiclip_workflow.py` contain genuinely adversarial tests that encode
canon, cite the incident that produced them, and would fail on the bug they are
named for. Several files openly document their own blind spots, which is the
behaviour `DECISIONS.md` asks for and I am not going to pretend is common.

But the net has **two specific holes that matter to this refactor**, and they
are not random:

1. **The one LED with two writers is the one LED the tests exclude from their
   fixture.** Two surface harnesses configure a seven-button scene column that
   production cannot produce. The eighth button — `0x59`, the note that both
   `TransportButtonLeds` and `SlotSurface` write — is absent from every
   scene-LED test. Spec §2 D2's race is invisible **by construction**, and I
   demonstrated it executably below.
2. **Nothing tests `device_facts.py`.** Zero tests import it. `Fact.refuse_with()`
   — written specifically so rule 4 would be "executable rather than
   aspirational" — has never been executed by a test. Meanwhile five production
   modules cite a fact id that **does not exist**.

Add to that: **47 commits since 2026-08-20 changed a looper production module
and its tests in the same commit.** That is nearly every commit in the window.
The suite is therefore a near-complete record of what each session decided to
write, and a very thin source of independent evidence. Mitch's framing is
correct, and understated.

---

