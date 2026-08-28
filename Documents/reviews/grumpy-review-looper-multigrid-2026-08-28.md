# Grumpy review — looper multigrid

**Date:** 2026-08-28 · **Branch:** main · **HEAD:** a4ad52c
**Scope:** `scripts/sooperlooper/` (slot_runtime, slot_surface, slot_matrix, apc_footswitch,
loop_model, slot_leds, apc_transport, apc_panel, apc_leds, apc_link, sl_grid_sync,
sl_loop_states, sl_bench_listener), `scripts/sooperlooper-apc-bench.py`, plus
`scripts/mpe-pressure-remap.py` / `scripts/midi-clock-in.py` for resource ownership, plus `tests/`.

**Read in full:** slot_runtime.py, slot_surface.py, slot_matrix.py, loop_model.py,
apc_footswitch.py, slot_leds.py, sl_bench_listener.py, sl_grid_sync.py, sl_loop_states.py,
fake_sl_engine.py, test_multiclip_workflow.py.
**Sampled:** sooperlooper-apc-bench.py (main loop, device open/reopen, multigrid wiring),
apc_panel.py, test_slot_runtime.py / test_slot_surface.py / test_slot_matrix.py (setup
patterns and assertions), grep-level pass over every `close_port` / rtmidi construction in
`scripts/`.
**Not read:** looper_songs.py, loop_mix.py, sl-watchdog.py, sl_probe.py, the touch UI,
audio-engine tests.

---

## 1. First impressions (the gut check)

This does not look like a hackathon. It looks like a codebase written by someone who has
been burned, repeatedly, by the same physical instrument and has been writing the burns
down. The comments in `slot_runtime.py` and `apc_footswitch.py` are the best artifact in
the repo: they cite dates, measured numbers, engine source files, and the symptom the
player reported. `apc_panel.py`'s header — "three wrong answers to a question the hardware
answers unambiguously" — is the kind of thing most teams never write.

And that is also the problem. **The knowledge lives in prose, not in structure.** Almost
every comment in `slot_runtime.py` is a load-bearing invariant that nothing enforces:
"the footswitch owns every command in this lane," "no OSC here," "the binding moves NOW."
Those are the rules of the system. They are enforced by whoever reads the comment before
editing the function. Ten of them are one careless refactor away from silently going away,
and the test suite would not notice — see §6.

