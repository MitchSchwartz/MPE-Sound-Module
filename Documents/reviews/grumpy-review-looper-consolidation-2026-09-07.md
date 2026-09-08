# Grumpy review — the looper consolidation

**2026-09-07, evening.** Branch `dev`, working tree as it stands: 57 uncommitted
changes plus `looper_timing.py`, `test_looper_timing.py`,
`test_docs_do_not_claim_authority.py` and `looper-audit-2026-09-07.md` untracked.
Reviewer had no hand in any of it.

**Read in full:** `looper_timing.py`, `test_looper_timing.py`,
`test_docs_do_not_claim_authority.py`, `fake_sl_engine.py`, the 09-07 audit,
`AGENTS.md`, the two most recent looper reviews, `test.yml`,
`sooperlooper/README.md`, the head of `binding_table.py`, `tests/engine/harness.py`
and `run-engine.sh`. **In part:** `slot_runtime`, `track_gesture`, `loop_model`,
`sl_grid_sync`, `sl_grid_state`, `looper_songs`, `slot_surface`, `slot_matrix`,
`apc_panel`, `sl_loop_states`, `sl_limits`, `ci_gate.py`, `CODE-MAP.md`,
`DECISIONS.md`, and today's full diff. **Sampled:** `sooperlooper-apc-bench.py`
structure only (969 lines, one 871-line function). **Not read:**
`led_compositor`, `sl-watchdog`, `control_registry` body, `loop_mix` body,
`tail_phase`, the LED modules.

**Ran:** `pytest -q` twice (122 s each) and `unittest discover -s tests` once.
Nothing that touches the appliance.

---

## Step 0 — the fate of the last three reviews' findings

Sources: `grumpy-review-looper-process-2026-09-07.md` (R1, ~13 h old),
`why-the-looper-bugs-recur-2026-09-06.md` (R2, 1 day), and the looper-relevant
🔴/🟡 of the 2026-08-30 set — `grumpy-review-ownership-config-drift`,
`grumpy-review-ownership-clock-tail` (R3, 8 days).

