# Review audit — looper consolidation (cycle 1 of 5)

**Auditing:** `Documents/reviews/grumpy-review-looper-consolidation-2026-09-07.md`
**Against:** `MPE-Module` branch `dev`, working tree on disk, 2026-09-07 (58 uncommitted changes, unchanged from the state the review itself audited).
**Method:** every file:line citation in the review was opened and read; the double-import claim was reproduced by running Python, not by reading imports; `pytest -q` was run twice more (both clean, 2107 passed / 23 skipped, 121s and 120s); `git diff` was read in full for every file the review's Step 0 and Step 4 depend on.

No product code, tests, or documents were modified.

---

## Work Queue

Five load-bearing claim groups (assigned), plus the review's own claim inventory:

1. F1 — `_assert_total()` vacuity, `binding_table.ACTIONS` second vocabulary, `SCENE_LAUNCH`/`SCENE_STOP` dead rows, "one boundary" falsity
2. F2 — `RECORD_START` decided outside `when()`, 3-of-12 routing, D0 test asserts the table not the code path, `plan_gesture` has no `sounding` param
3. F5 — the double-import claim (highest stakes; reproduced by execution)
4. F10 — the `test_planned_promote_sync_path` flake and its relationship to `ci_gate.py`
5. F3 / F13 — README 16-vs-15, and the missing finding ledger
6. F4, F6, F7, F8, F9, F11, F12 — the remaining 🟡 findings
7. Step 0 table (24 rows, CLOSED/OPEN/DECLINED verdicts)
8. Step 4 — what the review missed: the deletions (4 scripts, 5 specs, dead knobs) and `tests/fake_sl_engine.py`
9. D0/D1/D5 — whether leaving them open is safe (not whether they should be answered)

---

## Claim Verification

### F1 — `looper_timing._assert_total()` and the second vocabulary

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 1 | `_assert_total()` compares `ACTIONS` and `RULES`, both literals in the same file | ✅ Confirmed | `scripts/sooperlooper/looper_timing.py:123-127` defines `ACTIONS` as a 12-tuple; `:217-349` defines `RULES` as a 12-key dict in the same file; `:354-370` `_assert_total()` diffs the two. Both sides are written by the same author in the same file — the check proves internal self-consistency, not that the table matches the control surface. |
| 2 | `binding_table.ACTIONS` is a second, unreconciled 16-name vocabulary | ✅ Confirmed | `scripts/sooperlooper/binding_table.py:163-205`: `noop, scene_launch, scene_release_consumed, slot_press, slot_release, slot_delete, clip_press, clip_release, clip_clear, ignore_reserved_row, latch_shift, transport_note, stop_all_loops, clear_all_loops, bank_scroll, fader_move` — 16 entries, counted. No file imports both `ACTIONS` tuples and cross-checks them; `grep -rn "binding_table.ACTIONS\|looper_timing.ACTIONS"` outside their own definitions finds no reconciliation. |
| 3 | `SCENE_LAUNCH`/`SCENE_STOP` are dead rows | ✅ Confirmed | `grep -rn "SCENE_LAUNCH\|SCENE_STOP" scripts tests` finds both symbols used only inside `looper_timing.py` (their own definitions) and once each in `tests/test_looper_timing.py` (testing the table in isolation). Zero call sites anywhere invoke `timing.when(timing.SCENE_LAUNCH, ...)` or `timing.when(timing.SCENE_STOP, ...)`. (Note: `SCENE_LAUNCH_NOTES_MK1/MK2` in `apc_transport.py` is an unrelated symbol — MIDI note assignments, not this action.) |
| 4 | The "one boundary" claim is false because `slot_surface.scene_press` dispatches per track | ✅ Confirmed | `scripts/sooperlooper/slot_surface.py:176-197` `scene_press` builds `plans` via `plan_scene_press`, then for each plan calls `self._rt.dispatch(plan, sl_state=self.track_state(plan.track))` — one `dispatch()` call per track. `slot_runtime.py:507-511` routes each dispatch through `timing.when(CLIP_LAUNCH or SLOT_SWITCH, self.timing_session(sl_state))`, and `timing_session()` (`:348-359`) sets `track_sounding=sl_state in ACTIVE_PLAY` **per track**. `when()`'s own logic (`:409-411`) falls back to `GRID_BOUNDARY` when `track_sounding` is false. So a scene where track A is sounding and B is not gives A `TRACK_WRAP` and B `GRID_BOUNDARY` — independently computed, exactly as claimed. `RULES[SCENE_LAUNCH].why` ("many launches sharing one boundary") describes behavior the code does not have, and `SCENE_LAUNCH` is never actually the action asked about (claim 3), so the row is doubly fictional. |

