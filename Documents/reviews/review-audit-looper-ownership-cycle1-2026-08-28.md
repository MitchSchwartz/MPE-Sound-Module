# Review audit — `refactor/looper-ownership-2026-08-30`, cycle 1

Audited at HEAD `7b3d857`. Suite measured on this machine: **1748 passed, 3 skipped, 2471 subtests** (`python3 -m pytest -q -p no:randomly`, 58 s).
16 findings were filed across six lenses. **8 survived adversarial refutation, 15 were killed or downgraded.** Every surviving finding below was re-verified by me against the working tree; where I inferred rather than measured, the sentence says so.

---

## 1. Verdict in five lines

1. **Keep the branch.** The ownership thesis is sound and nine real defects are fixed; no surviving finding argues for a revert of any stage.
2. **The one P0 has already been fixed at HEAD** by `7b3d857` — but it reached the Pi first: the appliance crashlooped on arrival this morning behind a green suite. That is the branch's own doctrine-1 shape landing on the branch.
3. **Two P1s remain open, both of them instruments rather than sound:** the bench's phase mark is wired past the seam that exists to pair it with the engine's (`slot_runtime.py:507`), and the 1536-subtest routing sweep cannot see the press/release axis (`test_binding_table.py:656`).
4. **Before deploying:** apply the two P1 fixes, then do the device pass the handoff asks for (§1 arrows + faders). Do not stack more refactoring on top.
5. **Nothing on this branch has made a sound.** The only hardware evidence in existence is a startup traceback. Timing, grid and tail claims are all unverified by ear.

---

## 2. Prioritized action matrix

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

## 3. Intent fidelity

Your first question, answered plainly.

### "The grid is STICKY — even after Stop All, even with a two-bar second track, reinitialize with the original settings. They should never be cleared away."

**HONOURED inside the bench process. DRIFTED across the process seam.**

Inside `sooperlooper-apc-bench.py`, `GridState` survives Stop All: `stop_all_loops` (`track_gesture.py:1125-1143`) routes through `apply_established_grid(..., arm_loops=False)` and re-sends the established grid rather than clearing it, and the bar count is carried rather than defaulted to 1. That is the intent, implemented.

The drift is that the grid the bench owns and the grid a *loaded song* carries never meet. `load_song` (`looper_songs.py:778`) builds a local `GridState`, pushes bpm/bars/cycle_s onto the engine, and discards it. That runs in `touch-patch-browser.service`; the deciding `GridState` lives in `mpe-looper-session.service`. Nothing carries it across — `sl_bench_listener.on_update` routes `wet`, `in_peak_meter`, `state`, `loop_len`, `loop_pos` and has no `tempo` branch.

The finder's headline consequence was **refuted**: `load_song` passes `arm_loops=True`, so every loop sits at `quantize=1`/`sync=1` on the song's cycle, the next take is engine-snapped to a whole multiple of it, and `derive_tempo` is a fixed point of that relation — so the take cannot redefine the grid to a foreign value. What survives is narrower and real: the bench's *stale* grid from earlier in the same session can be pushed back over the song's by the first Stop All (P2 above), and until a new take lands `next_boundary` returns `None`, so launches fall back to waiting on a loop wrap instead of the grid.

### "After Stop All, phase resets to zero, and the next clip launch must start IMMEDIATELY, not wait."

**HONOURED in behaviour. The mechanism carries the open P1.**

`_session_sounding()` is False after Stop All pauses every loop, so `_execute_slot_ops` takes the immediate-launch branch and the clip starts on the press. That is your stated behaviour and the branch delivers it.

The defect is the half that follows: `slot_runtime.py:507` then calls a bare `mark_phase_zero` lambda, moving the bench's downbeat to the launch instant without zeroing the engine's. Per spec §4 the bench's new downbeat is the *correct* one — the clip started into silence is the phase reference — so the fix is to add the engine's half, not to remove the bench's. Anyone "fixing" this by deleting the `mark_phase_zero` call would regress exactly the behaviour you asked for.

### "The quantize unit is `GridState.cycle_s` — the first take's own length — NOT one bar from BPM. Bar count and BPM describe the cycle; they do not divide it."

**HONOURED.** This is the branch's cleanest win. `5e12100` moved the ring-out cap from one bar to one cycle, `apply_established_grid` sends `eighth_per_cycle = 8 × bars` so SL's cycle equals the take's own length exactly, and `read_engine_grid` computes the stored `cycle_s` from the *snapped* bar count so a reload sends back what the manifest holds. `test_the_seam_sends_the_bar_count_not_a_default` asserts `eighths × 30 / bpm == grid.cycle_s` against the value actually put on the wire — the invariant checked at the wire, not at a property. I verified that test passes. The one attempt to file a drift here was refuted.

### "Tail threshold is RELATIVE to the tail's own peak (`MPE_SL_TAIL_RATIO`); ~−40 dB validated by ear. Do not unvalidate what the ear approved."

**HONOURED on the ratio; UNVERIFIABLE-WITHOUT-HARDWARE on the cap.**