| # | Finding (source) | Mark | Evidence now |
|---|---|---|---|
| 1 | The test double discards every `/set` control (R1 🔴) | **OPEN — 1 day** | `tests/fake_sl_engine.py:59-68` byte-identical. `if parts[2] != "hit": return`. Mitigated *elsewhere* (`tests/engine/`), not here |
| 2 | Loop length is an argument, not a result (R1 🔴, R2 §1) | **OPEN — 2 days** | `fake_sl_engine.py:180` `_finish_record(..., length: float = 2.0)`; `:185` `def boundary(self, *, length: float = 2.0)`. Unchanged |
| 3 | Engine semantics guessed from send order (R1 🔴) | **CLOSED** | `trigger` removed from the burst; `tests/engine/test_engine_claims.py::test_a_trigger_in_the_stop_all_burst_keeps_the_clip_playing`. The wrong mechanism is *retracted in the source* at `track_gesture.py:1286-1293` |
| 4 | Policy pinned by tests that get inverted (R1 🔴) | **OPEN — 1 day** | Today's diff again rewrites assertions in the same change as the production edit, and weakens one — see 🟡 F6 |
| 5 | `run_bench` — 848 lines, 34 closures, 0 tests (R1 🟡) | **OPEN — 1 day** | Now `sooperlooper-apc-bench.py:90-961`, ~35 closures. `test_bench_call_sites.py` binds call signatures by AST; nothing constructs or runs it |
| 6 | `loop_len` latch cannot say "empty" (R1 🟡) | **OPEN — 1 day** | `slot_surface.py:434-436` unchanged: `if loop_len > 0:`. Still no reason on file |
| 7 | Fader path unobservable in the journal (R1 🟡) | **CLOSED** | Bench logs every emit and adoption (`MPE_APC_FADER_LOG`); `tests/engine/…::test_a_fader_move_sets_wet_and_the_loop_keeps_playing` |
| 8 | `verify_stop_all` flattered itself (R1 🟡) | **CLOSED** | `track_gesture.py:1193-1200` counts checkable loops and names the empties |
| 9 | Spikes in the production tree (R1 🟢) | **CLOSED** | `slot_matrix_spike.py`, `spike-load-halt.py` deleted; no live reference remains |
| 10 | `apc_panel.scene_press_row` kept "deliberately" (R1 🟢) | **DECLINED** | `apc_panel.py:160-166` gives the reason: it is the differential oracle `test_binding_table.py` checks the table against. Legitimate |
| 11 | Deploy gate that exercises the looper (R1 backlog #3) | **OPEN — 1 day (half)** | CI half landed: `scripts/ci_gate.py`, called from `looper-deploy.sh:27-32`, requires `unittest`+`engine`+`shell-tests`. The on-appliance half did not: `smoke-16-loops.sh` still has exactly one caller, `diagnose-16loop-crackle.sh:134` |
| 12 | Give the fake a clock (R2 §What would close it, 1) | **OPEN — 2 days** | See #2 |
| 13 | Give the fake a playhead (R2, 2) | **OPEN — 2 days** | `boundary()` is still instantaneous; no phase parameter anywhere |
| 14 | Duration/phase rows in `test_fake_engine_fidelity.py` (R2, 3) | **OPEN — 2 days** | 130 lines, three classes, all discrete-state: buffer ops and wrap transitions. Zero duration entries, zero phase entries |
| 15 | `measure-loop-alignment.py` as a gate (R2, 4) | **OPEN — 2 days** | `grep -rn measure-loop-alignment .github/` → nothing |
| 16 | Nothing sanity-checks the defining take (R2, "the real single defect") | **CLOSED** | `sl_grid_state.py:41` `MIN_DEFINING_TAKE_S` (1.5 s, env-overridable); refusal at `:185-188` with a stated reason |
| 17 | Multiclip slot-binding "take was NOT registered" (R2, "a third thing") | **CLOSED** | Diagnosed as a false alarm, not a binding defect: `slot_surface.py:98-103, 479-496`, commit `716468f` |
| 18 | `MPE_LOOPER_EIGHTH_PER_CYCLE` half-honoured (R3 config-drift 🟡 8) | **OPEN — 8 days** | `sl_grid_sync.py:111` reads the env into `EIGHTH_PER_CYCLE`; `:204` `apply_grid_sync(eighth_per_cycle: int = 8)` still hardcodes it. Named with the same line number eight days ago |
| 19 | `slot_matrix.NUM_TRACKS` decoy constant (R3 config-drift 🟡 9) | **CLOSED** | Deleted today (`slot_matrix.py` diff), with its guard test |
| 20 | `loop_mix.wet_for()` sole-writer claim false (R3 config-drift 🔴 4) | **CLOSED** | The false claim is gone; `remote_fader.py:10` records `looper_songs.load_song` as the documented exception |
| 21 | `scripts/sooperlooper/README.md` states 16 tracks (R3 config-drift, claims 1-7, 11) | **OPEN — 8 days** | `README.md:55,59,62,95,167,169` still say 16 / sixteen; `apc_grid.py:33` `NUM_LOOPS = MAX_USABLE_LOOPS` = 15. `:59` still calls rows 1-7 "Reserved … future"; they are the implemented multigrid slot rows. **The file was edited today** — see 🔴 F3 |
| 22 | The bench bypasses `resolve_num_loops()` (R3 config-drift 🔴 2) | **CLOSED** | `sooperlooper-apc-bench.py:53, 124` |
| 23 | `eighth_per_cycle` has two answers (R3 clock-tail:670) | **OPEN — 8 days** | Now three: `sl_grid_sync:253` (`EIGHTH_PER_CYCLE * bars`), `:204` (literal 8), `sl_grid_state.py:337-346` (`beats_per_cycle * 2`, env ignored) |
| 24 | **The fate of a finding is not recorded anywhere** | **UNTRACKED** | 47 files, 1.4 MB in `Documents/reviews/`. `grep -l "Finding ledger" Documents/reviews/*.md` → **zero**. The only index, `looper-review-index-2026-08-15.md`, covers three of them and stopped 23 days ago |

**Counts: 9 CLOSED · 13 OPEN · 1 DECLINED · 1 UNTRACKED.**

**The headline is the age distribution.** Nine closures in about thirteen hours
is a real day's work, and every one of them is verifiable. But three findings are
**eight days old and were named with file and line number** on 2026-08-30 —
including one (#21) in a file that was edited *today* to add a disclaimer above
the falsehoods rather than fix them. And #24 is why: no review in this repository
has ever ended with a ledger, so every review's Step 0 has to be reconstructed by
reading 1.4 MB of prose. Findings here are not being ignored; they are being
**re-derived and re-typed**, which costs the same and yields less.

---

## 1. First impressions

This does not read like a project in trouble. It reads like one that has learned
an unusual lesson very well and is now applying it slightly too literally.

The good is genuinely rare. `tests/engine/` is the single most valuable thing to
land here: SooperLooper 1.7.9 on a JACK dummy backend, launched with the
appliance's flags (`run-engine.sh` mirrors `run-sooperlooper.sh`), twenty tests
that can *contradict* a claim — including `test_a_free_form_take_is_as_long_as_it_was_held`
and `test_a_clip_hit_mid_bar_waits_for_the_bar_and_lands_one_cycle_long`, which
are the first assertions in this repository's history that can be wrong about how
long a loop is. `harness.py`'s docstring says why the fake's `boundary()` was a
lie and prices the honest clock in wall-clock seconds. `scripts/ci_gate.py` treats
"cannot ask the API" as a refusal rather than a pass. `test_clock_tail_ownership.py`
walks the call graph and carries its own non-vacuity proof. `binding_table.py`'s
opening is the best piece of engineering writing in the tree: it explains that
statement order was load-bearing, and then makes the bug *unexpressible* rather
than fixed.

What worries me is the ratio. Today produced 446 inserted lines across 44 files —
450 of them a new authority module for a 12-row table, 109 a test whose subject
is other documents — while the two 🔴 from twelve hours earlier (the fake's
missing clock, the discarded `/set`) went untouched and a README that has said
"16 tracks" for eight days got a paragraph saying it is not authoritative.

**The consolidation is real and I would keep it. What it has not yet done is
change what a regression *is*.** That was done in `tests/engine/`, and well.

## 2. Architecture & structure

The looper is a C++ engine driven over OSC plus a Python control layer, and the
split is right. Today's change adds a fourth layer of a specific kind: **modules
that exist to refuse a second opinion.** `sl_limits.py` (how many loops),
`control_registry.py` (which note), `binding_table.py` (what a control does),
`sl_loop_states.py` (what a state code means), and now `looper_timing.py` (when it
happens). Four of the five are good. `sl_loop_states.py` in particular is
twenty-seven lines that end an entire bug class, and its `EMPTY_STATES` comment
("Ask this set, never a code") is the right shape.

Two structural problems remain, both flagged before and both still true.

`run_bench` (`sooperlooper-apc-bench.py:90-961`) is the only place every module
meets and the only place nothing executes. `test_bench_call_sites.py` is an
honest mitigation — it AST-resolves every call and binds it against the real
signature, which would have caught the `repaint_scenes(force=…)` crashloop — but
it is a type check, not a run.

Engine state is still cached in three modules (`track_gesture`, `slot_surface`,
`looper_songs`), and `looper_songs` runs in the touch-browser process
(`looper_songs.py:519`), so the third cache is across a process boundary.

## 3. Single authority

This is the section the consolidation asked for, so it gets the space.

### Q1 — "When does an action take effect?"

Claimed owner: `looper_timing.py`. Sites that answer it today:

| Site | What it decides |
|---|---|
| `looper_timing.RULES` (`:217-330`) | The table, 12 rows |
| `slot_runtime.py:507-511` | Asks, for `CLIP_LAUNCH` / `SLOT_SWITCH` |
| `loop_model.py:215-219` | Asks, for `RECORD_CLOSE` |
| `track_gesture.py:1297-1299` | Asserts, for `STOP_ALL` |
| **`loop_model.py:159-171`** | **Decides `RECORD_START` itself**, from `grid_established`, without asking |
| **`slot_runtime.py:575-583`** | **Overrides a `Moment`** after `DEFERRED_LAUNCH_GRACE_S = 5.0` |
| `sl_grid_sync.set_grid_active` (`:137-167`) | The engine half, correctly sourced from `engine_controls` |

**Three of twelve actions actually route through `when()`.** `SCENE_LAUNCH`,
`SCENE_STOP`, `CLIP_STOP`, `OVERDUB`, `CLIP_CLEAR`, `CLEAR_ALL`, `FADER_MOVE` and
`RECORD_START` never reach it. For eight of the twelve rows the module is a
*description*, not a decision — which is fine, and is not what the file says
("Nothing else in this repository is permitted to decide", `:381`) nor what
`AGENTS.md:221` says ("the only place").

They agree today. What makes them diverge tomorrow is D0: the fix named in
`looper_timing.py:249-251` is `gate=WHILE_SOUNDING, enforced_by=BY_BENCH`, and
`loop_model.plan_gesture` does not take a `sounding` argument at all
(`loop_model.py:115-123`). So the sanctioned fix cannot be applied at the only
place that decides. See 🔴 F2.

**The one module that should own it** is `looper_timing`, and to earn that the
`RECORD_START` branch at `loop_model.py:164` has to become a `when()` call.

### Q2 — "What is the quantize unit?"

| Site | Answer |
|---|---|
| `sl_grid_sync.py:111` | `int(os.environ.get("MPE_LOOPER_EIGHTH_PER_CYCLE", "8"))` |
| `sl_grid_sync.py:253` | `EIGHTH_PER_CYCLE * max(1, bars)` — honours the env |
| `sl_grid_sync.py:204` | `eighth_per_cycle: int = 8` — **ignores the env** |
| `sl_grid_state.py:337-346` | `beats_per_cycle * 2` = `8 × bars` — **ignores the env** |
| `looper_songs.py:546-549` | falls back to `EIGHTH_PER_CYCLE` |

Four answers, two of which ignore the environment variable the other two read.
Set `MPE_LOOPER_EIGHTH_PER_CYCLE=16` and startup writes 8 while establishment
writes 32 and the bench's own model still says 16. Flagged with this line number
on 2026-08-30. **Owner should be `sl_grid_state`**, since it already holds
`bars`; `sl_grid_sync` should take the value, never a default.

### Q3 — "How many loops are there?"

`sl_limits.MAX_USABLE_LOOPS = 15` is the authority and the Python side is clean
(`apc_grid.py:33`, `sl_osc_session.py:42`, bench `:124` all resolve through it).
Four literal `15`s live outside Python — `bootstrap-pi5-looper.sh:46`,
`restart-sooperlooper.sh:13`, `tests/engine/run-engine.sh:9`,
`tests/engine/harness.py:44` — with no guard, and the README says 16 in six
places. Shell cannot import Python, so the duplication is forced; the *absence of
a check* is not.

### Q4 — "Which state means what?"

`sl_loop_states.py` owns it and is used everywhere I looked. The one outlier,
`track_gesture.STOPPED_STATES` (`:1167`), is a domain set built from those codes.
No finding.

## 4. Code quality

Naming is good throughout — `settle_stop_all`, `note_loop_content`,
`grid_silent_reason`, `mark_immediate_downbeat`. Error handling is honest; I found
nothing swallowed.

The comment-as-changelog habit R1 named is unchanged and in one place got worse:
`stop_all_loops` is now seven lines of sends under **forty-five** lines of
history, three separate dated corrections, and one explicit retraction
(`track_gesture.py:1259-1299`). The retraction is admirable — "was reasoned from
the send order, never measured, and is wrong twice over" is exactly the right
sentence. It is also the third narrative layer on one seven-line burst.

Duplication: `resolve_apc_transport_notes` / `resolve_arrow_notes` /
`resolve_fader_ccs` are still the same eleven lines three times.

## 5. Code smells (the hall of shame)

### 🔴 F1 — `looper_timing`'s totality check compares the file with itself

`scripts/sooperlooper/looper_timing.py:354-370`:

```python
def _assert_total() -> None:
    """Every action has a rule, and every rule names an action."""
    missing = [a for a in ACTIONS if a not in RULES]
    ...
    extra = [a for a in RULES if a not in ACTIONS]
```

`ACTIONS` is defined at `:123-128` in the same file. The check proves that a
twelve-element tuple and a twelve-key dict, written eighty lines apart, agree —
which they will, because whoever adds one adds the other. The docstring at `:36-38`
claims something else entirely: *"Every action the surface has is a row in
`RULES`; a missing row raises at import."* The module has no knowledge of the
surface.

Proof that this is not theoretical: `binding_table.ACTIONS` (`:163-203`) is a
*different* vocabulary of sixteen names — `scene_launch`, `slot_press`,
`slot_delete`, `clip_press`, `stop_all_loops`, `fader_move` — and nothing
reconciles it with `looper_timing.ACTIONS`. `looper_timing` carries `SCENE_STOP`,
which no binding produces. `binding_table` carries `slot_delete`, which has no
timing rule. Two "one table" authorities, two unrelated vocabularies, no check.

Worse, two rows are provably dead *and* wrong. `slot_surface.scene_press`
(`:177-197`) dispatches one plan per track, each of which is evaluated at
`slot_runtime.py:507` as `CLIP_LAUNCH` or `SLOT_SWITCH` — never `SCENE_LAUNCH`.
So `RULES[SCENE_LAUNCH].why = "a scene is many launches sharing one boundary"`
describes behaviour the code does not have: a scene where track A plays and B
does not gives A `TRACK_WRAP` and B `GRID_BOUNDARY` (`when()`, `:409-411`) — two
boundaries, one scene.

**Fix:** derive `ACTIONS` from something independent, the way
`binding_table` rule 3 already does for owners. A test that maps every
`binding_table` action to the `looper_timing` action(s) it can produce, and fails
on either side being unmapped, is thirty lines and turns the totality claim into
a real one.
**Fate:** Enforced — new test, `tests/test_looper_timing.py`.
**Fails today if it regresses?** No. Nothing can.

### 🔴 F2 — `RECORD_START` is the row the whole module was written for, and nothing asks it

`scripts/sooperlooper/loop_model.py:159-171`:

```python
    if state == STATE_IDLE:
        ...
        if not grid_established:
            return Plan(commands=("record",), expect=STATE_RECORDING,
                        arm_grid=True,
                        note="defining the grid (free-form, no count-in)")
        return Plan(commands=("record",), expect=STATE_RECORDING)
```

That is a timing decision — "does this record wait?" — taken from
`grid_established` at a call site, forty lines above a `timing.when()` call for a
*different* action. `looper_timing.py:4` says: *"If a timing decision lives
anywhere else, that is the bug."*

The guard that is supposed to catch this cannot. `tests/test_looper_timing.py:175-177`:

```python
    def test_the_gesture_path_asks_rather_than_decides(self) -> None:
        src = inspect.getsource(loop_model)
        self.assertIn("timing.when(", src)
```

A substring search over the whole module. It is satisfied by the `RECORD_CLOSE`
call at `loop_model.py:215` and would be satisfied by a call in a comment.

The cost is concrete. `test_D0_recording_into_silence_still_counts_in`
(`:191-203`) says *"When this is fixed the test fails, and the rule's own comment
has to be rewritten in the same commit."* It will not. It asserts
`when(RECORD_START, …)` against the table; the appliance's behaviour comes from
`loop_model.py:164` and `sl_grid_sync.set_grid_active`. Change the table and the
test fails while the instrument is unchanged; change the instrument and the test
passes while the table is a lie. **The D0 pin is decoupled from the thing it
pins.** That is the same failure shape `AGENTS.md` calls "the reading that looks
the same whether it is broken or fine", aimed at the new module.

**Fix:** route `RECORD_START` through `when()`. `plan_gesture` needs a `sounding`
argument for the D0 fix to be possible at all; add it now while the answer is
still "no change in behaviour", and assert the *plan*, not the table, in the D0
test.
**Fate:** Handed to the user — this is one line of behaviour risk against an
open question you own. Recommendation: do the plumbing now, leave the gate at
`WHILE_GRID`, so answering D0 later is a one-line table edit.
**Fails today if it regresses?** **No.**

### 🔴 F3 — the README got a disclaimer instead of a correction

Today's whole diff to `scripts/sooperlooper/README.md` is +8/−1: a banner —
*"**Orientation, not authority.** Where this file and a module disagree, the
module wins"* — and a D0 sentence. Fifty lines below it, untouched:

```markdown
## APC 16-track clip row (Ableton-style, banked)

| Row | APC notes | Tracks | Role |
| **0** (bottom) | 0–7 | 8 visible of 16 | Clip pads … |
| 1–7 | — | — | Reserved (per-track controllers, scenes — future) |
```

`apc_grid.py:33` says `NUM_LOOPS = MAX_USABLE_LOOPS` = **15**. Rows 1-7 are the
implemented multigrid slot rows (`slot_matrix.py`, `slot_surface.py`). Both were
enumerated as FALSE with line numbers on 2026-08-30
(`grumpy-review-ownership-config-drift-2026-08-30.md`, claims 1-7 and 11). The
file has been edited twice since without either being fixed.

A disclaimer does not stop a reader quoting one sentence — the audit says exactly
that about the spec `CORRECTION`s ("A stamp alone does not stop somebody quoting
one specific sentence"), then does the weaker thing here. The banner arguably
makes it worse: it names two questions the file is not authoritative on, implying
the rest is merely unofficial rather than numerically wrong.

**Fix:** sed `16`→`15` in six places, and rewrite the rows 1-7 line. Ten minutes.
Then extend `test_docs_do_not_claim_authority.py` to this file: assert
`MAX_USABLE_LOOPS` appears and no bare "16 loops" does.
**Fate:** Enforced — `tests/test_docs_do_not_claim_authority.py`.
**Fails today if it regresses?** **No.** The new docs test covers
`Documents/specs/*.md`, `DECISIONS.md`, `DIRECTION.md`, the audit and `AGENTS.md`
— not `scripts/sooperlooper/README.md`, not `docs/CODE-MAP.md`, not `README.md`,
all three of which the audit lists as having been fixed.

### 🟡 F4 — `Moment` drops the half of the rule that says who waits

`looper_timing.py:158-172` returns `lands_on` and `why`. `Rule.enforced_by`
(`BY_ENGINE` / `BY_BENCH`) is never returned. The file's own prose at `:66-68`
says confusing the two is *"how a 'deferred' command became indistinguishable
from an 'ignored' one for six weeks."*

So each caller still hardcodes the enforcer: `slot_runtime.py:512-516` holds the
message itself; `loop_model.py:220-235` sets `begin_quantize_wait` and lets the
engine hold it. Half the rule is centralised, half is at the call site — and
`RECORD_START` is a `BY_ENGINE` rule whose `lands_on` is `GRID_BOUNDARY`, which
`:56-59` defines as *"our own timer"*. The two fields contradict each other on
the row that matters most, and `Rule.__post_init__` (`:190-201`) checks only the
`WHILE_SOUNDING`+`BY_ENGINE` pair.

**Fix:** put `enforced_by` on `Moment` and add the second invariant —
`GRID_BOUNDARY` implies `BY_BENCH`, or say in the row why it does not.
**Fate:** Fixed now (small) + Enforced in `test_looper_timing.py`.
**Fails today if it regresses?** No.

### 🟡 F5 — the authority module is imported twice, as two modules

Verified by running `python3 -c "from tests import conftest; import
scripts.sooperlooper.slot_runtime as sr; import scripts.sooperlooper.looper_timing
as pkg; print(sr.timing is pkg)"` → **`False`**.

`sys.modules` holds both `looper_timing` (bare, what production uses via
`conftest.py`'s path insert) and `scripts.sooperlooper.looper_timing` (what
`tests/test_looper_timing.py:21` imports). `RULES` is two dicts; `Session` and
`Moment` are two classes, so instances of one fail `isinstance` against the other
and compare unequal.

Nothing breaks today — both are built from one file and nobody mutates `RULES`.
But a module whose entire premise is "there is exactly one table" is loaded as
two tables, and the tests assert against the copy production does not use. This
is the module's own thesis, one level up.

**Fix:** `tests/test_looper_timing.py` should `import looper_timing as timing`
(bare) like the production modules do, or `conftest` should alias one to the
other. One line.
**Fate:** Fixed now.
**Fails today if it regresses?** No.

### 🟡 F6 — an assertion was weakened in the same change as the fix

`tests/test_track_gesture.py`, before (git diff):

```python
        self.assertEqual(
            [v for path, v in sent if path == "/sl/-1/set"],
            [["mute_quantized", 0.0], ["quantize", 0.0],
             ["quantize", 0.0], ["mute_quantized", 1.0]],
            "restored only once the engine has been asked what happened",
        )
```

after:

```python
        restored = dict(v for path, v in sent if path == "/sl/-1/set")
        self.assertEqual(restored.get("quantize"), 0.0)
        self.assertEqual(restored.get("mute_quantized"), 0.0, …)
```

The `dict(...)` collapses duplicates keeping the last and discards order. The
earlier assertion pinned the exact sequence; the replacement cannot see a control
set twice with conflicting values, cannot see ordering, and `.get()` passes on a
missing key returning `None` only if the expected value were `None` — here it
would fail, but the companion loop above it (`for control, value in lifted: assertEqual(value, 0.0)`)
does **not** assert *which* controls were lifted, so dropping `mute_quantized`
from `engine_controls` entirely would keep it green.

The control set had to change from 2 to 4, so the old literal had to go. It could
have become a four-element literal. `AGENTS.md:271` — *"Do not weaken an
assertion to make a test pass."*

**Fix:** assert the exact list of four `(control, value)` pairs, in order.
**Fate:** Fixed now.
**Fails today if it regresses?** Partly — a wrong *value* still fails; a missing
*control* does not.

### 🟡 F7 — two "immediate all-stop" bursts, two different control sets

`track_gesture.stop_all_loops:1300-1303` lifts **all four** controls before the
burst:

```python
    for control, value in engine_controls(grid=False).items():
        osc.send_message("/sl/-1/set", [control, value])
    osc.send_message("/sl/-1/hit", "mute_on")
    osc.send_message("/sl/-1/hit", "pause_on")
```

`looper_songs.stop_playback:585-588`, the same gesture from the other process,
sends the same two `hit`s after lifting **one**:
`probe.send("/sl/-1/set", ["mute_quantized", engine_controls(grid=False)["mute_quantized"]])`.

The audit fixed the *value* drift between these two (real, and worth it) and left
the *set* drift, unexplained in either file. Same for `sl_grid_sync.apply_freeform`
(`:366-374`), which sends `quantize`/`sync`/`round` from `engine_controls(grid=False)`
and silently omits `mute_quantized` that `set_grid_active` sends at `:167`.

**Fix:** one helper that sends the whole dict, used by all three. The AST guard
already forbids literals; it does not notice a *subset*.
**Fate:** Enforced — extend `OneAuthorityTests` to require that any module
calling `engine_controls` sends every key it returns.
**Fails today if it regresses?** No.

### 🟡 F8 — the one-authority AST guard checks two of four controls, on one syntax

`tests/test_looper_timing.py:138-166` scans for `ast.List` of exactly two
elements whose first is a constant in `CONTROLS = {"quantize", "mute_quantized"}`
and whose second is a numeric constant.

Missed by construction: `sync` and `round` (also returned by `engine_controls`,
also quantization); tuple syntax `("quantize", 1.0)`, which `python-osc` accepts
identically; a non-constant value (`float(1)`); a name-bound key; and every file
outside `scripts/sooperlooper/*.py` + the bench — `scripts/looper-session.py`,
`scripts/measure-loop-alignment.py:724-727` (which does set `quantize` and `sync`
directly, defensibly, and is invisible to the guard), and `mpe-cli`.

The docstring at `:142` claims it "refuses a second" opinion. It refuses one
spelling of two of the four.

**Fix:** widen `CONTROLS` to the keys of `engine_controls(grid=True)` — derived,
not typed — and match `ast.List | ast.Tuple`. Then add the negative control this
project's own doctrine requires: a test that injects an offending line into a
temp copy and asserts the guard fires.
**Fate:** Enforced — same file.
**Fails today if it regresses?** Partly.

### 🟡 F9 — one `Session.grid`, two definitions

`looper_timing.Session.grid` (`:137-139`) is documented as *"A grid has been
established."* Its two producers disagree:

- `slot_runtime.timing_session:357` — `grid=self._grid_boundary() is not None`,
  which is `GridState.next_boundary()`, and returns `None` when `phase_zero_at`
  is unset or `cycle_s` is zero (`sl_grid_state.py:355-357`) even with a grid
  established.
- `loop_model.py:217` — `grid=grid_established`, the flag itself.

No bug today: the only rule `slot_runtime` asks about (`CLIP_LAUNCH`,
`SLOT_SWITCH`) gates on `WHILE_SOUNDING` and never reads `grid`. It becomes a bug
the first time `slot_runtime` asks about a `WHILE_GRID` action — `CLIP_STOP`,
say, which is on the roadmap by virtue of being a row.

**Fix:** one predicate, exported from `sl_grid_state`, used by both.
**Fate:** Handed to the user / Enforced later. Recommendation: name it
`GridState.is_established_and_phased()` and make `Session.grid`'s docstring say
which it means.
**Fails today if it regresses?** No.

### 🟢 Minor

- `divergences()` (`looper_timing.py:444`) says it is *"for the boot banner and
  tests"*. `grep -rn divergences scripts/` → the function, and nothing else. In
  the file whose thesis is that the code is the documentation.
- **D-number collision.** `looper_timing` uses D0-D4; `Documents/looper-audit-2026-09-07.md:162`
  refers to a **D5 that does not exist in the module** — it is
  `slot_runtime.DEFERRED_LAUNCH_GRACE_S`, unlabelled in code. Separately,
  `control_registry.py:205` and `scripts/lib/engine-guard.sh:2` use "spec defect
  D4" and "spec D5" from an unrelated numbering. Three schemes, one namespace.
- `docs/CODE-MAP.md:8` says *"Notably missing below: `looper_timing.py`"*;
  `:266` lists it. Both edited today. Two rows away, `:265` and `:269-270` cite
  `apc_footswitch.py`, which does not exist.
- `track_gesture.py:1297` uses a bare `assert` (stripped under `python -O`) on a
  synthetic `Session(sounding=True)`, so it can only fail if the table changes —
  which is the point, but say so.
- `tests/test_gesture_against_engine.py` drives `FakeSlEngine` and `MagicMock`.
  With `tests/engine/` in the tree, that name now means the opposite of itself.
- `tests/test_sl_grid_sync.py`, added today, says *"`DECISIONS.md` still states
  that `eighth_per_cycle` is 8"*; `DECISIONS.md:816` was corrected in the same
  change. And `scripts/sooperlooper/README.md:47` is now a 120-character line
  spliced mid-sentence.

## 6. Logic & business rules

The two open questions are handled well. D0's cost is measured, not asserted
(`looper_timing.py:237-241`, three grid conditions, six samples), the fix is
written out, and the reason it is not taken — "it changes playing feel, which is
Mitch's call" — is the correct boundary. D1 correctly separates *when the action
fires* from *when the gesture is dispatched* and points at `binding_table` rather
than modelling a release delay in a timing table. **Both are safe to leave open**;
the only defect is F2, that D0's pin does not touch the code path D0 lives on.

D5 is not safe in the same way. `slot_runtime.expire_deferred:575-583` fires a
queued launch off-grid after five seconds, which is a timing decision at a call
site, with a literal constant, in a module the timing table does not cover. The
guard at `:562-573` is thoughtful — silence drops the launch rather than forcing
it — but the 5.0 is unexplained and unnamed.

**Settings read once and treated as state.** The class the consolidation set out
to kill: `TrackGesture.quantized` is gone and its absence is documented at
`track_gesture.py:240-244`. Good. Survivors, in descending order of risk:

- `FakeSlEngine.__init__(quantized=True)` (`tests/fake_sl_engine.py:40-41`) —
  the fake's quantize behaviour is fixed at construction and every `/set` that
  would change it is discarded at `:62-63`. So the very control that
  `engine_controls` now centralises is invisible to the suite that checks it.
  This is the same defect the production code just deleted, still living in the
  instrument that measures it.
- `sl_grid_sync.EIGHTH_PER_CYCLE` (`:111`), `DEFAULT_CLOCK`, `DEFAULT_BPM`,
  `COUNT_IN`, `sl_osc_session.NUM_LOOPS` (`:42`) — module-import constants read
  from the environment. Correct for an appliance that restarts on config change;
  worth one sentence saying so, since the flag just deleted looked identical.

## 7. Test strategy & execution

**I ran it.** `python3 -m unittest discover -s tests`: **2130 tests, OK, 23
skipped, 120 s**. `python3 -m pytest -q`, run twice: first run **1 failed**
(`tests/test_audio_engine.py::GraphRestartTests::test_planned_promote_sync_path`),
second run **2107 passed**. That file passes 12/12 in isolation. So: a flake,
1-in-2 at suite scale, 0-in-13 otherwise. Not a looper test, but
`scripts/ci_gate.py` requires a green `unittest` job before any deploy, so the
deploy gate is now downstream of it. I did not establish the mechanism and will
not guess one; the candidate worth checking first is that `_bash_env`
(`test_audio_engine.py:48-49`) does `os.environ.copy()` while six test files write
`os.environ[...]` without restoring. **The finding is that a flake exists in the
gate's dependency and nothing records flakes**, so the next one gets a rerun
instead of an investigation.

**Read the doubles first, as instruments.**

| Double | Computes | Is handed |
|---|---|---|
| `FakeSlEngine` | state transitions for 8 `hit` verbs; buffer load/save bindings | **loop length** (`:180,185`), **quantize mode** (`:40`), and every one of the thirteen `/set` controls, which it discards (`:62-68`) |
| `MagicMock` OSC (23 files) | nothing | everything |
| `tests/engine/` real engine | length, state, position, `wet`, cycle — **from the engine** | the flags, which mirror the appliance's |

The axis the suite measures is *which messages were sent, in which order, into
which state machine*. The axis the reported bugs travel on is *how long, at what
phase, under which controls*. R1 and R2 both said this. The correct response
landed — but in a **second harness**, and the two are not reconciled.
`tests/test_fake_engine_fidelity.py` still opens with the right instinct (*"The
harness must be able to SEE the bugs that actually shipped"*) and its 130 lines
are still three classes of discrete-state entries. R1's own suggestion — record
the real engine's responses and diff the fake against the recording — is the
missing link between two harnesses and one instrument with a calibration.

**The class the fidelity harness still cannot remember:** anything where the
answer is a *number the system computed* rather than a *state it entered*. Seven
bars. A 221 BPM bar. A launch 0.4 of a cycle late. A `wet` that drifted
`0.9959 → 0.8604`.

**Credit, specifically.** `tests/engine/test_engine_claims.py` is 20 tests, 23 KB,
and includes the two duration tests that make "recorded seven cycles when it
should have been four" a sentence the repository can say for the first time. It
is wired into CI as its own job with a 30-minute budget. `test_clock_tail_ownership.py`,
`test_bench_call_sites.py`, `test_control_registry.py`'s AST walk and
`test_periodic_loop_lint.py` are all real. `ci_gate.py` treating "cannot ask" as
"refuse" is the correct polarity and rare.

## 8. Security & performance

Nothing. I scanned the four new files for addresses, hostnames, credentials and
tailnet ranges: clean. `tests/engine/harness.py:24-27` correctly discloses that
`--network host` opens 9951/udp on the laptop while a test runs.

No new polling loops today; `poll_grid_wait` and `expire_deferred` ride existing
polls, which is the documented rule, and `when()` is a dict lookup plus four
comparisons on the press path. Operationally, `ci_gate.py` adds a network round
trip and up to `--wait` seconds to every deploy and refuses when GitHub is
unreachable — the right call, worth knowing before it lands on a Saturday.

## 9. Documentation vs. reality

The doctrine — *documents are history, the code is the description* — is right,
and `test_docs_do_not_claim_authority.py` is the correct kind of enforcement:
executable, and it fails on a *new* unstamped spec, which is the case a manual
pass cannot cover.

Its coverage is the problem. It checks `Documents/specs/*.md`, `DECISIONS.md`,
`DIRECTION.md`, `AGENTS.md` and the audit. The audit
(`looper-audit-2026-09-07.md:100-107`) additionally claims fixes to
`README.md`, `docs/CODE-MAP.md` and `scripts/sooperlooper/README.md` — **all
three unchecked**, and two of the three still contain the contradiction they were
supposed to lose (F3; CODE-MAP `:8` vs `:266`).

Load-bearing claims checked against code:

| Claim | Where | Verdict |
|---|---|---|
| "Timing … is `looper_timing.py` and nowhere else" | `AGENTS.md:41` | **False as stated** — 3 of 12 actions route through it; `loop_model.py:164` and `slot_runtime.py:575` decide their own |
| "`looper_timing` refuses a second opinion in the test suite" | audit `:192` | **Half true** — F8 |
| "`binding_table` refuses a second claim on a control at import" | audit `:192` | **True** — `assert_no_binding_collisions`, called at import |
| "`looper_timing.when()` now answers for every action" | audit `:65` | **False** — see F1/F2 |
| "All 23 files in `Documents/specs/` carry a HISTORY stamp" | audit `:96` | **True**, and pinned |
| "`scripts/sooperlooper/README.md` says it is orientation, not authority" | audit `:107` | **True, and beside the point** — F3 |
| "`docs/CODE-MAP.md` admits it is stale and lists `looper_timing.py`" | audit `:105` | **True and self-contradictory** — `:8` says it is missing |
| "Four dead scripts … zero references at the time of removal" | audit `:117` | **True.** Checked all four; every surviving mention is a dated record or the audit itself, and `docs/CLASSIC-MIDI-PLAN.md:336-338` was correctly updated to past tense with the deletion date. This part was done properly |

**Could a new dev onboard in a day?** For the two consolidated questions, yes —
`looper_timing.py` and `binding_table.py` are readable in an hour and honest
about their own reasoning. For everything else, no: they would read
`scripts/sooperlooper/README.md`, believe there are sixteen tracks and that rows
1-7 are unimplemented, and be wrong on both by lunchtime.

**Build/deploy.** `looper-deploy.sh` → `ci_gate.py` → reset → restart is a real
improvement. The appliance-side smoke run is still missing, and
`smoke-16-loops.sh` and `measure-loop-alignment.py` remain scripts nothing calls.

---

## Verdict

The consolidation did the hard, unglamorous thing correctly: it found that one
question was being answered in five places, put it in one place, deleted the flag
that recorded the mode instead of the state, and — the part that actually
matters — stood up a real SooperLooper in CI so a claim about the engine can now
be contradicted by the engine. Nine findings from the previous two reviews are
genuinely closed and I verified each. But the new authority's guarantees are
weaker than its prose: `_assert_total` compares the file with itself, the record
branch that D0 is about never asks the table, the `RECORD_START` divergence is
pinned by a test that cannot see the code path it describes, and the guard
against a sixth opinion checks two of four controls in one syntax. Meanwhile the
oldest findings — the fake's missing clock, the README's phantom sixteenth track
— are eight days and two reviews old, and the README was edited *today* to add a
disclaimer above them. The pattern is not neglect; it is that this repository has
1.4 MB of reviews and not one finding ledger, so every session re-derives the
backlog instead of closing it. Fix that first and the rest gets cheaper.

## Priority backlog (🔴 only)

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

## Finding ledger

| # | Finding | Severity | Fate | Enforced by | Fails today if it regresses? |
|---|---------|----------|------|-------------|------------------------------|
| F1 | `_assert_total()` compares `ACTIONS` with `RULES`, both in the same file; `SCENE_LAUNCH`/`SCENE_STOP` are dead rows and `binding_table.ACTIONS` is an unreconciled second vocabulary | 🔴 | Enforced | *to write* — `tests/test_looper_timing.py`, cross-check against `binding_table.ACTIONS` | **No** |
| F2 | `loop_model.py:159-171` decides `RECORD_START` timing itself; the D0 test asserts the table, not the code path | 🔴 | Handed to user (plumbing now, gate unchanged) | `tests/test_looper_timing.py:175-177` exists but is a substring match | **No** |
| F3 | `scripts/sooperlooper/README.md` still says 16 tracks and "rows 1-7 reserved"; edited today to add a disclaimer above them | 🔴 | Fixed now + Enforced | *to write* — extend `tests/test_docs_do_not_claim_authority.py` | **No** |
| F4 | `Moment` omits `enforced_by`; `RECORD_START` is `BY_ENGINE` with `lands_on=GRID_BOUNDARY` ("our own timer") | 🟡 | Fixed now + Enforced | `Rule.__post_init__` second invariant | No |
| F5 | `looper_timing` is loaded twice (`looper_timing` and `scripts.sooperlooper.looper_timing`); two `RULES`, two `Session` classes | 🟡 | Fixed now | one import line in `tests/test_looper_timing.py` | No |
| F6 | Stop All restore assertion weakened from an ordered 4-element literal to a duplicate-collapsing `dict` in the same change as the fix | 🟡 | Fixed now | `tests/test_track_gesture.py` | Partly |
| F7 | `stop_all_loops` lifts four controls; `looper_songs.stop_playback` lifts one; `apply_freeform` omits `mute_quantized` | 🟡 | Enforced | *to write* — `OneAuthorityTests`: sending some keys of `engine_controls` means sending all | **No** |
| F8 | The AST guard covers 2 of 4 controls, `ast.List` only, `scripts/sooperlooper/*.py` + bench only; no negative control | 🟡 | Enforced | `tests/test_looper_timing.py:138-166`, widened + a falsification case | Partly |
| F9 | `Session.grid` is `next_boundary() is not None` in `slot_runtime`, `grid_established` in `loop_model` | 🟡 | Handed to user | — | **No** |
| F10 | Flaky `test_audio_engine.py::GraphRestartTests::test_planned_promote_sync_path` — 1 of 2 full pytest runs, 0 of 13 isolated; `ci_gate` depends on the job | 🟡 | Handed to user | — | **No** |
| F11 | `expire_deferred`'s 5 s off-grid launch (D5) is a timing decision at a call site with an unnamed literal, outside the table | 🟡 | Handed to user | `tests/test_slot_runtime.py` pins the behaviour, not its ownership | Partly |
| F12 | `MPE_LOOPER_EIGHTH_PER_CYCLE` honoured at `sl_grid_sync:253`, ignored at `:204` and `sl_grid_state:337` — **open 8 days** | 🟡 | Fixed now | *to write* | **No** |
| F13 | No review in `Documents/reviews/` (47 files, 1.4 MB) contains a finding ledger; the only index stopped 2026-08-15 | 🔴 | Deleted / Enforced — this file is the first ledger; the fix is that the next Step 0 reads it instead of re-deriving | — | **No** |

*Thirteen findings — four 🔴, nine 🟡. Every row is checkable against the tree as
of this file's date; where I could run the check I ran it and said so. Next
reviewer: this table is your Step 0. Mark each row CLOSED / OPEN-N-days /
DECLINED rather than re-deriving it, and leave one behind for the reviewer after
you — that is finding F13.*