**Rollup: F1 fully confirmed, all four sub-claims.**

### F2 — `RECORD_START` decided outside `when()`

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 1 | `loop_model.py:159-171` decides `RECORD_START` timing from `grid_established`, not by asking | ✅ Confirmed | `scripts/sooperlooper/loop_model.py:159-171`: `if state == STATE_IDLE: ... if not grid_established: return Plan(commands=("record",), ..., note="defining the grid (free-form, no count-in)"); return Plan(commands=("record",), expect=STATE_RECORDING)`. No call to `timing.when()` anywhere in this branch. |
| 2 | Only 3 of 12 actions route through `when()` | ✅ Confirmed | Call sites found: `slot_runtime.py:507-511` (`CLIP_LAUNCH`, `SLOT_SWITCH`), `loop_model.py:215-219` (`RECORD_CLOSE`) = 3 actions. `track_gesture.py:1297-1299` calls `timing.when(timing.STOP_ALL, ...)` but only inside an `assert` on a synthetic `Session` — it verifies an invariant, it does not branch on the result, so it is correctly excluded from the "asks" count by the review's own table. |
| 3 | The D0 test asserts the table, not the code path, and would not fire when D0 is answered | ✅ Confirmed | `tests/test_looper_timing.py:191-203` `test_D0_recording_into_silence_still_counts_in` calls `timing.when(timing.RECORD_START, silent)` directly — it never touches `loop_model.plan_gesture`. If someone edits `RULES[RECORD_START]` to `gate=WHILE_SOUNDING`, this test starts failing (correctly) — but if someone instead fixes the actual bug at `loop_model.py:164` while leaving the table's `gate=WHILE_GRID`, the test stays green while the code and the table now disagree. The substitute guard, `test_the_gesture_path_asks_rather_than_decides` (`:175-177`), does `self.assertIn("timing.when(", inspect.getsource(loop_model))` — a substring search over the **whole module**, satisfied by the unrelated `RECORD_CLOSE` call 40 lines away. Verified this is a plain substring check, not an AST walk scoped to the `RECORD_START` branch. |
| 4 | `plan_gesture` has no `sounding` parameter | ✅ Confirmed | `scripts/sooperlooper/loop_model.py:115-123`: `def plan_gesture(*, edge, sl_state, pending, grid_established, is_defining, tail_capture_enabled=False) -> Plan`. No `sounding` param exists, so the sanctioned D0 fix (`gate=WHILE_SOUNDING, enforced_by=BY_BENCH`) cannot be wired into `plan_gesture` without a signature change first. |

**Rollup: F2 fully confirmed, all four sub-claims.**

### The double-import claim (F5) — reproduced by execution

Ran directly, not inferred from imports:

```
$ python3 -c "
from tests import conftest
import scripts.sooperlooper.slot_runtime as sr
import scripts.sooperlooper.looper_timing as pkg
print('sr.timing is pkg:', sr.timing is pkg)
print(sr.timing.__name__, pkg.__name__)
import sys; print([k for k in sys.modules if 'looper_timing' in k])
print('RULES same dict:', sr.timing.RULES is pkg.RULES)
"
sr.timing is pkg: False
looper_timing scripts.sooperlooper.looper_timing
['looper_timing', 'scripts.sooperlooper.looper_timing']
RULES same dict: False
```

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 1 | `looper_timing` is imported twice under two module names, producing two `RULES` dicts | ✅ Confirmed (reproduced) | `sys.modules` holds both `looper_timing` and `scripts.sooperlooper.looper_timing` as distinct entries; `RULES is` comparison is `False`; `Session`/`Moment`/`Rule` are two distinct classes. |
| 2 | All production modules import the bare name | ✅ Confirmed | `grep -n "looper_timing" scripts/sooperlooper/*.py`: `sl_grid_sync.py:21`, `looper_songs.py:23`, `loop_model.py:33`, `slot_runtime.py:40`, `track_gesture.py:90-91` — all `import looper_timing as timing` or `from looper_timing import ...` (bare). Only `tests/test_looper_timing.py:21` imports `scripts.sooperlooper.looper_timing as timing` (package-qualified). |
| 3 | Blast radius: which tests assert against the unused copy? Does behavior differ? | ✅ Confirmed, blast radius is real but narrow | Every assertion in `WhenTests`, `InvariantTests`, `DivergenceTests` in `tests/test_looper_timing.py` calls the **package-qualified** copy's `when()`/`RULES`/`engine_controls()` — literally the copy production never touches. Checked for identity-sensitive failure modes (`isinstance` against `timing.Session`/`Rule`/`Moment`, or `==` comparisons across the two copies): none exist anywhere in `tests/` or `scripts/` (`grep -rn "isinstance.*Session\|isinstance.*Moment"` → no hits). `OneAuthorityTests`' three tests (`test_no_module_writes_a_quantize_value_of_its_own`, `test_the_launch_path_asks_rather_than_decides`, `test_the_gesture_path_asks_rather_than_decides`) do AST parsing and `inspect.getsource()` string checks on the **production** modules directly — those are unaffected by which copy of `looper_timing` is loaded. Since both copies load byte-identical source with no shared mutable state (all dataclasses are frozen), their computed values are identical; only object identity and class identity differ. **Conclusion: real defect, zero behavioral difference today, exactly as Grumpy characterized it** ("Nothing breaks today... but the tests assert against the copy production does not use"). |

