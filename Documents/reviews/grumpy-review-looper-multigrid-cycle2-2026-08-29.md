# Grumpy review — looper multigrid, CYCLE 2

**Date:** 2026-08-29 · **Branch:** main · **Range:** `665bb43..HEAD`
**Commits under review:** `7181e3e` (wrap quantization), `e0ffeae` (harness fidelity),
`a497876` (one entry path / non-blocking save), `cbec874` (TAIL phase).
Also in range but out of scope: `2fc657e` (mechanical rename), `e90531d` (audio sink).

**Read in full:** `slot_runtime.py`, `slot_surface.py`, `tail_phase.py`, the
`track_gesture.py` diff, `slot_matrix.py` / `slot_leds.py` / `led_table.py` /
`sl_bench_listener.py` / `sl_osc_session.py` diffs, `fake_sl_engine.py` diff,
`test_tail_integration.py`, `test_tail_phase.py`, the `test_slot_runtime.py` and
`test_slot_surface.py` diffs.
**Sampled:** `loop_model.plan_gesture` (the overdub branches), `test_multiclip_workflow.py`,
the bench wiring diff.
**Not read:** touch UI, audio-engine tests, `test_detect_*.sh` tests, `looper_songs.py`.

**Method note:** every 🔴 below was reproduced against the current tree with a scratch
harness, not inferred. The builder's "these fail against the pre-fix code" claim was
spot-checked by disabling the deferral in a worktree at HEAD (§6.2). Full suite at HEAD:
**1564 passed, 3 skipped**.

---

## 1. First impressions (the gut check)

Cycle 1 said the knowledge lived in prose and nothing enforced it. Cycle 2 moved a
meaningful amount of it into structure, and it shows. `LAUNCH_COMMANDS` is one constant
with one comment where there were two divergent implementations. `_hand_to_gesture` is one
function where there were three copies. `track_state()` is one accessor where there were
two caches. `on_wrap` is a real edge where `sl_state in ACTIVE_PLAY` was a level test
pretending to be one. `tail_phase.py` is a pure decision object with four named exits and
no OSC in it. This is the right direction and most of it is done well.

What has NOT changed is the failure *shape* this codebase keeps producing: **a state
machine with an entry condition and no latch.** Cycle 1's bug was `_overdub_pass = True`
on any OVERDUBBING report. Cycle 2 replaced it with `if self._tail is None:
self._begin_tail()` on any OVERDUBBING report — same shape, new object, and now the exit
sends a command that *toggles*, so re-entry is not merely wasteful, it is destructive
(§4🔴-1). The lesson from cycle 1 was written down in the review and not learned in the code.

The second recurring shape: **new deferred state with no owner on the abort paths.**
`_deferred` and `_awaiting` are two new dictionaries holding player intent across time.
`reset()` clears both. Nothing else does — not Stop All, not clear-a-slot, not
`forget_active_slot`, not a bank change. That produces §4🔴-2, which is the worst bug in
this cycle: **Stop All does not stop everything.**

---

## 2. Architecture & structure

### 2.1 The wrap boundary — is it sound?

Mostly yes, and it is a genuine improvement. The good decisions:

- **One detector.** `sync_loop_pos` calls `detect_loop_wrap` once and drives both the
  tail exit and `self._on_wrap()`. There is no second wrap implementation. Correct.
- **Per-track.** The callback is installed per gesture in `SlotSurface.__init__`
  (`slot_surface.py:72-74`), so two tracks wrapping at different times get two independent
  boundaries. Verified by construction — `set_wrap_callback` binds `loop` by default arg,
  the classic late-binding trap, and it is done right.