The owner's hypothesis — "repetitive smelly code that isn't reusing functions properly" —
is **half right and misdiagnosed**. There is very little copy-paste. What there is instead
is worse: two *complete, independent implementations of the same concept* that were never
reconciled, plus a third concept (SooperLooper's own quantization) that neither of them
models. It is not duplication of code. It is duplication of **authority**.

---

## 2. Architecture & structure

### 2.1 The layering, as built

```
bench (event loop, MIDI, OSC client)
  └─ SlotSurface        input routing, LEDs, "did the boundary arrive?"
       ├─ SlotRuntime   slot occupancy, files, load/save OSC
       │    └─ slot_matrix  (pure) plan_cell_press / apply_pending / resolve_at_boundary
       └─ LoopFootswitch × 15   gesture → hit verbs
            └─ loop_model  (pure) plan_gesture
```

The decision to keep two pure planner modules (`slot_matrix`, `loop_model`) with no I/O is
correct and is the reason this is reviewable at all. The decision to make the active lane
*forward* to the footswitch rather than re-decide (`slot_matrix.py:973`) is also correct,
and the comment explaining why is accurate.

### 2.2 The shape that is wrong

The multigrid layer was built as a **sibling** of the single-clip layer with a hand-drawn
border between them, rather than as a **layer above** it. The border is drawn twice, in
prose, in two files:

- `slot_runtime.py:37-40` — `GESTURE_ACTIONS = {ACT_FORWARD, ACT_RECORD}`, "runtime must
  not send parallel OSC for these."
- `slot_surface.py:517-536` — `_is_active_lane`, "Must agree with `plan_cell_press`
  exactly."

Two predicates for one question, in two files, with a comment in each saying they must
agree. That is the definition of an unenforced invariant, and it is where the split-brain
comes from. There is no test asserting `_is_active_lane(note) == (plan_cell_press(...)
.action == ACT_FORWARD)` over the cross product of track states — which is a five-line
property test that would have caught the whole class.

### 2.3 The boundary is the real architectural hole

There are **three** notions of "the bar arrived" in this feature and none of them is
defined in terms of the others:

| # | Whose boundary | Where | What it does |
|---|---|---|---|
| 1 | SooperLooper's | `sl_grid_sync.py:120-141` (`quantize`, `sync`, `mute_quantized`) | Actually defers audio |
| 2 | `LoopFootswitch`'s | `apc_footswitch.py:485-509` (`awaiting_quantize`, 6 s timeout) | Gates gestures |
| 3 | `SlotRuntime`'s | `slot_runtime.py:100-102` → `resolve_at_boundary` | Moves `active_slot`, repaints |

Boundary 3 is triggered by `SlotSurface._maybe_resolve` (`slot_surface.py:690-702`), whose
test for "a boundary arrived" is `sl_state in ACTIVE_PLAY`. For a track that is already
playing — which is *every* switch — that is true on the very next state callback, i.e.
immediately. **It is not a boundary. It is "the track is playing."**

The correct signal exists and is already being delivered: `loop_pos` wrap. `LoopFootswitch`
has a real wrap detector (`sl_grid_sync.detect_loop_wrap`, used at
`apc_footswitch.py:300-320`). `SlotSurface` never receives `loop_pos` at all —
`sl_bench_listener.py:54-56` routes `loop_pos` only to the footswitch and drops it on the
floor for the surface. The one component that reasons about boundaries is the one component
that is not given the clock.

**This is the fundamental shape error, and yes, it should be restructured.**

### 2.4 What the right shape is

One boundary source, one scheduler, one place effects are emitted.

1. **`loop_pos` becomes a first-class input to the surface.** Add
   `SlotSurface.on_loop_pos(track, pos)` and route it in `sl_bench_listener.on_update`.
   Reuse `detect_loop_wrap` — do not write a second one.
2. **Collapse `Track.pending` into a deferred-effect queue, not a state flag.** Today
   `Pending` records *what the model will become*; nothing records *what OSC to send when
   it does*. Make it hold the plan:
   `Pending(kind, to_slot, from_slot, on_boundary: tuple[SlotPlan, ...])`.
3. **`SlotRuntime.boundary(track)` executes those effects and then advances the model** —
   in that order, in one function. Not "surface resolves the model, and separately `_launch`
   already sent the OSC three bars ago."
4. **`_launch()` stops being called from `_execute_slot_ops` at press time.** A press
   produces a plan; a boundary executes it. The only thing a press may do immediately is
   what the player must hear immediately: nothing, for a switch.
5. **One lane predicate.** Delete `SlotSurface._is_active_lane` and ask
   `plan_cell_press(...).action == ACT_FORWARD` — the planner is pure and free to call.
6. **One entry path.** `press()`, `dispatch()` and `scene_press()` all become thin adapters
   over a single `_apply(plan, sl_state)`. See §4🔴-3.

That is a real refactor — call it 2–3 days with the tests rewritten — but it is smaller
than the sum of the next five one-at-a-time bug hunts, and it removes the class rather
than the instance.

### 2.5 Dependency management

Clean. `python-rtmidi` + `python-osc`, no framework, no vendored anything. The `sys.path`
insert games in the tests (`tests/test_multiclip_workflow.py:36`) are ugly but honest for a
scripts-directory layout. No complaint.

---

## 3. Code quality

**Naming.** Mostly excellent and self-documenting (`_needs_flush`, `save_first`,
`expect_cleared`, `forget_active_slot`). Three exceptions:

- `LoopFootswitch` (`apc_footswitch.py:95`). The owner is right; see §4🟡-1.
- `SlotRuntime.boundary()` (`slot_runtime.py:100`) is named for a thing it does not do. It
  does not detect a boundary and does not act on the engine; it advances a model. Call it
  `commit_pending()` until it earns the other name.
- `SlotSurface.press()` vs `SlotRuntime.press()` vs `SlotSurface.scene_press()` — three
  `press`es at two layers with different contracts.

**Error handling.** Good where it matters: `_flush_active` (`slot_runtime.py:277-329`) is
genuinely careful — temp file, size floor, fsync data, rename, fsync dir, and it refuses to
proceed rather than losing audio. That routine is the best code in the review. Two bare
`except Exception: pass` at `sooperlooper-apc-bench.py:461` and
`calibrate-patch-normalization.py:405` are the usual sin, but both are on shutdown/probe
paths where the alternative is worse.

**Dead code.** Real, and misleading — see §4🟡-2.

**Types.** `from __future__ import annotations` throughout, dataclasses frozen, `Callable`
signatures on injected sends. This is better typed than most Python I review. One lie:
`apc_footswitch.py:608` sets `self._pending_since = None` where every other write is a
float. Harmless today only because `_expire_pending` short-circuits on `_pending is None`.

---

## 4. Code smells (the hall of shame)

### 🔴 1. `load_loop` fires at press time; the mute it pairs with fires at the bar

`slot_runtime.py:223-245`:

```python
def _launch(self, plan: SlotPlan) -> bool:
    ...
    self._send(f"/sl/{loop}/load_loop", [str(path), "", ""])
    self._send(f"/sl/{loop}/hit", ["mute_off"])
```

`sl_grid_sync.py:141` sets `mute_quantized = 1.0`. So SooperLooper defers the `mute_off` to
the cycle boundary — and executes the `load_loop` **immediately**, because loading a buffer
is not a quantized operation. On a switch from a *sounding* slot A to slot B, the sequence
the player hears is: press mid-bar → A's buffer is overwritten with B's audio *now* → the
rest of the bar plays B, out of phase, from wherever the playhead happened to be → the
boundary arrives and the already-unmuted loop does nothing visible.

**This is the answer to "stop and play is quantized, record is quantized, but clips
aren't."** It is not a missing quantize setting. It is a command that cannot be quantized
being issued at press time next to one that is.

*Fix:* the `load_loop` must move behind the boundary — queue it as the pending's effect and
send it from `SlotRuntime.boundary()` (§2.4). Short of the full restructure, the honest
interim is to load into the buffer only after `mute_on` has actually landed (state observed
in `SILENT`), which costs one bar of silence on a switch but is at least coherent.

### 🔴 2. `_maybe_resolve` calls "playing" a boundary

`slot_surface.py:690-702`:

```python
elif pending.kind in (PENDING_LAUNCH, PENDING_SWITCH):
    arrived = sl_state in ACTIVE_PLAY
```

Combined with `poll_pending()` (`slot_surface.py:582-604`), which runs this every idle
tick against the *cached* `self._sl_states`, a pending switch on a playing track resolves
on the first poll after the press — microseconds. The model advances, both pads repaint,
and the player is told the switch happened. The engine was never scheduled to do anything
at a boundary in the first place (see 🔴-1), so the two halves are wrong in a way that
happens to look self-consistent on the LEDs. That is the worst kind of wrong: the surface
lies confidently.

*Fix:* resolve on a `loop_pos` wrap, from `detect_loop_wrap`, delivered to the surface.
Never from a level-triggered state test.

### 🔴 3. Three entry paths, one of which is not a real gesture

`SlotSurface.press()` (`slot_surface.py:458`), `SlotRuntime.dispatch()`
(`slot_runtime.py:104`), `SlotSurface.scene_press()` (`slot_surface.py:489`).

The already-fixed binding bug (`_prepare_record`, `slot_runtime.py:211-220`) is the second
instance of this, not the last. Compare what `press()` does after a plan against what
`scene_press()` does:

| | `press()` | `scene_press()` |
|---|---|---|
| `fs.set_note(note)` | yes | yes |
| `fs.expect_cleared()` on ACT_RECORD | **yes** (`:477-482`) | **no** |
| pad down | yes | yes |
| pad up | on real note-off | synthesised immediately (`:510`) |
| `sl_state` source | `fs.sl_state` (`:466`) | `self._sl_states` (`:497`) |

Two live defects fall out of that table:

- **A scene launch on a row containing an empty slot arms a record without
  `expect_cleared()`.** The footswitch still derives `playing` from its last engine report,
  so its gesture is a *mute*, not a record — the exact symptom documented at
  `apc_footswitch.py:526-539` as fixed. It is fixed on one path only.
- **Two different sources of truth for `sl_state` in the same class.** `press()` reads
  `fs.sl_state`; `scene_press()` reads `self._sl_states`. These are updated from the same
  callback (`sl_bench_listener.py:47-50`) but in sequence, and `_sl_states` also carries
  entries for tracks whose footswitch was never polled. Any drift between them means the
  *planner* and the *executor* saw different engine states for the same press.

*Fix:* one private `_apply(plan, *, sl_state, note)` that all three call. One accessor for
`sl_state`. Delete the other.

### 🔴 4. `ACT_LAUNCH` cannot start a paused loop

`slot_runtime.py:243-244` launches with `load_loop` + `mute_off`. But `loop_model.py:1396-1403`
documents — and `sl_grid_sync.py:136-139` repeats — that `mute_off`/`trigger` **does not
lift a pause**, which is why the single-clip launch sends `pause_off` then `trigger`.

`stop_all_loops` (`apc_footswitch.py:826-828`) sends `mute_on` **and** `pause_on` to every
loop. So after any Stop All, every matrix launch of a stored clip sends a `mute_off` to a
PAUSED loop and nothing sounds. Two implementations of "launch," one of them knows the
engine's rule and one does not.

*Fix:* a single `launch_commands()` helper in `loop_model.py` that both callers use.

### 🔴 5. Cancelling a queued switch pauses the clip that was playing

`slot_matrix.py:936-948` makes a re-tap of the *outgoing* slot (`pending.from_slot`) an
`ACT_CANCEL`. `slot_runtime.py:156-158` implements cancel as:

```python
if plan.action == ACT_CANCEL:
    self._send(f"/sl/{loop}/hit", ["pause_on"])
```

The outgoing slot of a switch is the slot that is currently **sounding**. "Never mind, stay
on A" therefore stops A. `pause_on` is right for cancelling a queued *launch* onto a silent
track (it is copied from `loop_model.py:1294-1306`, where the loop is muted) and wrong for
cancelling a switch. One handler, two situations, no distinction.

*Fix:* branch on `pending.kind` — `PENDING_LAUNCH` → `pause_on`; `PENDING_SWITCH` →
drop the queued load and send nothing.

### 🟡 1. `LoopFootswitch` is a lie, and a cheap one to fix

There is no foot switch in this product and never was. The class is *the per-track gesture
interpreter for one pad*: press/hold/release → engine verbs, plus that track's LED and its
unconfirmed-intent model. The name has produced real confusion — it is why
`slot_surface.py` reaches inside it for `poll_hold`, `hold_fired`, `_sync_led` and
`_on_grid_dropped` (four private accesses across the boundary) rather than treating it as
the peer object it is.

**Proposed name: `TrackGesture`** (file `track_gesture.py`), with `poll_footswitches` →
`poll_track_gestures` and `footswitches_by_loop` → `gestures_by_track`.

**Blast radius, measured, not estimated:** the symbol `LoopFootswitch` appears **88 times
across 23 files** — 6 product modules (`apc_footswitch`, `sl_bench_listener`, `slot_leds`,
`slot_matrix`, `slot_runtime`, `slot_surface`), 7 test modules, 10 docs. The word
"footswitch" appears in 52 files, mostly docs and historical reviews. The code rename is
mechanical and low-risk (`git grep -l | xargs sed`, one pass, run the suite); the doc
sprawl is the only tedious part and archived reviews should be left alone as historical
record. **Do it — one commit, no behaviour change, before the restructure in §2.4, so the
restructure's diff is readable.**

### 🟡 2. Dead vocabulary that live code still branches on

`ACT_STOP` and `ACT_CLOSE` (`slot_matrix.py:48,50`) are **never produced by any planner** —
verified by grep across `scripts/` and `tests/`. Therefore:

- `apply_pending`'s `ACT_STOP` branch (`slot_matrix.py:263-264`) is unreachable, so
- `PENDING_STOP` can never exist, so
- `slot_leds.py:67` (`PENDING_STOP` → yellow blink) is dead, and
- `slot_surface.py:695-696` (`pending.kind == PENDING_STOP` → `arrived = sl_state in
  SILENT`) is dead.

`slot_runtime._launch`'s first branch (`:226-230`, `track.active_slot == plan.slot`) is
also unreachable: `ACT_SWITCH` always has `slot != active`, and `ACT_LAUNCH` always has
`active is None`.

This is not cosmetic. Four live-looking branches implementing a *stop* semantic that the
matrix does not have is precisely the material from which someone rebuilds the wrong
mental model. `test_slot_matrix.py:163,213` even asserts on `PENDING_STOP` states
constructed by hand — tests for a state the instrument cannot reach.

*Fix:* delete `ACT_STOP`, `ACT_CLOSE`, `PENDING_STOP` and all four branches, or implement
per-clip stop. Do not leave it half-present.

### 🟡 3. `_overdub_pass` cannot tell ring-out from a musical overdub

`apc_footswitch.py:256-260`:

```python
if sl_state == SL_STATE_OVERDUBBING:
    self._overdub_pass = True
```

Unconditional, on any report of OVERDUBBING regardless of origin. `_end_overdub_pass`
(`:322-339`) then sends `overdub` at the next wrap and kills it. Since `plan_gesture`
returns `("overdub",)` for a press on a playing loop in the tail-capture path
(`loop_model.py:1360-1370`) and `FakeSlEngine` models `overdub` on PLAYING → OVERDUBBING,
**any** overdub the player starts is terminated one pass later. The docstring claims the
arming is safe because it is derived from engine state rather than from the command sent —
but that is exactly what makes it wrong: the engine's report does not carry intent, and the
comment mistakes "derived from the engine" for "correct."

This is the concrete case for the missing rule in §5.2. The fix is to record *why* the
overdub was entered — set a `_ring_out_armed` flag when **we** sent the closing `overdub`,
and require both it and `sl_state == OVERDUBBING` before scheduling the auto-off.

### 🟡 4. Resource ownership: the fix was pasted, not shared

`mpe-pressure-remap.py:257-270` and `midi-clock-in.py:51-56` now both contain the same
three-step teardown:

```python
for step in ("cancel_callback", "close_port", "delete"):
```

Two copies of a rule that cost a cross-process outage, in two files, with the explanation
in one of them. There is no shared helper, no test, and — worse — **no stated rule**. The
same construct-and-drop pattern still exists elsewhere:

- `calibrate-patch-normalization.py:404` — `rtmidi.MidiOut().get_ports()` inside a
  0.5 s-interval retry loop. CPython's refcount destroys the temporary, so this probably
  does not leak; "probably" is the problem. Nothing in the repo says which of these is safe.
- `midi-clock-in.py:137,182`, `midi-clock-out.py:59,153` — process-lifetime probe objects,
  each holding a client for the life of the process, never deleted.
- `spike-router-hop-latency.py:89-93` — five virtual ports, no teardown at all.

For the record, `sooperlooper-apc-bench.py:459-460` (`reopen_apc`) is **fine**: it reuses
the same `MidiIn`/`MidiOut` objects across close/open, so no client is allocated per
reconnect. Worth stating explicitly, because it looks like the leaky pattern.

*Fix:* one `midi_ports.py` with `open_input(...)` / `close_input(...)` context managers
carrying the comment once, plus a line in `AGENTS.md`: *an rtmidi object owns an ALSA
sequencer client; `close_port()` frees the port, `delete()` frees the client; a MidiIn/Out
that outlives its use must be deleted.* Then a test that constructs and tears down N
inputs and asserts `/proc/asound/seq/clients` returns to baseline — the appliance already
has the reader for this in `sooperlooper/midi_subscription.py`.

### 🟢 1. `apc_footswitch.py` is 842 lines and holds four responsibilities

Gesture state machine, LED animation, grid-clock establishment/phase re-anchor, and
module-level transport helpers (`reset_all_loops`, `stop_all_loops`). The grid-clock half
(`_maybe_establish_grid`, `_try_commit_phase_reanchor`, `_flush_deferred_grid_side_effects`,
~90 lines) is per-*session*, not per-track, yet lives on a per-track object and reaches
callbacks upward. It belongs beside `GridState`.

### 🟢 2. `slot_surface.py` reaches into footswitch privates

`fs.poll_hold()`, `fs.hold_fired`, `fs._sync_led()` (via `apply_view`), `fs.grid`,
`fs._on_grid_dropped` (`apc_footswitch.py:790-792`). The comments explain each one, which
is more than most codebases manage, but a `TrackGesture` with a deliberate public surface
would need none of them.

---

## 5. Logic & business rules

### 5.1 What is expressed well

`loop_model.plan_gesture` is a genuinely good state machine: the `sl_state` vs `pending`
split (`loop_model.py:1167-1178`) is the right decomposition, "the LED renders `pending` as
a *blink* and only ever paints a solid colour from `sl_state`" is a rule you can check by
looking at a pad, and it is enforced structurally in `led_for`. `slot_matrix.plan_cell_press`
is likewise pure and exhaustively testable. Keep both.

### 5.2 The missing rule — derived vs stored

The owner is right that this is unstated, and it is the generator of at least four of the
bugs above. State the rule and enforce it:

> **Derived** from the engine, never stored: whether a loop is sounding, recording,
> muted, paused; its length; its position. Read `sl_state` / `loop_len` / `loop_pos` at
> the point of use.
> **Stored** by the bench, because the engine has no concept of it: which slot is bound to
> a track's buffer (`active_slot`), what is on disk (`Slot.file`, `Slot.dirty`), what the
> player asked for that has not happened yet (`pending`), **and why we issued a command**
> (intent — the thing `_overdub_pass` is missing).
> **Never stored:** anything the engine reports. If you find yourself caching an engine
> value to compare against later, you want an intent flag, not a cache.

`SlotSurface._sl_states` (`slot_surface.py:402`) violates the first clause today, and
`_maybe_resolve` reading it from a poll is how a level-triggered test got mistaken for an
edge.

### 5.3 Race conditions and edge cases

- **`_flush_active` blocks the event loop for up to 2 s** (`slot_runtime.py:302-319`,
  `SAVE_TIMEOUT_S = 2.0`, `time.sleep(0.01)` in the loop). It is called synchronously from
  a pad press. For those 2 s the bench processes no MIDI, no OSC state updates, no LED
  polls, and `poll_footswitches` does not run — a held pad's hold timer does not advance,
  and the APC's input buffer fills. On the failure path this happens *every* time. A 2 s
  freeze on a musical instrument during a take is not an acceptable failure mode even when
  the data handling is correct. Make it a state machine polled from the main loop.
- **`_declined` is a single slot for all 15 tracks** (`slot_surface.py:409, 661-663`).
  Two tracks alternating declines log every time; one track declining repeatedly logs once.
  The dedupe key includes the track, but the storage does not. `dict[int, tuple]`.
- **`scene_press` synthesises `on_pad_up()` immediately after `on_pad_down()`**
  (`slot_surface.py:509-510`). `LoopFootswitch._gesture` is debounce-guarded
  (`apc_footswitch.py:479-480`, `_debounced()`), so with any non-zero `debounce_ms` the
  synthesised "up" is **silently dropped** — and the up edge is where mute/launch lives
  (`loop_model.py:1389-1403`). A scene launch of a stored, muted clip therefore does
  nothing on a real appliance, while passing in tests that construct footswitches with
  `debounce_ms=0` (`test_slot_surface.py:56`). **This is a 🔴 hiding in a 🟢's clothing,
  and it is exactly the "different behaviours at different times" the owner reports.**
- **`Track.with_slot` does not bounds-check** (`slot_matrix.py:874-877`) while `slot()`
  does. `with_slot(8, ...)` raises `IndexError` out of a MIDI callback.

---

## 6. Test strategy & execution

1519 passing tests, and every defect above shipped. That is not bad luck; it is a
structural property of this suite.

### 6.1 What the suite genuinely does well

`tests/fake_sl_engine.py` is a real behavioural model, not a mock, and its docstring states
the right principle ("quantized actions sit pending until `boundary()`... that gap is where
the bugs live"). `tests/test_multiclip_workflow.py`'s rule — *state is only ever created by
gestures* — is the correct diagnosis of the previous failure, written down where the next
person will read it. `Session.deliver()` (`test_multiclip_workflow.py:99-121`) delivering
**only changes** rather than the current state every round is a genuinely sophisticated
piece of harness design, and it is the reason the "flashing forever" bug was reproducible.
Credit where due: this is better than most test suites I review.

### 6.2 Why it still cannot catch this class

**(a) The fake engine models `hit` verbs and nothing else.** `FakeSlEngine.send_message`
returns early for any path whose third segment is not `hit` (`fake_sl_engine.py:50-52`).
So `load_loop` and `save_loop` are *invisible to the model*: loading a clip does not change
`state` or `loop_len`, and saving does not create a file. The 🔴-1 defect — audio swapped
at press time instead of the boundary — **cannot be expressed** in this harness, because
in the harness `load_loop` does nothing at all. That is also why a one-argument `load_loop`
survived for months: arity was never checked against a model that had a signature.
*Fix:* give the fake a per-loop buffer identity, make `load_loop` validate arity (3 args)
and set it, make `save_loop` write the file, and assert in tests that the buffer identity
changes **at the boundary and not before**.

**(b) Every `SlotSurface`/`SlotRuntime` test runs unquantized.** `test_slot_surface.py:56`
and `test_multigrid_equivalence.py:86` build footswitches with `quantized=False`;
`test_multiclip_workflow.py:59` constructs `FakeSlEngine(quantized=False)`.
`QuantizedSessionTests` (`:340-378`) is the sole exception, it flips the flag by hand
(`:352-355`), and it covers **record only** — there is no quantized test for launch, switch,
scene launch, or cancel. The appliance's actual configuration is quantized. The suite's
default is the configuration Mitch does not play.
*Fix:* make quantized the default in `Session`, and force the free-running case to be the
one that opts out.

**(c) `loop_pos` never reaches the surface, in production or in tests.**
`Session.ring_out_pass` (`test_multiclip_workflow.py:141-155`) feeds positions to `fs`
only. The harness faithfully reproduces the production gap rather than exposing it. Once
`on_loop_pos` exists (§2.4), the harness should drive it, and then a switch test can assert
"`load_loop` is not sent before the wrap."

**(d) The by-assignment ban is stated in one file and violated in the neighbours.**
`test_slot_runtime.py` assigns `rt._tracks[0] = Track(...)` at lines 70, 81, 90, 147, 163,
176, 185, 201, 222, 237, 264; `test_slot_surface.py` at 126, 151, 168, 201, 224, 272, 311,
312. The rule from `test_multiclip_workflow.py:20-24` is right and applies to those files
too. They are not all convertible — some are legitimately unit tests of one function — but
**every test that asserts on a multi-step outcome must be.**
*Fix:* mechanical guard — a test that greps the suite for `_tracks[` outside the allowed
unit-test modules. Cheap, and it makes the rule real.

**(e) Test-only construction hides a production defect.** `debounce_ms=0` everywhere
(`test_slot_surface.py:56`, `test_multigrid_delegates.py:31`) is what conceals §5.3's
scene-launch bug. Harnesses should use the appliance's defaults and override only what the
test is about.

**(f) Nothing tests the invariant pairs.** The two candidates, both ~5 lines:
`_is_active_lane(note) == (plan_cell_press(...).action == ACT_FORWARD)` across all track
states; and "for any plan, exactly one of {runtime sends OSC, footswitch sends OSC} does."
The second would have caught the missing `expect_cleared` on the scene path.

### 6.3 Missing categories

There is no end-to-end test that runs the actual bench event loop, and no test at all for
`sooperlooper-apc-bench.py`'s main loop other than `test_apc_bench.py` (227 lines,
argument/wiring level). Every one of the seven `continue` blocks in the event loop
(`sooperlooper-apc-bench.py:604-700`) repeats the same four poll calls by hand; a missed
one is invisible.

---

## 7. Security & performance

No network exposure beyond localhost OSC, no credentials, no user input parsing.
`os.environ` is used for ~30 tuning knobs, all with defaults, none secret. Nothing here
would keep a security reviewer awake.

Performance, on an appliance where CPU is the documented scarcest resource
(`AGENTS.md`, `DECISIONS.md` 2026-08-18):

- **The 2 s synchronous stall in `_flush_active`** (§5.3) is the significant one.
- `SlotSurface.poll_led_repaint` (`slot_surface.py:601-604`) calls `poll_pending()`, which
  iterates all 15 tracks and dict-copies `self._rt.tracks()` (`slot_runtime.py:70-71`)
  **on every idle tick of the main loop** — which the bench comments describe as running
  "thousands of times a second." That is a dict copy of 15 frozen dataclasses per tick for
  a loop that is almost always empty. Guard it with a cheap "any pending?" flag maintained
  on write.
- `repaint()` calls `_footswitch_leds()` (`slot_surface.py:723-729`) which calls
  `current_led()` on eight footswitches, each doing 1–2 `time.monotonic()` calls and a
  `led_for` lookup — also per tick. The MIDI diff at the end is well done and does keep the
  wire quiet; the CPU above it is not diffed.
- `PacedMidiOut` (`apc_link.py`) is the right answer to the -EPIPE problem and the comment
  documenting the measurement is exemplary.

---

## 8. Developer experience

`AGENTS.md` is one of the better orientation documents I have read, and `docs/CODE-MAP.md`
plus `apc_panel.py` mean a new dev can find things. The audio-safety section at the top of
`AGENTS.md` is correctly placed and correctly blunt.

Where a new dev would cry:

- **There is no document that says how the two layers divide authority.** That rule
  currently exists as four comments in three files (§2.2, §5.2). It should be ~20 lines at
  the top of `slot_surface.py` or in `Documents/DECISIONS.md`, and it should name the
  boundary source explicitly.
- **`MPE_SL_MULTIGRID=0` by default** (`sooperlooper-apc-bench.py:269`) is the right call
  for shipping and the wrong call for the test suite, which has quietly settled into
  testing the off-by-default path more thoroughly than the on path.
- The comment density is a genuine asset but it is now past the point where it substitutes
  for structure. When a function needs eleven lines of comment to explain why it does not
  send an OSC message (`slot_runtime.py:146-153`), the type system should be saying it
  instead — e.g. `_execute_slot_ops` for gesture actions should not have `self._send` in
  scope at all.

---

## The good, the bad, and what smells

**Good.** `_flush_active`'s durability protocol. The `sl_state` / `pending` split in
`loop_model`. Two genuinely pure planner modules. `FakeSlEngine` as a behavioural model
with cited measurements. `Session.deliver()` delivering only changes. `apc_panel.py` as a
single measured source of truth. `PacedMidiOut`. The commit-message-grade comments that
name the date, the symptom, and the player's own words.

**Bad.** Three unreconciled notions of "the bar arrived," of which the one the matrix uses
is not a boundary. `load_loop` issued at press time next to a quantized `mute_off` — the
direct cause of "clips aren't quantized." Three entry paths with different behaviour.
Two implementations of "launch," one of which cannot start a paused loop. A 2 s synchronous
stall on a pad press.

**Smells.** Invariants that exist only as prose in two files that must agree. Dead action
vocabulary with four live branches hanging off it. A class named for hardware that does not
exist. A resource-ownership fix pasted into two files instead of shared into one. A test
suite whose default configuration is not the appliance's, and whose engine model cannot see
the two OSC verbs the feature is built on.

---

## Verdict

The owner's instinct is correct that something structural is wrong, and his stated cause is
not quite it. This is not smelly, repetitive code — the code is unusually careful and
unusually well documented. The failure is that multi-clip was bolted on as a **sibling** of
the single-clip layer with the border between them drawn in comments, so the two halves
each believe they own the boundary, the launch, and the sl_state, and neither of them owns
the one thing that actually defines a musical boundary — the `loop_pos` wrap, which is
delivered to the half that does not need it and withheld from the half that does. That is
why behaviours are "inconsistent at different times": they are consistent per code path,
and there are three paths. Restructure it as one boundary source, one deferred-effect
queue, and one entry path (§2.4) — and change the test defaults to quantized before writing
a line of it, or the restructure will be verified by a suite that cannot see the feature.

## Priority backlog

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