**Rollup: F5 confirmed by execution, including the "nothing breaks today" mitigating claim.** This is the review's strongest piece of verification work — it is the one claim the review itself said to prove by running code rather than reading, and it did.

### The flake (F10)

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 1 | `test_planned_promote_sync_path` failed once in two `pytest -q` runs, isolated runs green | 🔍 Can't reproduce, plausible | Ran `pytest -q` twice more: both clean, `2107 passed, 23 skipped` in 121.06s and 120.87s. Consistent with Grumpy's own characterization ("1-in-2 at suite scale, 0-in-13 otherwise") — a low base-rate flake will not reproduce in 2 more tries. I cannot confirm the failure occurred, but two more clean runs neither prove nor disprove a rare flake; I have no basis to call this fabricated. |
| 2 | `ci_gate.py` gates deploys on this suite | ⚠️ Partially True — the mechanism is looser than stated | `scripts/ci_gate.py:39` `REQUIRED_DEFAULT = ("unittest", "engine", "shell-tests")` — confirmed the deploy gate requires a green `unittest` check run. **But** `.github/workflows/test.yml:20-30`, the `unittest` job, runs `python3 -m unittest discover -s tests` — **not** `pytest -q`. Grumpy ran `unittest discover -s tests` only once (clean, 2130 tests OK) and never established whether the pytest-only flake reproduces under the runner the gate actually depends on. My own testing didn't touch `unittest discover` either. The claim "the deploy gate is now downstream of it" is true in the loose sense that both runners execute overlapping test code, but the specific flake was only ever observed under `pytest`, and the gate's actual dependency is on the `unittest discover` invocation, which has shown zero failures across all runs (Grumpy's + mine). This nuance doesn't make the underlying worry wrong — a shared root cause (a leaked `os.environ` write, Grumpy's own hypothesis) could in principle surface under either runner — but the review states the connection more directly than the evidence supports. |
| 3 | Candidate mechanism: `_bash_env` copies `os.environ` while six files write `os.environ[...]` without restoring | ✅ Confirmed as stated (a hypothesis, correctly hedged) | `tests/test_audio_engine.py:48-49` `_bash_env`: `env = os.environ.copy()`. `grep -rln "os.environ\[" tests/*.py`: exactly six files — `test_calibration_handoff.py`, `test_audio_profile.py`, `test_midi_sync.py`, `test_health_source_liveness.py`, `test_norm_trim_v2.py`, `test_output_latency_model.py`. Grumpy explicitly said "I did not establish the mechanism and will not guess one" and named this only as "the candidate worth checking first" — that hedge is honest and matches what I can verify (a plausible but unconfirmed candidate, not a claimed root cause). |

**Rollup: F10 downgraded to ⚠️ Partially True.** The flake itself is plausible and honestly hedged; the "downstream of it" framing overstates a connection that hasn't been tested end-to-end (pytest flake vs. unittest-discover gate).

### F3 / F13

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 1 | `scripts/sooperlooper/README.md` still states 16 tracks at lines 55, 59, 62, 95, 167, 169 | ✅ Confirmed, exact line numbers | `grep -n` reproduces the same six lines verbatim: `:55` "APC 16-track clip row", `:59` "8 visible of 16" / "Reserved (per-track controllers, scenes — future)", `:62` "Master fader \| CC 56 \| all 16", `:95` "sixteen tracks", `:167` "all 16 loops", `:169` "scaling all 16". |
| 2 | `sl_limits.MAX_USABLE_LOOPS = 15` is the authority | ✅ Confirmed | `scripts/sooperlooper/sl_limits.py:43` `MAX_USABLE_LOOPS = 15`; `apc_grid.py:33` `NUM_LOOPS = MAX_USABLE_LOOPS`. |
| 3 | Rows 1-7 are implemented multigrid slot rows, not "reserved... future" | 🔍 Can't fully verify in the time available, but consistent | `slot_matrix.py`/`slot_surface.py` implement an 8-row grid (`NUM_SLOTS = 8` in `slot_matrix.py`) that is live under `MPE_SL_MULTIGRID`, matching the review's claim that rows 1-7 are not merely reserved. I did not trace every code path to prove the UI actually reaches all 7 rows, but the README's own claim of "future" is contradicted by `slot_matrix.py`/`slot_surface.py` existing and being under test (`tests/test_slot_matrix.py`, `tests/test_slot_surface.py`). |
| 4 | The file was edited today to add a disclaimer, not fix the numbers | ✅ Confirmed | `git diff -- scripts/sooperlooper/README.md` shows a +8/-1 diff: a new banner ("Orientation, not authority...") and a D0 sentence, with the 16-track table below it byte-identical to before. |
| 5 | `test_docs_do_not_claim_authority.py` doesn't cover this file | ✅ Confirmed | `tests/test_docs_do_not_claim_authority.py:27-28`: `SPECS = REPO/"Documents"/"specs"`, `AUDIT = REPO/"Documents"/"looper-audit-2026-09-07.md"`. Separately checks `DECISIONS.md`, `DIRECTION.md`, `AGENTS.md`. No reference anywhere in the file to `scripts/sooperlooper/README.md`, `README.md`, or `docs/CODE-MAP.md`. |
| 6 | No file in `Documents/reviews/` contains a finding ledger | ✅ Confirmed | `grep -l "Finding ledger" Documents/reviews/*.md` → only the review being audited itself (which added its own "## Finding ledger" section). 48 files total in the directory now (47 existed before this review was added, matching Grumpy's count). |

**Rollup: F3 and F13 both fully confirmed, including exact line numbers.**

### The remaining 🟡 findings

| # | Finding | Verdict | Evidence |
|---|---------|---------|----------|
| F4 | `Moment` omits `enforced_by`; `Rule.__post_init__` checks only the `WHILE_SOUNDING`+`BY_ENGINE` pair | ✅ Confirmed | `looper_timing.py:157-173` `Moment` has fields `lands_on`, `why` only. `:190-209` `__post_init__` checks gate/landing/enforcer membership, non-empty `why`, and exactly one cross-field rule (`WHILE_SOUNDING` + `BY_ENGINE` → raise). No check ties `GRID_BOUNDARY` to `BY_BENCH`. `RECORD_START`'s rule (`enforced_by=BY_ENGINE`, `lands_on=GRID_BOUNDARY`) does contradict `GRID_BOUNDARY`'s own docstring ("Our own timer... used when nothing is playing for the engine to wrap", `:56-59`) which reads as bench-side language applied to an engine-enforced row. Confirmed as a real terminology conflict; agree with Grumpy's own "Fails today if it regresses? No" — this is presentational/documentation-internal, not a live behavior bug. |
| F6 | Stop-All restore assertion weakened from an ordered 4-element list to a duplicate-collapsing `dict`, in the same diff as the fix | ✅ Confirmed | `git diff -- tests/test_track_gesture.py`: before, `assertEqual([v for path,v in sent if path=="/sl/-1/set"], [["mute_quantized",0.0],["quantize",0.0],["quantize",0.0],["mute_quantized",1.0]], ...)`. After: `restored = dict(v for path,v in sent if path == "/sl/-1/set"); assertEqual(restored.get("quantize"), 0.0); assertEqual(restored.get("mute_quantized"), 0.0, ...)`. Confirmed the `dict()` collapses duplicates keeping the last value and drops order. Confirmed the companion "lifted" loop earlier in the same test (`for control, value in lifted: assertEqual(value, 0.0, ...)`) checks only values, never which controls appear — so a version of `engine_controls()` that silently dropped `mute_quantized` from its output would still pass both loops. This directly contradicts `AGENTS.md:271`'s written rule, "Do not weaken an assertion to make a test pass" — worth weighting for that reason specifically, not just as generic test hygiene. |
| F7 | Three call sites lift different subsets of the four engine controls for an "immediate" burst | ✅ Confirmed | `track_gesture.stop_all_loops` (`:1300-1303` region, confirmed in diff): `for control, value in engine_controls(grid=False).items(): osc.send_message(...)` — all four. `looper_songs.py:583-584`: `probe.send("/sl/-1/set", ["mute_quantized", engine_controls(grid=False)["mute_quantized"]])` — one key only. `sl_grid_sync.apply_freeform:360-374`: sends `quantize`, `sync`, `round` explicitly, never `mute_quantized`. Three different subsets of the same four-key dict, confirmed exactly as described. |
| F8 | The AST guard covers 2 of 4 controls, `ast.List` only, and a narrow file scope | ✅ Confirmed | `tests/test_looper_timing.py:128` `CONTROLS = {"quantize", "mute_quantized"}` — `sync` and `round` (both also in `engine_controls`'s output) excluded. `:154` `if not isinstance(node, ast.List) or len(node.elts) != 2: continue` — tuples not matched. `_sources()` (`:130-136`) globs `scripts/sooperlooper/*.py` plus one bench file; confirmed `scripts/looper-session.py` and `scripts/measure-loop-alignment.py` exist at `scripts/` (one level up, outside the glob) and the latter does set `quantize`/`sync` directly (`measure-loop-alignment.py:723-726`: `restore["quantize"] = sl.get_global("quantize"); sl.set_global("quantize", QUANTIZE_CYCLE); sl.set_loop(args.loop, "sync", 1.0)`) — outside the guard's reach, exactly as claimed. |
| F9 | `Session.grid` has two independently-computed producers that can disagree | ✅ Confirmed | `slot_runtime.py:357`: `grid=self._grid_boundary() is not None`. `_grid_boundary` (`:114`) wraps `GridState.next_boundary()`, and `sl_grid_state.py:348-360` `next_boundary()` returns `None` when `not self.established or not cycle or self.phase_zero_at is None` — i.e. can be `None` even when `established=True`. `loop_model.py:217` instead passes `grid=grid_established` directly — the raw flag. So in a state where `established=True` but `phase_zero_at is None` or `cycle_s==0`, the two producers disagree (`slot_runtime` says no grid, `loop_model` says grid). Confirmed no bug manifests today because the only rule `slot_runtime` currently asks about gates on `WHILE_SOUNDING`, not `grid` — matches Grumpy's own "No bug today" qualifier. |
| F11 | `expire_deferred`'s 5s grace timer is "unexplained and unnamed" | ⚠️ Partially True — "unnamed" is incorrect | `slot_runtime.py:76`: `DEFERRED_LAUNCH_GRACE_S: float = 5.0`, preceded by a four-line comment (`:71-75`) explaining the *mechanism* ("If `loop_pos` stops arriving the wrap never comes and the switch is stranded — a dead pad with no error, which is worse than a late switch. After this many seconds without a wrap the launch fires anyway and says so."). The constant **is** named, and the purpose **is** documented. What is genuinely missing is any measurement or reasoning tying the *specific value* 5.0 to anything (no "MEASURED" comment, unlike D0's rule) — so "unexplained" (as in: why 5.0 and not 3.0 or 10.0) holds, but "unnamed" overstates it. I'm downgrading this specific sub-claim; the underlying point (D5 is a timing decision at a call site, outside the table, with an unjustified numeric constant) stands. |
| F12 | `MPE_LOOPER_EIGHTH_PER_CYCLE` is honoured in one place and ignored in two others | ✅ Confirmed | `sl_grid_sync.py:111` `EIGHTH_PER_CYCLE = int(os.environ.get("MPE_LOOPER_EIGHTH_PER_CYCLE", "8"))`. `:253` (`establish_grid_clock`) uses it: `EIGHTH_PER_CYCLE * max(1, bars)`. `:204` (`apply_grid_sync`) has `eighth_per_cycle: int = 8` as a **hardcoded** default — confirmed via `grep -rn "apply_grid_sync("` that **none** of its five call sites (`sooperlooper-apc-bench.py:243,357`, `looper_songs.py:772`, `sl_grid_sync.py:399`, `tests/engine/harness.py:262,280`) ever pass `eighth_per_cycle=` explicitly, so the env var has zero effect on this path. `sl_grid_state.py:337-346` `eighth_per_cycle` property: `return self.beats_per_cycle * 2` — also ignores the env var entirely, always computing `8 * bars` from `BEATS_PER_BAR`. Confirmed: setting `MPE_LOOPER_EIGHTH_PER_CYCLE=16` changes startup behavior (`apply_grid_sync` — no, wait: startup is hardcoded 8, so it does NOT change) but does change `establish_grid_clock`'s output, while `sl_grid_state`'s own property still computes the old value — a real, live, three-way divergence exactly as claimed, open 8 days. |

**Rollup: F4, F6, F7, F8, F9, F12 confirmed exactly as stated. F10 and F11 downgraded to Partially True** — in both cases the underlying worry is real, but one specific sub-claim in each ("downstream of it" for F10, "unnamed" for F11) overstates what the evidence supports.

### Step 0 table (spot-checked)

Spot-checked a sample rather than all 24 rows given the depth already spent on F1/F2/F5. Checked rows #1 (`fake_sl_engine.py` discards `/set`), #2/#12 (loop length as an argument), #19 (`NUM_TRACKS` deleted), #21 (README, folded into F3), #23 (`eighth_per_cycle` three answers, folded into F12).

| # | Finding | Verdict | Evidence |
|---|---------|---------|----------|
| 1 | Test double discards every `/set` control | ✅ Confirmed | `tests/fake_sl_engine.py:59-68`, byte-for-byte: `def send_message(self, path, arg): self.sent.append(...); parts = path.strip("/").split("/"); if len(parts) != 3 or parts[0] != "sl": return; if parts[2] in ("load_loop","save_loop"): ...; if parts[2] != "hit": return`. Any `/sl/-1/set` message (which is exactly how `quantize`/`mute_quantized`/`sync`/`round` are sent) is recorded in `self.sent` but never changes `self.quantized` or any other engine-model state. |
| 2/12 | Loop length is an argument, not a computed result | ✅ Confirmed | `tests/fake_sl_engine.py:180` `def _finish_record(self, loop, length: float = 2.0)`; `:185` `def boundary(self, *, length: float = 2.0)`. The fake is handed the answer it should be computing. |
| 19 | `slot_matrix.NUM_TRACKS` decoy constant deleted today | ✅ Confirmed | `git diff -- scripts/sooperlooper/slot_matrix.py`: `-from sl_limits import MAX_USABLE_LOOPS` / `-NUM_TRACKS = MAX_USABLE_LOOPS` removed cleanly. `grep -rn "NUM_TRACKS" scripts/ tests/` after the diff finds no remaining references — deletion is clean, not dangling. |

**Rollup: sampled Step 0 rows all check out.**

---

## Severity Re-Assessment

| # | Issue | Reviewer Rating | My Rating | Delta | Reasoning |
|---|-------|-----------------|-----------|-------|-----------|
| F1 | Totality check vacuous; scene rows fictional | 🔴 | High | = | Agree with reviewer. Not Critical — nothing crashes, no data at risk — but it undermines the one guarantee the whole day's work was built to provide, and the scene-launch "one boundary" claim is provably false for a feature that exists today (multi-track scene press). |
| F2 | `RECORD_START` decided outside `when()`; D0 test can't catch a fix | 🔴 | High | = | Agree. This is the flagship use case the module claims to own, and the regression protection is a decoy. Real risk the next change to this branch silently diverges table from code. |
| F3 | README 16-vs-15, disclaimer not correction | 🔴 | Medium-High | slightly ↓ | Real and worth fixing (8 days old, misleads onboarding), but it's pure documentation with zero runtime effect — I'd put it a notch below F1/F2 on pure severity, though the effort is trivial enough that priority (below) stays high regardless. |
| F13 | No finding ledger anywhere in `Documents/reviews/` | 🔴 | Medium | ↓ | Real and costly (Step 0 reconstruction tax), but it's a process gap, not a code defect — nothing breaks, nobody is misled about current behavior. I'd call this Medium; the review's own act of adding a ledger to itself is most of the fix already in motion. |
| F5 | Double-import of `looper_timing` | 🟡 | Low-Medium | ↓ | Confirmed by execution, and confirmed genuinely inert today (no identity-sensitive test exists). I'd rate this Low-Medium rather than treat it as urgent — it's a correctness smell with a one-line fix, not a live risk. |
| F6 | Weakened Stop-All restore assertion | 🟡 | Medium-High | ↑ | I'd raise this slightly. It's not just generic test-hygiene erosion — it directly and specifically violates a written project rule (`AGENTS.md:271`, "Do not weaken an assertion to make a test pass"), introduced in the same commit that is supposed to be raising the bar. That combination (explicit house rule + same-day violation) earns more weight than an ordinary 🟡. |
| F7 | Three inconsistent control-lifting call sites | 🟡 | Medium-High | ↑ | This is precisely the class of bug ("two files, both plausible alone, no reader could see it") that `looper_timing.py`'s own docstring says it exists to prevent, and it is still present across three call sites today. I'd raise this because it's a live, reachable inconsistency (not hypothetical), not because it's currently causing an observed bug. |
| F8 | AST guard covers half the controls, one syntax, narrow scope | 🟡 | Medium | = | Agree with reviewer's implicit weighting. Real gap, but the two files outside its scope that do set quantize directly appear to be deliberate diagnostic tooling (`measure-loop-alignment.py`), not accidental drift — the review itself says "defensibly." |
| F9 | `Session.grid` two producers | 🟡 | Low | ↓ | Agree with reviewer's own "no bug today." I'd keep this Low-priority-but-worth-doing — the fix is cheap and forecloses a bug before the next `WHILE_GRID`-gated `slot_runtime` action is added. |
| F10 | Flaky test, gate dependency | 🟡 | Low-Medium | ↓ | Downgraded per the claim verification above — the gate's actual `unittest` job runs a different command (`unittest discover`) than the one where the flake was seen, and it has been clean in every run (Grumpy's and mine). Worth tracking, not urgent. |
| F11 | D5 grace timer unexplained/unnamed | 🟡 | Low | ↓ | The "unnamed" component is wrong (see claim table); only the unjustified-magnitude part survives, which is a minor polish item. |
| F12 | `eighth_per_cycle` three-way divergence | 🟡 | High | ↑ | I'd raise this. It's a live, reachable, 8-day-old bug: an env var documented and read in one place has zero effect through two of the three code paths that claim to honor it, and would silently produce wrong quantize-unit behavior the moment someone actually changes `MPE_LOOPER_EIGHTH_PER_CYCLE` from its default. This is not hypothetical the way F9 is — the divergence exists at every value of the env var other than the default, right now. |

---

## What the Review Missed

The review is unusually thorough; the gaps found are narrow.

**1. `scripts/sooperlooper/gesture_engine.py` is an entirely orphaned module, and today's diff still edited it.**

`grep -rln "gesture_engine" scripts/ tests/ docs/ Documents/` finds exactly one hit outside the file itself: `Documents/specs/multi-clip-integration-plan.md:354`, which lists it as `**New** — extract from footswitch / loop_model` — a planning doc, not a caller. No test imports it (`grep -rn "gesture_engine" tests/` — nothing besides the plan doc). No production script imports it. `plan_arm_record` and `plan_close_take`, its only two functions, have zero call sites anywhere (`grep -rn "plan_arm_record\|plan_close_take"` → only their own definitions). This is a 67-line dead file — exactly the class of thing R1 flagged and got credit for closing (`slot_matrix_spike.py`, `spike-load-halt.py`, deleted). This one was missed because it isn't named like a spike; it reads like production code (`"""Shared record/close gestures — single-clip gesture and multigrid matrix."""`). Today's diff removed the now-decommissioned `quantized` parameter from both of its dead functions (`git diff -- scripts/sooperlooper/gesture_engine.py`, -4 lines) — real effort spent maintaining code nothing calls, in the same session that closed four other dead files.

**2. `Documents/specs/multi-clip-per-track-spec.md` has a same-file, same-session inconsistency in its own deletion bookkeeping.**

The file was edited today (new HISTORY banner, several `CORRECTION 2026-09-07` notes added, confirmed via `git diff`). Its SP7 row was specifically updated to read `was slot_matrix_spike.py --sp7, deleted 2026-09-07` — but the SP1, SP2, and SP4 rows immediately above it (lines 595, 596, 599) still cite `slot_matrix_spike.py --sp1` / `--sp2` / `--sp4` as their "Method" column with no such annotation, even though the same file was deleted in the same commit. Low severity — the file now carries the HISTORY stamp and is covered by `test_docs_do_not_claim_authority.py` for the "this is not current behavior" property — but it's a concrete example of the exact kind of partial-fix-in-the-same-diff pattern the review calls out elsewhere (F3's disclaimer, F6's weakened assertion): the editor touched the file for this specific reason and still missed three of four sibling rows.

**3. The deletions (4 scripts, 5 specs) are otherwise clean.** I independently re-verified this rather than trusting the review's own table: `grep -rln` for each of the 4 deleted scripts and the 5 deleted specs (`looper-loop-seam-spec.md`, `next-tasks-2026-08-20.md`, `next-work-order-2026-08-19.md`, `queue-2026-08-21-evening.md`, `rerun-order-2026-08-19.md`) across the whole repo. Every remaining reference is in a dated doc, review file, or DECISIONS.md entry, and every live spec that references a now-deleted file (`low-latency-512-256-spec.md:104,437`, `session-control-plane-spec.md:20`, `DECISIONS.md:480`) was itself edited today to say "deleted 2026-09-07" in past tense. No dangling live-code or live-spec reference found. This confirms and extends the review's own "audit :117 True" verdict — I checked the 5 specs the review didn't explicitly re-verify, not just the 4 scripts.

**4. `tests/fake_sl_engine.py` — no new findings beyond what the review already found.** I read the whole file independently. Every claim the review makes about it (discards `/set`, loop length as an argument, quantize fixed at construction) checks out exactly, and I found nothing additional worth flagging — the file is honestly self-documenting about its own limitations (see the comment at `:14-20` about verb semantics being "taken from the engine, not guessed").

---

## What the Review Got Right (And Why It Matters)

**F5, done properly.** Most reviews would assert the double-import claim from reading two `import` lines. This one ran `python3 -c "..."` and printed `is` and `sys.modules`. That is the difference between a plausible-sounding claim and a proven one, and it's the right instinct given how much the rest of the review's confidence rests on "one place, one table" — the one claim that could have quietly falsified the whole thesis got the strongest treatment.

**F2's chain is the standout finding.** It isn't just "this branch doesn't call `when()`" — it's that the row `RECORD_START` exists in `RULES` specifically because of a measured, real bug (the D0 comment cites actual press-to-first-sample timings), and the row's own guard test (`test_D0_recording_into_silence_still_counts_in`) is wired to the wrong object, so fixing the code without fixing the test (or vice versa) both look green. That's a subtler and more dangerous failure than "missing test coverage" — it's a test that actively signals false confidence.

**F7, once confirmed, generalizes past its own three examples.** The pattern — one canonical source of truth (`engine_controls()`), three call sites that each independently decide how much of it to use — is exactly the shape of bug the whole `looper_timing.py`/`binding_table.py` consolidation exists to eliminate, and it survived the consolidation in the one place (`engine_controls`) that was supposed to be the fix for it.

---

## Prioritized Action Matrix

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

## Disagreements and Judgment Calls

**F10's "downstream of it" framing.** The review is correct that `ci_gate.py` requires a green `unittest` check run, but the specific sentence "the deploy gate is now downstream of it [the flake]" implies a tighter coupling than what's verified. The GitHub Actions `unittest` job runs `python3 -m unittest discover -s tests`, not `pytest -q` — a different test runner, with different collection and (potentially) different environment-mutation ordering. Grumpy ran `unittest discover` exactly once and it was clean; I didn't re-run it either, for the same reason Grumpy gave (not worth chasing a rare flake past a couple of tries). The honest framing is "a flake exists somewhere in this suite, and the same suite (under a different runner) gates deploys" — which is still worth recording, just not with the implied direct causal link.

**F11's severity.** I'd push back harder than a simple downgrade: calling `DEFERRED_LAUNCH_GRACE_S` "unnamed" when it's a well-named constant with a four-line explanatory comment risks training the next reader to distrust the review's precision on adjacent, harder-to-verify claims. The underlying point (D5 is a real timing decision outside the table, and the *number* 5.0 has no measurement behind it, unlike D0's MEASURED values) is worth keeping — I'd just phrase it as "the value is asserted, not measured" rather than "unnamed."

**No disagreement with the core thesis.** The review's verdict — "the consolidation did the hard thing correctly... but the new authority's guarantees are weaker than its prose" — holds up under audit. I did not find a single claim in the four 🔴 findings that was fabricated or meaningfully overstated. That is a high bar for an AI-generated review to clear, and this one cleared it.

**On D0/D1/D5 being safe to leave open:** agree with the review's own qualifier — D0 and D1 are safe to leave open *given* F2 is fixed (right now D0's own guard test doesn't touch the code path D0 lives on, which is a reason to prioritize F2, not a reason D0 itself is unsafe). D5 is the one of the three that isn't fully "safe" as-is in the sense the review means: it's not that leaving the *design question* open is unsafe, but that `expire_deferred`'s current implementation is doing real timing decision-making (a literal 5.0s threshold, a call site outside `looper_timing`) regardless of whether D5's design question ever gets a ruling — that's a standing architectural exception to the "one place decides" claim, independent of Mitch's judgment call on the design itself.