- **Wired at construction, not at the call site.** The comment says why ("production and
  every test harness get it by construction"). That is the correct answer to the cycle-1
  complaint that the surface was never given the clock.
- **Validate at press, fire at the boundary.** `_defer_launch` checks the clip file exists
  *now* so a missing file is a failed press, not a pad that waits a bar and does nothing.
  That is the right split and the docstring earns its keep.
- **A grace timer.** `DEFERRED_LAUNCH_GRACE_S` rides the existing state poll rather than
  adding a ticker — correct per AGENTS.md's CPU rule, and correctly reasoned ("a late
  switch is bad; a switch that never happens is worse").

**Behaviour on the cases the brief asked about:**

| Case | Behaviour | Verdict |
|---|---|---|
| Bank change with a launch deferred | Gestures exist for all 15 tracks and keep receiving `loop_pos`; the wrap still fires. | ✅ correct |
| Engine restart / `loop_pos` stops | No wrap; the 5 s grace fires an unquantized launch and logs it. | ✅ correct, and the log is honest |
| `loop_pos` gaps | `detect_loop_wrap` is a decrease test, so a gap that lands past zero still trips. | ✅ |
| Track with no grid | `bar_seconds` falls back to `loop_len`, then 2 s. Wrap needs `loop_len > 0`; with none, the grace covers it. | ✅ |
| Two tracks wrapping at different times | Independent per-track callbacks. | ✅ |
| **Stop All while a launch is deferred** | The wrap never comes (paused), the grace fires 5 s later, and the track un-pauses and plays. | 🔴 §4🔴-2 |

So: the wrap is a sound boundary. What is not sound is the **abort** story around it.

### 2.2 The peak subscription lifecycle

`on_tail_change(loop, active)` → `set_peak_updates`. Subscribe on `_begin_tail`,
unsubscribe on both `_end_tail` and `_abandon_tail`, and `_begin_tail` is guarded against
double-arming. I traced every exit:

| Exit | Unsubscribes? |
|---|---|
| `EXIT_DECAY` (`sync_in_peak`) | ✅ via `_end_tail` |
| `EXIT_CAP` (`poll_tail`) | ✅ via `_end_tail` |
| `EXIT_WRAP` (`sync_loop_pos`) | ✅ via `_end_tail` |
| `EXIT_ABANDONED` (engine leaves OVERDUBBING) | ✅ via `_abandon_tail` |
| Stop All | ✅ indirectly — `mute_on`/`pause_on` moves the state off OVERDUBBING, which hits the `_abandon_tail` branch on the next report |
| Reset All | ✅ indirectly — `undo_all` → OFF → abandon |
| Bank change | n/a — the tail is per-track and survives, correctly |

**The subscribe/unsubscribe is balanced.** Credit where due: this is the part of the tail
work that was done carefully, and `_abandon_tail`'s "send nothing, whatever ended it already
did" is exactly the right rule with exactly the right comment.

The one gap is not a leak but a hole: `set_peak_updates` is fire-and-forget with no
verification that the subscription took. `PI5-LOOPER-SEAM-WRAP.md` is cited in three
comments as the time every tail peak was silently dropped. The `poll_tail` cap is the
stated mitigation — and the cap has no integration test (§6.3).

### 2.3 Does "one entry path" hold?

**Substantially yes, with one new divergence introduced.**

The claim is real: `_hand_to_gesture` is the single handoff, `track_state()` is the single
state source, and `press`/`scene_press` now differ only in `tap`, which is a real
difference. The cycle-1 table of divergences (`expect_cleared` on one path only, two
`sl_state` sources) is genuinely closed. `_prepare_record` moving the binding move out of
`press()` and into the shared `_execute_slot_ops` closes the third.

The new divergence is the **third caller nobody counted**: `poll_pending`'s resume path
(`slot_surface.py:~305`) calls `_hand_to_gesture(plan, tap=False)`. That is wrong on two
counts and both are demonstrable — see §4🟡-1.

### 2.4 What is still deferred from cycle 1

- `_maybe_resolve` still contains a level-triggered `sl_state in ACTIVE_PLAY` path, now
  scoped to "nothing was held". The scoping is argued and the comment is honest. Accept.
- `SlotSurface` still reaches into gesture privates (`fs._tail` in tests, `fs._pad_down`).
  Unchanged from cycle 1's 🟢-2.
- Dead vocabulary (`ACT_STOP` / `PENDING_STOP`) is deleted, including the hand-constructed
  tests for it. ✅ Closed.

---

## 3. Code quality

**Naming.** `TailPhase`, `EXIT_DECAY/CAP/WRAP/ABANDONED`, `saw_loud`, `has_deferred`,
`resume_awaiting`, `awaiting_tracks`, `LAUNCH_COMMANDS` — all readable without the comment,
which is the test. `_end_tail` vs `_abandon_tail` is a genuinely useful distinction ("one
sends, one doesn't") and the naming carries it.

`SlotRuntime.boundary()` still does not detect a boundary — cycle 1 asked for
`commit_pending()`. Not done, and now it matters more, because `expire_deferred` calls
`boundary()` when explicitly *no* boundary arrived.

**Error handling.** `poll_flush` remains the best code in the repo — temp file, size floor,
fsync data, rename, fsync dir, and it refuses rather than losing audio. Splitting it into
begin/poll without weakening the safety rule is the highest-value change in this cycle and
the comment block above it ("the SAFETY RULE is unchanged and is the reason for the split
rather than a shortcut around it") is the right thing to have written.

**Types.** `self._on_wrap = None` is untyped and untyped-nullable in a class that otherwise
declares everything. `on_tail_change`, `on_wrap`, `on_grid_*` are all bare `=None`
parameters with no `Callable` annotation, unlike the `send:` injections in `SlotRuntime`.
Minor, but it is a regression in a file that was better typed than most.

**Dead-ish code.** `_defer_launch`'s `retrigger_only` branch mirrors `_launch`'s
`track.active_slot == plan.slot` branch, which cycle 1 established is unreachable
(`ACT_SWITCH` always has `slot != active`, `ACT_LAUNCH` always has `active is None`). Cycle 2
deleted the *other* dead vocabulary and then added a second copy of this one. 🟢.

---

## 4. Code smells (the hall of shame)

### 🔴 1. A stale OVERDUBBING report re-arms the tail, and the exit toggles

`track_gesture.py`:

```python
if sl_state == SL_STATE_OVERDUBBING:
    if self._tail is None:
        self._begin_tail()
elif prev_sl == SL_STATE_OVERDUBBING:
    self._abandon_tail()
```

There is no latch. `sync_from_sl` is called on **every** state auto-update, not only on
change (`BENCH_STATE_MS = 100`, and `FakeSlEngine.poll` models this faithfully). So the
sequence is:

1. The tail ends — decay or cap — and `_end_tail` sends `hit overdub` to leave the overdub.
2. A state report already in flight, sent by the engine before the hit landed, arrives
   saying OVERDUBBING.
3. `self._tail is None`, so **a second tail is armed**, on a loop that is now PLAYING.
4. That tail runs to its cap (one bar — the input is quiet, so `saw_loud` is never set and
   the decay exit cannot fire) and sends `hit overdub` **again**.
5. `overdub` on a PLAYING loop turns overdub **ON**. Nothing is armed to turn it off.

Reproduced against the current tree:

```
in_tail after cap: False   hits: ['overdub']
re-armed: True                      # one stale OVERDUBBING report
second overdub-off sent: ['overdub']
```

The end state is a loop silently recording the room over the take, indefinitely, with a
green pad. That is precisely the class AGENTS.md and both prior reviews name as the worst
one: silent, wrong, no error. It is intermittent (the window is the OSC round-trip inside a
100 ms report cadence, so single-digit percent per tail) and it is **certain** in the case
the `overdub` hit is dropped or delayed, which is the case the cap exists for.

Cycle 1 flagged this exact shape as 🟡-3 and prescribed the fix: *record why the overdub was
entered.* The prescription was not followed; the flag was moved to a new object.

*Fix:* latch it. A tail may only be armed on a **transition into** OVERDUBBING
(`prev_sl != SL_STATE_OVERDUBBING`), and additionally only when this object sent the
closing `overdub` — set an intent flag in `_gesture` when `plan.commands` contains
`overdub` from the take-closing branches. One line for the transition guard; that alone
kills the reproduction above.

### 🔴 2. Stop All does not cancel a deferred launch — the track restarts 5 s later

`stop_all_loops` (`track_gesture.py`) pauses and mutes every loop and resets the grid phase.
It does not know `SlotRuntime` exists. `SlotRuntime._deferred` still holds the launch, and
`Track.pending` still holds the intent, so `poll_pending` → `_maybe_resolve` →
`expire_deferred` keeps running. No wrap can arrive (the loop is paused), so the grace
expires and fires.

Reproduced:

```
after stop-all + grace: [('/sl/0/load_loop', [...s1.wav, '', '']),
                         ('/sl/0/hit', ['pause_off']),
                         ('/sl/0/hit', ['trigger'])]
```

Five seconds after the player hit Stop All, a track un-pauses and plays. On stage. Stop All
is the panic button — the one gesture that must be absolute — and cycle 1's audit ranked
"silent failure following the most common recovery action a performer takes" as the top P0
for exactly this reason.

The narrower version of the same hole: `_awaiting` also survives Stop All, so a parked press
can replay into a stopped instrument.

*Fix:* give the runtime a `stop_all()` / `abandon_pending()` that clears `_deferred`,
`_awaiting`, and every `Track.pending`, and call it from the bench's Stop All branch beside
`stop_all_loops`. The bench already calls `slot_surface.reset()` on the Reset All branch;
Stop All needs the softer sibling. Add the test the audit asked for and did not get.

### 🔴 3. `TailPhase.started_at` is on a clock the caller cannot set

```python
def _tail_clock(self) -> float:
    return time.monotonic()

self._tail = TailPhase(started_at=self._tail_clock(), cap_s=cap)
```

but `poll_tail(self, *, now: float | None = None)` and `sync_in_peak(..., now=...)` both
accept an injected clock, and `TailPhase.tick` computes `now - self.started_at`. Feed
`poll_tail(now=5.0)` to a gesture armed at `time.monotonic() == 500000` and you get
`-499995 >= cap` — False, for ever.

This is not a live bug (production passes `None` and both sides use `monotonic`). It is
worse than a live bug in one specific way: **it makes the cap untestable through the
gesture, and the cap is the exit that guarantees an overdub terminates when the meter never
arrives.** The `now=` parameter's own docstring says it exists so "a test that cannot advance
the clock can only ever assert that nothing happened yet" — and the cap has no such test
(§6.3). I hit this while probing: my first cap probe silently did nothing, and the tail
stayed armed.

*Fix:* inject the clock at construction like `SlotRuntime` already does
(`now: Callable[[], float] = time.monotonic`), and delete the per-call `now=` parameters.
One clock per object, chosen by the caller. Then write the cap integration test.

### 🟡 1. The parked-press resume is a third entry path, and it lies to the gesture

`slot_surface.poll_pending`:

```python
plan = self._rt.resume_awaiting(track_index, sl_state=self.track_state(track_index))
if plan is not None:
    self._hand_to_gesture(plan, tap=False)
```

`tap=False` means `fs.on_pad_down()` — but the pad's real up edge happened seconds ago,
while the press was parked. Reproduced:

```
after resume: osc: [('/sl/0/hit', ['undo_all']), ('/sl/0/hit', ['record'])]
gesture _pad_down latched: True   note: 8
```

The record fires (down edge), and the gesture is left holding a pad that nobody is holding.
Consequences: `hold_blink_pending` reads `self._pad_down`, so the pad blinks a phantom
long-press indefinitely; the mute/launch half of the gesture, which lands on the **up**, is
now desynchronised; and in the non-multigrid `poll_track_gestures` path this would fire
long-press-to-clear on a track the player is not touching.

Second defect on the same line: `note=None` is passed, so `_hand_to_gesture` resolves the
note via `note_for_cell`. If the player banked away while the save was in flight, that
returns `None` and the whole handoff **returns silently** — while `_prepare_record` has
already moved the binding. The runtime believes a take is recording into slot N; the engine
was never told.

*Fix:* `tap=True`. A resumed press has no pad to release — that is the exact situation
`synthesised_tap()` was built for, and the scene path already documents why. For the
off-view case, either park the note with the press or log the drop; a silent return here is
the same class of failure as the debounce-eaten scene launch.

### 🟡 2. `_awaiting` and `_deferred` are leaked by every abort that is not `reset()`

Cleared by: `press`/`dispatch` (any non-waiting outcome), `ACT_CANCEL`, `reset()`.
**Not** cleared by:

- `stop_all_loops` (§4🔴-2)
- `forget_active_slot` — the player long-presses the active pad to clear it, and a parked
  press for that track replays afterwards
- `_clear` (ACT_CLEAR on any slot) — clears the slot, leaves the deferred launch
- `set_view` — bank change (correct for `_deferred`, wrong for `_awaiting`, §4🟡-1)

Each individually is narrow. Collectively they are a category with no owner, which is the
same thing cycle 1 said about the boundary and the lane predicate.

*Fix:* one `SlotRuntime.abandon(track)` that drops `_deferred[track]`, `_awaiting[track]`
and `Track.pending`, called from every one of the above. Then a table test over
(abort path × parked state) so the next abort path added cannot forget.

### 🟡 3. A switch pressed during the tail saves a take that is still being recorded

`_maybe_mark_recorded` deliberately fires on `ACTIVE_PLAY`, which includes OVERDUBBING —
correct and well-argued. But that means the slot is marked `dirty` while the ring-out is
still being captured. A switch pressed during those ~1 bar sets `save_first`, and
`_begin_flush` sends `save_loop` mid-ring-out. The WAV on disk is the take minus its tail.
The player then hears the decay on the live buffer and not on the reloaded clip.

*Fix:* `_ensure_flushed` should return PENDING (not start the save) while that track's
gesture reports `in_tail`, and the existing parked-press machinery then does the right
thing for free. The plumbing already exists; only the condition is missing.

### 🟢 1. `boundary()` is still named for a thing it does not do

Cycle 1 asked for `commit_pending()`. Cycle 2 added `expire_deferred()`, whose entire
purpose is to call `boundary()` when no boundary arrived. The name is now actively wrong.

### 🟢 2. `_defer_launch`'s `retrigger_only` is unreachable

Mirrors `_launch`'s unreachable first branch, which cycle 1 documented as unreachable and
this cycle did not remove. Second copy of dead vocabulary, added in the commit that deleted
the first.

### 🟢 3. Typos in the rename

`sooperlooper-apc-bench.py` now reads "TrackGesturees are handed the raw client". Mechanical
sed artefact; the comment it lives in is otherwise load-bearing.

---

## 5. Logic & business rules

### 5.1 The parked-press state machine — can a take be lost? can a press be stranded?

**A take cannot be lost.** I traced it hard, because this is where the audio lives:

- `_begin_flush` writes to `path.name + ".part"` and only `os.replace`s over the real file
  once the size floor is met. A save that never lands destroys nothing.
- `poll_flush` on timeout drops the partial, keeps the original, stays `dirty`, and logs a
  message that names the actual state ("the take is still only in the engine buffer, and
  the clip already on disk is untouched").
- No caller can proceed on PENDING. `_execute_slot_ops`, `_prepare_record` (both branches)
  and `resume_awaiting` all return without touching the buffer.
- `undo_all` is only reachable on the `else` (nothing sounding) branch, after a CLEAN or a
  return. The test `test_the_buffer_is_never_reused_while_the_save_is_in_flight` asserts
  exactly this and asserts it on the right two verbs.

**"Refuse instead of replay" is the correct rule** and the reasoning in the docstring is
right: replaying a FAILED save finds the slot still dirty, starts a fresh save, parks, and
spins. The escape hatch is also right — "a later press by the player is a new decision and
does get a new attempt" — and `test_a_failed_save_does_not_retry_for_ever` pins the
one-attempt property with a `len(saves) == 1` assertion, which is the assertion that
actually discriminates.

**A press can be stranded**, in the two ways above (§4🟡-1 off-view, §4🟡-2 abort paths).
Neither loses audio; both lose the press silently, which is this project's stated worst
failure mode.

One more, minor: `resume_awaiting`'s FAILED branch returns a plan the surface logs but does
not repaint for. The refusal is visible in the log and not on the surface.

### 5.2 TAIL × deferred launch × Stop All

- **Tail vs deferred launch at the same wrap:** `sync_loop_pos` calls `_end_tail(EXIT_WRAP)`
  *before* `self._on_wrap()`. So the overdub is closed before `load_loop` swaps the buffer.
  That ordering is correct and, as far as I can tell, deliberate — but nothing says so and
  nothing tests it. Swapping those two lines would corrupt the take with no test failing.
  Worth a comment and a test; it is the kind of ordering this codebase has been burned by.
- **Tail vs parked press:** §4🟡-3.
- **Tail vs Stop All:** the tail is abandoned correctly (state moves off OVERDUBBING), and
  the peak subscription is released. ✅
- **Deferred launch vs Stop All:** §4🔴-2. ❌

### 5.3 A note on cycle 1's 🟡-3

Cycle 1 claimed any player-initiated overdub gets killed one pass later. Reading
`loop_model.plan_gesture`: a press on a PLAYING loop returns `mute_on`, not `overdub`.
There is no pad route into a musical overdub. **Cycle 1's 🟡-3 was over-rated on that
specific ground** and should be recorded as such. The *shape* it identified — arming from
engine state instead of from intent — is nonetheless the direct cause of §4🔴-1.

---

## 6. Test strategy & execution

### 6.1 What got genuinely better

`FakeSlEngine` is the real win of this cycle. Modelling `load_loop`/`save_loop` — and
modelling the one-argument form as a **no-op**, so the arity bug is catchable — is the
right kind of fidelity. So is the `running_before` snapshot in `boundary()`, whose comment
explains precisely why a naive implementation would end the ring-out in the breath that
started it. Adding a playhead so a wrap is a thing that can be *crossed* closes the hole
cycle 1 named ("every test that thought it was crossing a bar line was crossing nothing").

`test_the_press_does_not_sleep` measures wall-clock elapsed against a 50 ms bound. That is a
test of the actual property rather than of the implementation that provides it, and it will
survive a rewrite. Good.

### 6.2 Do the new tests fail for the right reasons?

I checked the builder's claim rather than taking it. In a worktree at HEAD I disabled the
deferral (`_execute_slot_ops` always launches immediately) and the deferred branch in
`_maybe_resolve`, then ran the three affected suites:

```
FAILED test_slot_surface.py::PendingResolutionTests::test_the_engine_reaching_the_target_resolves_it
FAILED test_slot_runtime.py::SwitchSafetyTests::test_a_dirty_buffer_is_saved_before_the_switch
FAILED test_slot_runtime.py::StrandedSwitchTests::test_a_wrap_that_never_comes_launches_anyway
FAILED test_slot_runtime.py::StrandedSwitchTests::test_the_hold_survives_until_the_grace_runs_out
4 failed, 72 passed
```

**The claim holds.** In particular `test_the_hold_survives_until_the_grace_runs_out` fails on
`assertNotIn("/sl/0/load_loop", ...)` — it is the test that pins the actual audio behaviour,
not the model behaviour, and it discriminates. `test_the_engine_reaching_the_target_resolves_it`
asserts "playing is not a boundary" and then that the wrap *is*, which is the right pair.

The honest qualification: **only four of 76 tests in those suites discriminate**, and the
"audio does not move at press time" property is pinned only at the `SlotRuntime` level. No
`SlotSurface`-level test asserts that a pad press sends no `load_loop`, so the surface half
of the quantization is guarded by the model assertion alone.

### 6.3 What is asserting its implementation back to itself, and what is missing

- **`test_a_peak_reaches_the_gesture` asserts `fs._tail.saw_loud`.** Reaching into a private
  to assert an internal flag. The observable property is "a peak that says loud, followed by
  quiet, ends the overdub" — assert the `hit overdub`, and the test survives the refactor
  that §4🔴-3 needs.
- **No cap test through the gesture.** `test_tail_phase.py` covers `TailPhase.tick` in
  isolation, where the clock is coherent. `test_tail_integration.py` has no cap test at all —
  which is how §4🔴-3 shipped. The cap is the exit that exists specifically for the case the
  meter never arrives; it is the one exit with no wiring coverage.
- **No re-arm test.** Feeding two consecutive OVERDUBBING reports is a three-line test and
  it is the whole of §4🔴-1.
- **No Stop All × matrix test.** The cycle-1 audit filed this as its own P1 ("No test exists
  combining Stop All with a subsequent matrix launch"). It still does not exist, and §4🔴-2
  is what walked through the gap.
- **No abort-path table.** Nothing tests that `_awaiting`/`_deferred` are cleared by clear,
  forget, bank change, or Stop All.
- `test_a_peak_for_an_unbound_loop_does_not_raise` asserts nothing. It is a smoke test
  wearing a test's clothes; either assert the peak was dropped or delete it.

---

## 7. Security & performance

Nothing security-relevant; no network surface, no secrets, no untrusted input.

Performance, against AGENTS.md's CPU rule:

- **`poll_tail` in `poll_track_gestures`** is a new per-tick loop over 15 objects, each a
  `None` check. No fork, no syscall, negligible. Correctly placed before the multigrid
  early-return so it runs in both modes, and the comment says why. ✅
- **`expire_deferred`** rides the existing `poll_pending`. No new ticker. ✅
- **`BENCH_PEAK_MS = 25`** on one loop for ~one bar after a take closes, subscribed and
  unsubscribed on demand rather than standing on 15 loops. This is the right trade and the
  constant's docstring computes it. ✅ Best CPU-discipline decision in the diff.
- **`poll_flush` does a `stat()` per idle tick per in-flight save.** One syscall, at most a
  couple of tracks, bounded at 2 s. Fine, and vastly better than the `time.sleep` loop it
  replaced.

No PR line stating compute × cadence, which AGENTS.md asks for. The constants' docstrings
substantially do the job.

## 8. Developer experience

A new dev reading `tail_phase.py`'s module docstring learns the feature, its measured
history, the four exits, and why `saw_loud` exists — in 25 lines, without opening another
file. That is the standard the rest of the repo should be held to.

`Documents/reviews/` now has a cycle-1 review, a cycle-1 audit, and this. That is a real
decision record and it is being used — most of cycle 1's confirmed P0s are actually fixed,
which is more than most review loops achieve.

The onboarding hazard is unchanged: 1564 tests, all green, with three reproducible live
defects in the code they cover. A new dev will trust the suite more than it deserves.

---

## The good, the bad, and what smells

**Good.** `LAUNCH_COMMANDS` — one answer to "how do you start a loop." The non-blocking
save, with the safety rule intact and the temp-file/fsync/rename dance unchanged. The wrap
as a single per-track boundary, wired at construction. `_hand_to_gesture` and `track_state()`
collapsing the entry paths for real. `FakeSlEngine` growing a playhead and buffer ops. The
on-demand peak subscription. `tail_phase.py` as a pure object with named exits.

**Bad.** Stop All does not stop a deferred launch. A stale state report re-arms the tail and
the exit toggles overdub on. Both are silent, both are on the panic-button and
take-closing paths, both are the exact failure class this project has written down twice.

**Smells.** Two new dictionaries of deferred intent with no owner on the abort paths. A
clock that is injectable on one side of the same object and hardcoded on the other. A resume
path that re-enters through the "one entry path" carrying the wrong edge. A private-flag
assertion standing in for a behavioural one. And `boundary()`, still named for the thing
`expire_deferred()` exists to admit did not happen.

---

## Verdict

This is a real cycle of work, not a papering-over: four of cycle 1's five confirmed P0s are
genuinely closed, the harness can now see the bug classes it was blind to, and the test
red-check the builder claimed does hold where it matters. But the cycle reproduced its own
predecessor's defining mistake — arming a state machine off an engine report with no latch —
in a new object, where the exit now sends a toggling command; and it added two new stores of
deferred player intent that Stop All does not clear. Both are reproducible today, both are
silent, and both sit on the paths a performer uses when something has already gone wrong.
Fix the three 🔴s (two of them are a handful of lines) and this is shippable to the
appliance for real play; ship it as-is and the next bug report will be "it started playing
by itself after I stopped everything," which is the hardest kind to reproduce and the worst
kind to have on stage.

## Priority backlog

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