The relative-ratio model is intact and the ear-approved value is the code default in `tail_phase.py:53`, in the repo, where no env rewrite can reach it. Worth correcting the record, because a review claim got this backwards: the spec now reads `~~0.01, −40 dB~~ **0.032, −30 dB**` — 0.032 is the value your ear landed on across seven takes on 2026-08-30, and 0.01 was the number that had never been measured. The env-parity finding that claimed this branch endangers "your −40 dB" was refuted on exactly that ground.

What you must judge yourself is the **cap**, not the ratio. The ring-out cap is now one cycle instead of one bar — on a 4-bar cycle, 4× longer. Canon-backed and correct on paper; whether it sounds right is not something any test or agent can tell you.

---

## 4. Regression risk register

Ordered by likelihood of biting in a real session.

1. **Panel behaviour after stage 2** — near-certain to be *noticed*, unknown whether wrong. Four deliberate changes (scene row 0 lit from session start, `0x77` handed back to the scene indicator on release, Shift no longer darkening the scene column or rows 1–7, bank change sending only changed pads). No test and no agent can see a lamp. `8106513` reverts alone.
2. **Bench/engine phase divergence after an immediate start (P1)** — fires on your headline gesture: Stop All, then press a pad. Whether it is audible depends on unmeasured SL behaviour under `sync=1`; the repo contradicts itself here (`loop_model.py:248` says "starts on the bar", your own 2026-08-30 note in `sl_grid_state.py` says a post-Stop-All restart "was not quantized"). The wrong thing to do is settle it by reasoning.
3. **Ring-out length** — every take on a multi-bar cycle now rings out up to 4× longer. Deliberate, spec-backed, and the first thing you will hear.
4. **Routing after the stage 5 rewrite** — the table is correct as I read it, but the 1536-subtest sweep cannot see the press/release axis, so "routing is unchanged" is proven at branch granularity only. Symptom if wrong: pads that arm on release instead of press. Cheap to check by hand in ten seconds.
5. **Stop All after a song load (P2)** — needs the ordering record → load → Stop All. Symptom: the loaded song's clips start waiting on a boundary that belongs to a take you already discarded.
6. **`sl-restart` mid-session** — the new emit is correct in the case `57f6dcd` names (refuted as a finding). Residual is a contrived compound failure only.
7. **Banking on the mk2** — still dead, still blocked on your five minutes with `--dump-midi`. Unchanged risk; the branch made it honest (`resolve_arrow_notes` returns `{}` instead of guessing, and the banner no longer advertises banking).

---

## 5. Test pruning plan

**Read this first: most of the proposed pruning was refuted.** Five of the six bloat findings died under mutation testing — the "371 unconstructible subtests" fail at import, the `shift_first` axis is the sole catch for a per-layer index regression, the 73 hold subtests and the 242 registry subtests both discriminate and localize, and the two `CapabilityTests` classes guard different gates. The finder's original "2451 → ~250" is not supportable. What follows is what survives.

**Step 0 — a fix, not a cut. Must land first; cut nothing until it is red-checked.**
`tests/test_binding_table.py:656` — give `_ACTION_TO_BRANCH` edge-distinct values (`slot_press`/`slot_release`, `clip_press`/`clip_release`, `scene_launch`/`scene_release`) and teach `_old_chain_branch` (`:594`) to return the matching edge-aware tuple. Verify by swapping `binding_table.py:410-411` and confirming the sweep now fails. Count change: **0**.

**Step 1 — collapse the sweep's subtest reporting. −1530 subtests, zero coverage lost.**
In `_sweep` (`tests/test_binding_table.py:680`), drop the per-note `self.subTest(...)` and instead accumulate `expected[(variant, mode, shift, note, down)]` and `observed[...]`, then one `assertEqual(observed, expected)` per call. Identical inputs, identical assertions, identical detection power — one failure report instead of 1536. Affects `test_mk1_matches` (`:768`), `test_mk2_multigrid_matches` (`:760`), `test_mk2_single_clip_matches` (`:764`). **1536 → 6.**

**Step 2 — delete one vacuous test. −1 test.**
`tests/test_slot_leds.py:69 test_the_engine_state_does_not_override_it` — five byte-identical calls; redundant with `test_the_gesture_colour_wins`. Drop the dead `states` parameter from `_colour_of_active` (`:57`) and strip the fake argument from the two survivors. Replace with a surface-level test (see matrix row) that fails under the second-opinion mutation.

**Step 3 — repairs, not deletions. Count change 0.**
- `tests/test_multigrid_gesture.py:36` — `send_message` → `submit`.
- `tests/test_periodic_loop_lint.py`, `test_midi_router.py`, `test_looper_session.py`, `test_systemd_units.py` — move `if __name__ == "__main__": unittest.main()` to EOF.
- `tests/test_control_registry.py:429` — rewrite to the post-stage-2 layer semantics once `led_writers` is corrected.
- `tests/test_led_compositor.py:192` — extend `test_no_paint_method_takes_a_force_flag` to walk `ast.Call` keywords, not only `ast.FunctionDef` parameters.

**Net: 2471 → ~941 subtests (−1530, −62%); 1748 → 1747 tests.** Runtime is not the reason — the whole binding-table file runs in 0.79 s. The reason is that 1536 subtest lines advertise coverage on an axis they do not assert, and after Step 0 the six collapsed comparisons assert strictly more.

### Must NOT be deleted — sole cover for a P0 fixed on this branch

- `tests/test_bench_call_sites.py` (whole file) — the only thing standing between the `force=` class of error and another crashloop on arrival.
- `tests/test_led_compositor.py::ReconnectTests` (`:269`), incl. `test_the_matrix_still_shows_the_takes_after_a_reconnect` (`:331`) — P0 repaint-then-erase of 56 pads + 8 scene buttons.
- `tests/test_led_compositor.py::test_the_scene_column_is_lit_from_session_start` (`:365`) — P0 dark scene row since boot.
- `tests/test_led_compositor.py::LayerOwnershipTests::test_the_gesture_and_the_surface_never_both_paint_the_clip_row` — the *only* test that catches removal of the multigrid ownership guard (confirmed: sole failure under that mutation).
- `tests/test_led_compositor.py:148 test_no_led_byte_is_sent_outside_the_compositor` + its non-vacuity guard at `:161` — what makes downstream wire assertions sufficient.
- `tests/test_clock_tail_ownership.py` (18 tests) — P0 one-bar vs one-cycle ring-out, and `test_no_module_but_sl_grid_sync_writes_the_tempo`.
- `tests/test_track_state_ownership.py` (17 tests) — P0 flush ledger keyed by loop instead of `(loop, slot)`. This is the one that was routing around the unsaved-take safety net.
- `tests/test_env_parity_ownership.py` — P0 `pi5.env MPE_SL_LOOPS=16` phantom loop. Proven non-vacuous by re-adding a real dead key.
- `tests/test_engine_lifecycle_ownership.py` and `tests/test_sl_engine_restart.py` — P0 `sl-restart` never told the bench.
- `tests/test_slot_surface.py` silent-session launch tests — P0 `7c57107`.
- `tests/test_periodic_loop_lint.py::ScopeAndBlindSpotTests` — P1 CPU lint blind to 4 of 9 modules.
- The 24 subtests covering the dead mk2 arrows (`test_binding_table.py:306`, `:845`; `test_control_registry.py:388`, `:113`) — these hold the line until your device pass closes them.
- The `shift_first=True` sweeps — mutation-proven to be the only catch for a per-layer routing index regression. Step 1 collapses their *reporting*; it must not remove the axis.

---

## 6. What no agent can know

Stated plainly, because everything above is worth less than this section.

**No code on this branch has run successfully on the Pi.** One deploy happened and the bench crashlooped at startup (`7b3d857`). That traceback is the sum total of hardware evidence for a 16,648-line diff. No audio path, no grid, no tail, no LED and no gesture on this branch has executed on the appliance.

**No test executes `run_bench`.** `test_binding_table.py` and `test_looper_session.py` AST-parse it; `test_bench_call_sites.py` binds its call signatures and says in its own docstring that it proves nothing about behaviour, ordering, threading or timing. The most-executed function on the appliance remains the one function the suite has never run. "1748 passed" and "the bench starts" are independent facts.

Only your ears and eyes can settle these:

1. **The ring-out.** One cycle instead of one bar — 4× longer on a 4-bar take. Canon says cycle; only you can say whether it sounds like a ring-out or a smear.
2. **The panel.** Scene row 0 lit from session start; `0x77` handed back to the scene indicator on release; Shift no longer darkening the scene column or grid rows 1–7; bank change sending only changed pads. Four deliberate changes, zero instruments.
3. **Immediate start after Stop All.** Whether the clip actually starts on the press, and whether the *next* gesture after it lands where you expect. The P1 above says the two clocks disagree from that press onward; how audible that is depends on SL behaviour nobody has measured.
4. **Press vs release on every pad.** Ten seconds by hand. The 1536-subtest sweep cannot tell you.
5. **The four bank-arrow notes and the nine fader CCs.** Still VENDOR tier, still `unmeasured()`, still five minutes with `--dump-midi`. **Do not fill these in by reasoning — reasoning has produced three wrong answers about this panel already.** A wrong fader CC is indistinguishable from a fader nobody touched.
6. **The song-manifest level decision** the handoff flagged and deliberately did not make for you: `save_song` stores the *composed* `wet`, so a reload with the master elsewhere returns loops to their saved absolute level. Changing it changes how songs already on the appliance sound.

One measured fact worth keeping: `MPE_SL_MULTIGRID=1` is the appliance's live configuration — read from `/etc/mpe/mpe.env` and from the running process's `/proc/…/environ`, and now confirmed a third way by the crashloop, which only occurs on that branch of `run_bench`. Any future reasoning that treats the `MULTIGRID=0` code default as the shipped path is wrong.
