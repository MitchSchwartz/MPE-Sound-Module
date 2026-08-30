# Grumpy review — track / loop / clip state ownership

**Branch:** `refactor/looper-ownership-2026-08-30` @ `41d8541`
**Date:** 2026-08-30 · fresh-context adversarial reviewer, read-only
**Charter:** [`CHARTER-looper-ownership-2026-08-30.md`](CHARTER-looper-ownership-2026-08-30.md)
**Dimension:** who owns "what state is track N in"

**Canon used (charter §2 ranking):** `DECISIONS.md` § 2026-08-15 *Looper control layer:
engine truth plus intent that expires* (tier 2); `DECISIONS.md` § 2026-08-27 *Multi-clip
integration plan* (tier 2); `specs/multi-clip-integration-plan.md` and
`specs/session-control-plane-spec.md` (tier 3). **Tests were not treated as canon.**

---

## Verdict, first

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

## 1. Findings by severity

### 🔴 P0-1 — `SlotSurface.poll_pending` calls `repaint()` with a positional argument. The bench dies.

`scripts/sooperlooper/slot_surface.py:355`

```python
for track_index in self._rt.poll_grid_wait():
    self.repaint(self._sl_states)          # <-- TypeError
    self.repaint_scenes()
```

`scripts/sooperlooper/slot_surface.py:532`

```python
def repaint(self, *, force: bool = False) -> None:
```

`force` is keyword-only. There is no positional parameter. This is not a style issue —
it raises, and `scripts/sooperlooper-apc-bench.py:583–614` has **no `try` around
`poll_holds()`**, so the exception unwinds through `poll_led_repaint` → `poll_holds` →
`run_bench` → `main` and the controller process exits.

**Reachability.** `_grid_wait` is populated only by `_defer_launch`
(`slot_runtime.py:419–421`), which runs only when the session is sounding and a grid
exists. So: **any track is playing, a grid is established, and you launch a stopped
clip on another track.** That is the ordinary multigrid gesture. The pressed track is
silent, so it will never produce a wrap; the grid bar line is the only thing that can
fire the launch, and firing it kills the bench.

**Reproduced** (`scratchpad/repro_gridwait.py`):

```
deferred? True | grid_wait: {1: 102.0}
CRASH: TypeError SlotSurface.repaint() takes 1 positional argument but 2 were given
engine already told to load/launch? ['/sl/1/load_loop', '/sl/1/hit', '/sl/1/hit']
```

Note the last line. `poll_grid_wait` calls `land_pending` for every due track *before*
returning (`slot_runtime.py:290–297`), so the OSC has gone out and `_tracks` has already
advanced. The audio changes, then the surface dies with the pads frozen mid-blink.
Nothing repaints them again. This is the charter's "a pad the player believes is running
and isn't", with the process gone as a bonus.

**Fix direction:** `self.repaint()`. Then add the missing test — `SlotSurface` must be
exercised through `poll_led_repaint()` after a grid wait, not only `SlotRuntime`.

---

### 🔴 P0-2 — `SlotRuntime.reset()` forgets `_flush`. The next take is lost, and the model says it is safe.

`scripts/sooperlooper/slot_runtime.py:303–308`

```python
def reset(self) -> None:
    """Drop slot bookkeeping after a full track reset."""
    self._tracks = {i: Track() for i in range(self._num_tracks)}
    self._deferred.clear()
    self._grid_wait.clear()
    self._awaiting.clear()
```

Four of the five per-track dicts. `self._flush` (`slot_runtime.py:114`) survives. And
`_ensure_flushed` treats the *presence* of a job as proof a save is running:

`scripts/sooperlooper/slot_runtime.py:598–600`

```python
if loop not in self._flush:
    self._begin_flush(loop, track.active_slot)
return self.poll_flush(loop)
```

So after a reset, the next flush for that loop is never begun — the stale job is polled
instead. If SooperLooper's pre-reset `save_loop` landed its `.part` file (it usually
does; the save was in flight when the reset arrived), `poll_flush` renames **the old
take's bytes over the new slot's filename**, returns `FLUSH_CLEAN`, and the caller
proceeds to reuse the buffer.

**Reproduced** (`scratchpad/repro_flush_reset.py`):

```
press parked? noop | awaiting: (0,) | flush job: True
after reset -> tracks fresh: Track(slots=(None,...), active_slot=None, pending=None)
              _flush STILL HOLDS: True
second press: record | note: record into empty slot — track goes silent at arm
save_loop issued for the NEW take? False   (osc: [('/sl/0/hit', ['undo_all'])] )
slot 0 on disk now contains: b'OLD-TAKE'
model says slot 0 dirty? Slot(file='live_t00_s0.wav', len_s=9.0, sl_state=4, dirty=False)
```

Read the last two lines together. The file on disk holds a *different, older* recording.
The model says slot 0 is 9.0 s and `dirty=False` — **it claims the new take is durable.**
Then `undo_all` destroys the buffer that actually held it. Silent data loss with a model
that actively vouches for the loss.

This is the same failure shape `_begin_flush`'s own comment (`slot_runtime.py:606–615`)
was written to end — *"a save that never landed destroyed the take it was trying to
preserve"* — arriving by a different door: not the unlink, the reset.

`_clear()` (`:543`) and `forget_active_slot()` (`:556`) have the same hole. Both call
`abandon()` (`:219`), which clears `_deferred`, `_grid_wait`, `_awaiting` and
`Track.pending` — and not `_flush`. `_clear` also unlinks the WAV that a live `_flush`
job is about to `os.replace` into existence.

**Fix direction:** `_flush` is durability state, not intent, so `abandon()` should *not*
touch it — but `reset()`, `_clear()` and `forget_active_slot()` must cancel the job
explicitly (drop the `.part`, drop the entry) because the slot they were saving no
longer exists. Better: make `_ensure_flushed` validate that a live job's `slot` matches
`track.active_slot` and restart it if not, so presence alone is never taken as proof.

---

### 🔴 P0-3 — Banking while a pad is held deletes a clip on the track that moved under your finger.

`TrackGesture` has a fix for exactly this. `scripts/sooperlooper/track_gesture.py:202–213`:

```python
def release_pad(self) -> None:
    """Abandon an in-flight pad gesture without firing it.

    Banking while a pad is held would otherwise strand `_pad_down` on the
    track that just left the screen: poll_hold() runs for every track,
    visible or not, so ~hold_s later it would fire the long-press and clear
    a loop the player never let go of.
```

`apply_view` calls it for every gesture (`track_gesture.py:899`). **`SlotSurface.set_view`
has no equivalent** (`slot_surface.py:517–522`):

```python
def set_view(self, view: GridView) -> None:
    self._view = view
    self._painted = None
    self._sync_gesture_notes()
    self.repaint()
    self.repaint_scenes(force=True)
```

`_pad_down_note` and `_pad_down_at` survive the bank change, and `poll_hold`
(`slot_surface.py:293–321`) re-resolves that note against the **new** view:

```python
note = self._pad_down_note
self._hold_fired = True
...
self.press(note, hold=True)          # -> plan_cell_press(hold=True) -> ACT_CLEAR
```

**Reproduced** (`scratchpad/repro_twofinger.py` sibling; bank-nudge case):

```
holding note 25 -> cell (1, 3)
same pad now addresses cell (2, 3)
surface still thinks a pad is down: 25
  log: track 3 slot 4: clear this slot
clip file for track 2 slot 3 still on disk: False
track 2 model: Track(slots=(None, ...), active_slot=None, pending=None)
```

Hold an empty pad on track 1, nudge the bank one column (Shift+Right), keep holding: the
recorded clip on **track 2** is unlinked from disk and erased from the model. The player
never touched that track. Unrecoverable — `_clear` deletes the WAV.

**Fix direction:** `SlotSurface.set_view` must release its pad state, exactly as
`apply_view` does for gestures. Better still: this is one of three reasons the surface
should not own a second hold state machine at all — see P1-2.

---

### 🟡 P1-1 — `Track.pending` never expires, and while it is stuck the whole column stops showing gesture colour.

`DECISIONS.md` § 2026-08-15 is unambiguous:

> **Decision — keep the intent, stop calling it truth.** `sl_state` is authoritative
> always; `pending` is what we asked for and have not seen confirmed, **and it expires.**

and names the symptom the expiry exists to prevent: *"a queued launch blinking green
forever."*

`TrackGesture._pending` honours this (`track_gesture.py:236–246`, `PENDING_TIMEOUT_S`).
`slot_matrix.Track.pending` does not. Its only exits are:

- `resolve_at_boundary` via `land_pending` — needs the engine to reach `ACTIVE_PLAY`
  (`slot_surface.py:501–503`) or a wrap (`slot_surface.py:469`);
- `abandon()` via `expire_deferred` — but only for launches that were **deferred**
  (`slot_runtime.py:436–446`).

A launch that fired immediately (session silent, `slot_runtime.py:397–400`) sets
`Track.pending` with **no deferred entry and therefore no expiry path at all**. If the
engine never reaches `ACTIVE_PLAY` — `load_loop` rejected a truncated WAV, the loop was
paused, SL was busy — the pending is permanent.

**Reproduced** (`scratchpad/repro_pending_forever.py`):

```
launch OSC sent: ['/sl/0/load_loop', '/sl/0/hit', '/sl/0/hit']
pending after press: Pending(kind='launch', to_slot=2, from_slot=None)
pending after 5 MINUTES (gesture timeout is 6.0s): Pending(kind='launch', to_slot=2, from_slot=None)
```

The second half is worse. `slot_leds.matrix_messages` gives the active cell to the
gesture **only when there is no pending**:

`scripts/sooperlooper/slot_leds.py:109–112`

```python
if row == track.active_slot and track.pending is None:
    desired[note] = (gesture_leds or {}).get(track_index, LED_OFF)
else:
    desired[note] = static_cell_led(track, row)
```

So a stuck pending locks the gesture out of its own column:

```
gesture current_led for the active cell: 3 (RED = 3)
pad actually painted for active slot 5 : 0 (YELLOW = 5, OFF = 0)
pad painted for the stuck launch slot 2: 2 (GREEN_BLINK = 2)
```

**The player is recording and the pad is dark**, while a launch that never happened
blinks green next to it. Record-red, the ring-out blink, and the hold warning are all
suppressed on that column until something resolves a pending that has no resolver.

**Fix direction:** give `Pending` a `since` timestamp and expire it on the same clock and
with the same meaning as `_expire_pending` — drop the intent, defer to the engine, log
once. And drop the `pending is None` gate in `matrix_messages`: the pending blink belongs
to the cells the pending *names*, not to the active cell.

---

### 🟡 P1-2 — Two hold state machines with different cardinality. Two fingers corrupt the surface.

`TrackGesture` keeps `_pad_down` **per track** (`track_gesture.py:170`). `SlotSurface`
keeps **one** `_pad_down_note` for all 64 pads (`slot_surface.py:86–92`), and `note_down`
overwrites it without releasing the previous one:

`scripts/sooperlooper/slot_surface.py:116–124`

```python
def note_down(self, note: int) -> bool:
    if not self.handles(note):
        return False
    self._pad_down_note = note
    self._pad_down_at = self._now()
    self._hold_fired = False
```

Meanwhile `note_up` unconditionally forwards the release to the *column's* gesture
(`slot_surface.py:126–140`), whose `_pad_down` may belong to a different cell.

**Reproduced** (`scratchpad/repro_twofinger.py`) — track 0 playing slot 0, second clip in
slot 3, press slot 0, press slot 3, release slot 3:

```
gesture _pad_down after first down: True
surface _pad_down_note is now b: True
OSC after lifting finger 2: [('/sl/0/hit', ['mute_on'])]
note_up(a) accepted? False
track pending: Pending(kind='switch', to_slot=3, from_slot=0)
```

Releasing the **second** pad fired the **first** pad's `mute_on`. The outgoing clip is now
muted *and* a switch is queued — and `_execute_slot_ops`'s `ACT_CANCEL` branch
(`slot_runtime.py:346–351`) documents that a switch deliberately leaves the outgoing clip
sounding. The instrument goes silent for up to a bar for reasons nothing in the model
records. The real release of pad A is then rejected outright.

Two fingers on a clip matrix is not an edge case; it is how Session View is played.

**Fix direction:** the surface should not track hold at all. It already delegates hold to
the gesture in the active lane (`slot_surface.py:299–312`) precisely because *"a second
hold implementation fired at a different moment and painted a different blink."* Finish
that job: per-cell pad state keyed by note, or move hold entirely into the per-track owner.

---

### 🟡 P1-3 — `_stop_queued` survives Stop All and cuts a later, unrelated take to one cycle.

Two reset paths, two different completeness levels.

`reset_all_loops` (`track_gesture.py:934`) calls `fs.expect_cleared()`, which clears
`_stop_queued` (`track_gesture.py:656`). `stop_all_loops` does not
(`track_gesture.py:969–976`):

```python
for fs in gestures:
    fs.awaiting_quantize = False
    if fs.sl_state not in (SL_STATE_OFF, SL_STATE_OFF_MUTED):
        fs._expect(STATE_STOPPED)
    fs._sync_led()
```

`_stop_queued` is consumed on a state transition that may arrive arbitrarily later
(`track_gesture.py:282–287`):

```python
if sl_state == SL_STATE_RECORDING and self._stop_queued:
    self._stop_queued = False
    self._hit("record")
    self._begin_quantize_wait()
```

**Reproduced** (`scratchpad/repro_stopqueued.py`) — double-tap during a count-in, Stop
All, then a brand-new take later:

```
stop queued after double tap: True
after Stop All -> _stop_queued: True | pending: stopped | state: stopped
OSC sent while the new take starts: [('/sl/0/hit', 'record'), ('/sl/0/hit', 'record')]
awaiting_quantize (pad now deaf): True
```

Two `record` hits: the arm, then a stop the player issued *before the panic button*. The
new take is cut to exactly one cycle, and `awaiting_quantize` goes True so the pad is
deaf for up to `QUANTIZE_WAIT_TIMEOUT_S` = 6 s. Nothing in the log says why.

**Fix direction:** `stop_all_loops` should go through the same single "forget this
track's unfinished intent" entry point as `reset_all_loops`. There should be exactly one
such method on `TrackGesture` and it should clear every intent field, the way
`SlotRuntime.abandon()` was written to (`slot_runtime.py:219–235`) after the identical
lesson.

---

### 🟡 P1-4 — `_maybe_mark_recorded` is inference the integration plan says to delete.

`specs/multi-clip-integration-plan.md`, § *Engine reconciliation*:

> **Delete** the inference-only path that marks recorded when `not occupied(active)` and
> `sl_state ∉ ACTIVE_RECORD` without an explicit close gesture — it was a band-aid.

and § *Stop-doing list*, rule 3: *"**No** new occupancy inference without a corresponding
engine event or explicit close."*

`scripts/sooperlooper/slot_surface.py:404–432` is that path, verbatim:

```python
if sl_state not in ACTIVE_PLAY:
    return
row = self._rt.track(track)
active = row.active_slot
loop_len = self._loop_lens.get(track, 0.0)
if (
    active is not None
    and not row.occupied(active)
    and loop_len >= MIN_TAKE_LEN_S
):
    self._rt.mark_recorded(track, active, len_s=loop_len, sl_state=sl_state)
```

There is no check that a record gesture ever occurred. Any `ACTIVE_PLAY` report against
an unoccupied bound slot registers a take. The two inputs come from two different caches
(`_rt.track()` and `self._loop_lens`) neither of which the gesture that actually did the
recording can see.

The window is real, not theoretical. `_prepare_record` advances `active_slot`
**synchronously at press** while the engine keeps sounding the *old* slot to the wrap
(`slot_runtime.py:479–493, 505–514` — the comment explains why, correctly). During that
window `_sl_states[track]` is still `PLAYING`, `active_slot` is the new empty slot, and
`on_loop_len` will happily register a take that has not been recorded
(`slot_surface.py:395–402`).

The consequences compound with the next finding.

**Fix direction:** as the plan says. `mark_recorded` should be driven by the gesture's
own close — `TrackGesture` already knows exactly when a take closes (it is the thing that
starts `TailPhase`, `track_gesture.py:289–301`). Add a `on_take_closed(loop, len_s)`
callback alongside `_on_tail_change` and delete the inference.

---

### 🟡 P1-5 — `_loop_lens` and `TrackGesture.loop_len` disagree by construction, so takes are registered with the previous take's length.

`scripts/sooperlooper/slot_surface.py:395–397`

```python
def on_loop_len(self, track: int, loop_len: float) -> None:
    if loop_len > 0:
        self._loop_lens[track] = float(loop_len)
```

`scripts/sooperlooper/track_gesture.py:337–339`

```python
def sync_loop_len(self, loop_len: float) -> None:
    self.loop_len = float(loop_len)
```

Same OSC message, delivered to both by `sl_bench_listener.on_update` — and one of them
drops zeros. After a clear, the engine reports `loop_len = 0`; the gesture believes it,
the surface keeps the old value.

**Reproduced** (`scratchpad`, `loop_len` divergence case) — 4 s take, clear, 11 s take:

```
gesture.loop_len = 0.0 | surface._loop_lens = {0: 4.0}
...
slot 0 re-registered as: Slot(file='live_t00_s0.wav', len_s=4.0, sl_state=5, dirty=True)  <-- len_s is take 1's
after the real length arrives: Slot(..., len_s=4.0, ...)
```

The 11-second take is recorded in the model as 4.0 s, permanently — `_maybe_mark_recorded`
refuses to correct it once `occupied(active)` is true, and logs the refusal as if the
*binding* were the problem. `len_s` is what `looper_songs` persists to the manifest
(`looper_songs.py:332`) and what the touch browser shows.

The same run shows a second consequence: the ring-out cap for take 2 was chosen as
`"fallback, no grid and no loop length"` (2.0 s) from the gesture's correct `0.0`, while
the surface simultaneously believed 4.0 s. **Two caches of one number, driving two
different behaviours in the same 50 ms.**

**Fix direction:** delete `_loop_lens`. `TrackGesture.loop_len` is already the gesture's
own, already correct, and already the one the ring-out trusts.

---

### 🟡 P1-6 — Engine callbacks run on the OSC server thread and mutate the same stores as the main loop. No lock anywhere.

`sl_osc_session.py:98–101` runs `serve_forever` on a daemon thread. `_on_bench_state`
(`:117–122`) calls `SlBenchStateListener.on_update`, which calls `fs.sync_from_sl(...)`
**and** `self._surface.on_state(...)` (`sl_bench_listener.py:57–61`). `on_state` writes
`_sl_states`, calls `_maybe_mark_recorded` (writes `_tracks`), `_maybe_resolve` (can call
`expire_deferred` → `land_pending` → OSC + `_tracks` + `_deferred` + `_grid_wait`),
`_sync_gesture_notes`, `repaint`, `repaint_scenes`.

The main loop is doing the same things from `poll_holds()`
(`sooperlooper-apc-bench.py:520–532`). `grep -n "Lock\|threading" ` over
`slot_surface.py`, `slot_runtime.py`, `track_gesture.py`, `apc_link.py` and the bench
returns exactly one hit, and it is a comment asserting the opposite:

`scripts/sooperlooper/apc_link.py:55` — *"this runs on the same thread that handles pad
presses"*

Two concrete consequences:

1. `poll_grid_wait` iterates the live dict (`slot_runtime.py:290`) while the OSC thread
   can `pop` from it inside `abandon()` (`:231`) — `RuntimeError: dictionary changed size
   during iteration`, which in the bench means process exit (no handler, see P0-1).
2. `repaint()` is a read-modify-write on `self._painted` (`slot_surface.py:539–549`).
   Two interleaved repaints can leave `_painted` describing a surface the wire never
   received. Because the paint is a **diff**, the wrong colour is then never re-sent —
   it persists until a bank change or a re-enumeration. Persistent lying pad, no
   symptom in any log.

**Fix direction:** state the threading model in one place and enforce it. The cheapest
correct answer here, given CPU is the scarce resource (`DECISIONS.md` § 2026-08-18), is
for `SlBenchStateListener.on_update` to enqueue events onto a `deque` and for the main
loop to drain it — then *all* surface/runtime mutation and all MIDI writes happen on one
thread, by construction, and the "who may write it" question has a structural answer
instead of a hopeful one.

---

### 🟢 P2-1 — `PENDING_TIMEOUT_S` has no ticker. It expires lazily, on events that are missing precisely when it is stuck.

`_expire_pending` has exactly two call sites: `sync_from_sl` (`track_gesture.py:271`) and
`_gesture` (`:711`) — an engine message, or a pad press.

`register_auto_update` **delivers on change**. That is canon:
`specs/session-control-plane-spec.md:811–812` — *"`register_auto_update` delivering only
on change is a permanent property to design around (D6, criterion 4)."* So when an intent
is stuck, the engine is by definition silent, and the timeout does not run.

**Reproduced** — pending aged ten minutes, 500 idle polls of `poll_track_gestures`:

```
after 500 polls and 6.0s timeout, pending is STILL: stopped | state: stopped
LED sequence the surface will render: (6,) (YELLOW_BLINK = 6)
```

Dispatch is safe (`_gesture` expires first), so this is an LED/feedback defect, not a
wrong-command defect. But it defeats the stated purpose: *"exactly the 'did my press
register?' doubt the blink exists to remove"* (`track_gesture.py:66–73`).

Ideal place to fix it: `poll_track_gestures` already iterates every gesture every idle
pass, and under multigrid the `poll_led()` it calls is a **total no-op** — every write
inside it goes through `_set_led`, which returns immediately when `self._multigrid`
(`track_gesture.py:529–531`). The docstring at `:84–90` claims it "advance[s] blink
phase"; it does not — the phase is computed from `time.monotonic()` inside
`current_led()`. Replace that dead call with `_expire_pending()`.

### 🟢 P2-2 — `sync_in_peak` is defined twice; the first one and two fields are dead.

`track_gesture.py:248–250` is shadowed by `track_gesture.py:427–433`. `self._in_peak` and
`self._in_peak_seen` (`:181–182`) are therefore never written and never read. A reader
looking for "does the bench track input level" finds a plausible-looking accessor that
Python discarded at class-creation time.

### 🟢 P2-3 — `matrix_messages(sl_states=…)` is a dead parameter, and `Slot.sl_state` is a dead field.

`slot_leds.matrix_messages` takes `sl_states` at `:73` and never references it (verified
by AST). Every call site still threads it in, including `slot_surface.py:542`. It reads
as a second source of truth that is still consulted. It is not.

`slot_matrix.Slot.sl_state` (`:67`) is written by `mark_recorded` (`slot_runtime.py:321`)
and read nowhere. The trap: `looper_songs.SlotEntry.sl_state` — a *different* dataclass
with the same field name — **is** read, at `looper_songs.py:678–682`, to decide whether a
clip auto-plays on song load. A future session wiring the runtime's `Slot` into the
manifest would persist "was OVERDUBBING when the ring-out registered it" as "play this on
load."

### 🟢 P2-4 — `expect_cleared()`'s docstring is false for one of its two callers.

`track_gesture.py:640–659`: *"Sends no OSC: the caller has already cleared the engine."*
`_hand_to_gesture` calls it for every `ACT_RECORD` (`slot_surface.py:251–255`), including
the branch where `_prepare_record` **deliberately does not** clear the engine because
`record` over a playing loop lands the stop on the boundary itself
(`slot_runtime.py:479–493`). The behaviour is right; the contract statement is now wrong,
and this file's contract statements are how the next session decides what is safe.

### 🟢 P2-5 — `_waiting_for_quantize` uses bare `print`, not the module's `log`.

`track_gesture.py:611–617`. The module defines `log()` at `:79–81` specifically because
*"Untimed lines made a 2 s quantize wait invisible."* The one message about a quantize
wait timing out is the one that skips it.

---

## 2. The questions, answered

### 2.1 Every place that stores or derives "what state is track N in"

Nine, plus the engine.

| # | Store | Kind | Written by | Can disagree with engine? | Reconciled |
|---|---|---|---|---|---|
| 1 | SooperLooper | **truth** | the engine | — | — |
| 2 | `SlOscSession.last["N:state"]` (`sl_osc_session.py:114`) | cache | OSC thread | yes, on change-only silence | never; read by `sl_hud_monitor.py:129` |
| 3 | `TrackGesture.sl_state` (`track_gesture.py:174`) | cache | `sync_from_sl` only | yes, same reason | on next auto-update |
| 4 | `TrackGesture._pending` (`:165`) | **intent** | `_expect`, `_gesture`, `expect_cleared` | by design | `_expire_pending`, lazily (P2-1) |
| 5 | `TrackGesture.state` (`:216`) | derived | — | — | pure fn of 3+4 |
| 6 | `SlotSurface._sl_states` (`slot_surface.py:86`) | **duplicate cache** | `on_state` only | yes; cleared by `reset()` while 3 is not | never |
| 7 | `SlotRuntime._tracks[N]` (`slot_runtime.py:106`) | **truth for slots** | six writers, see 2.3 | n/a — engine has no concept | n/a |
| 8 | `Track.pending` (`slot_matrix.py:80`) | **intent** | `apply_pending` | by design | **never expires** (P1-1) |
| 9 | `Slot.sl_state` (`slot_matrix.py:67`) | frozen snapshot | `mark_recorded` | permanently | dead (P2-3) |
| 10 | `SlotEntry.sl_state` (`looper_songs.py:171`) | **persisted** | `save_song_v2` from a direct engine read | across restarts | on next save |

Also per-track lifecycle flags that are state in everything but name:
`awaiting_quantize` + `_wait_since` (`:175–176`), `_stop_queued` (`:177`), `_tail`
(`:150`), `_led_transition` (`:178`), `_phase_reanchor_at` (`:159`),
`_deferred_grid_clock` (`:183`), `SlotSurface._loop_lens` (`:87`),
`SlotRuntime._deferred` / `_grid_wait` / `_awaiting` / `_flush` (`:112–121`).

`SlotSurface.track_state()` (`slot_surface.py:207–220`) claims to be *"the ONE answer"*
and its docstring is the right instinct — but it unified four of six call sites. Still
reading the duplicate cache: `on_loop_len` (`:398`, and that is the take-registration
path) and `repaint_scenes` (`:557`, the scene-button colour). So the scene button's
**colour** and the scene press's **meaning** are computed from different sources.

**They currently agree only because `sl_bench_listener.on_update` writes both in the same
statement.** Nothing enforces that. `SlotSurface.reset()` breaks it deliberately
(`:507–508` clears the surface caches; nothing clears the gestures').

### 2.2 Intent that expires — how many mechanisms, do they agree?

Eleven timers. Four of them are about intent:

| Mechanism | Constant | On expiry it… |
|---|---|---|
| `TrackGesture._expire_pending` | `PENDING_TIMEOUT_S` 6 s | **drops** the intent, logs, defers to the engine |
| `TrackGesture._waiting_for_quantize` | `QUANTIZE_WAIT_TIMEOUT_S` 6 s | **drops** the wait, releases the pad |
| `SlotRuntime.expire_deferred` | `DEFERRED_LAUNCH_GRACE_S` 5 s | **performs the action anyway**, unquantized |
| `SlotRuntime.poll_flush` | `SAVE_TIMEOUT_S` 2 s | **refuses** the press |

Plus `TailPhase.cap_s` / `SILENT_GRACE_S` / `TAIL_HOLD_S`, `GRID_ANCHOR_FALLBACK_CYCLES`,
two `hold_s`, `REREGISTER_S`, and the grid boundary in `_grid_wait`. And two intents with
**no** expiry: `_stop_queued` and `Track.pending`.

**They do not agree on what "expired" means.** In the gesture layer expiry means *we were
wrong, believe the engine*. In the runtime layer expiry means *do it anyway*. Same word,
opposite semantics, one subsystem, and the runtime's version is the one that can start
audio five seconds after the player stopped — which is why `abandon()` had to be invented
(`slot_runtime.py:219–228` documents that exact incident).

Who clears the surface afterwards is likewise split: `_expire_pending` relies on a
subsequent `_sync_led`; `expire_deferred` relies on `_maybe_resolve`'s caller repainting;
`poll_grid_wait`'s repaint is the line that crashes (P0-1).

**Concrete sequence where an intent expires in one store and survives in another** —
P1-3 above is the cleanest: after Stop All, `TrackGesture._pending` is reset to
`STATE_STOPPED` and will expire in 6 s, `awaiting_quantize` is cleared, `Track.pending`
is cleared by `abandon_all` — and `_stop_queued` survives all three and fires minutes
later against a different take.

### 2.3 The two-layer problem — where is the seam, and is it clean?

The seam is one field: **`Track.active_slot`**. Everything the two layers need to agree
about is expressed through it.

- `TrackGesture` owns loop *N*'s engine lifecycle and knows nothing about slots.
- `SlotRuntime` owns which slot loop *N*'s single buffer currently represents.
- `SlotSurface` translates: `_is_active_lane` (`:272–291`) routes a press to the gesture
  iff `active_slot == slot`; `matrix_messages` routes the *colour* the same way;
  `_sync_gesture_notes` (`:458–467`) moves the gesture's pad note to the active cell.

**That seam is a good idea and the module comments defending it are right.** The
`test_multigrid_equivalence.py` framing — assert equivalence to the validated path rather
than enumerate behaviours — is the best single decision in this subsystem.

**But the seam is not clean, because `active_slot` has six writers across two modules**
and no invariant:

| Writer | file:line |
|---|---|
| `_execute_slot_ops`, `ACT_FORWARD` bind | `slot_runtime.py:340–343` |
| `_prepare_record` | `slot_runtime.py:514` |
| `mark_recorded` | `slot_runtime.py:315–326` |
| `resolve_at_boundary` via `land_pending` | `slot_runtime.py:198` |
| `_clear` | `slot_runtime.py:550–553` |
| `forget_active_slot` | `slot_runtime.py:571` |

Who wins when they disagree: **whoever ran last.** There is no arbiter. Two of the six
(`_prepare_record`, `mark_recorded`) run at moments when the engine is provably sounding
a *different* slot, and `_maybe_mark_recorded` reads the field in exactly that window
(P1-4).

The named methods:

- `_hand_to_gesture` (`slot_surface.py:222–271`) — **good.** Collapsing three copies into
  one was correct, and `tap` really is the only real difference.
- `land_pending` (`slot_runtime.py:176–198`) — the rename is honest and the failure
  branch (drop the pending rather than advance onto a slot the engine never loaded) is
  right.
- `mark_recorded` / `_maybe_mark_recorded` — the ownership defect (P1-4, P1-5).
- `_maybe_resolve` (`slot_surface.py:483–503`) — correct in its reasoning about
  `has_deferred`, but it is the *only* thing that can expire a matrix pending and it
  cannot expire a non-deferred one at all (P1-1).
- `forget_active_slot` (`slot_runtime.py:556–576`) — **the model for the rest.** Its
  docstring states the split exactly: *"the gesture owns the engine and the LED, the
  matrix owns the disk and the binding."* That sentence is the contract the whole
  subsystem should be written to. It is currently true of one method.

### 2.4 Deferred side effects — who holds the debt in flight?

Four kinds of deferred work, four different owners, no shared discipline:

| Debt | Held in | Owner while in flight | Survives owner reset? |
|---|---|---|---|
| deferred grid clock | `TrackGesture._deferred_grid_clock` | the gesture | yes — `expect_cleared` does not clear it |
| phase re-anchor | `TrackGesture._phase_reanchor_at` | the gesture | yes — same |
| deferred launch | `SlotRuntime._deferred` + `_grid_wait` | the runtime | no — `abandon`/`reset` clear both ✅ |
| in-flight save | `SlotRuntime._flush` | the runtime | **yes — and that is P0-2** |

`_flush_deferred_grid_side_effects` (`track_gesture.py:252–263`) is dead code — nothing
calls it. `grep` finds one definition and zero call sites. So `_deferred_grid_clock` is
set nowhere and applied nowhere; the field and its flusher are both vestigial. Worth
deleting or wiring, but say which.

`has_deferred` / `poll_grid_wait` are the runtime's honest attempt at this, and the
`abandon()` docstring is the best incident write-up in the module. The lesson simply was
not carried across to `_flush`.

### 2.5 Reset paths — do all stores reset together?

No. This is the table the charter asked for:

| Store | `reset_all_loops` | `stop_all_loops` | `SlotSurface.reset` | `on_stop_all` | `abandon` |
|---|---|---|---|---|---|
| `TrackGesture._pending` | ✅ `expect_cleared` | ⚠️ set to `stopped` | ✗ | ✗ | ✗ |
| `awaiting_quantize` | ✅ | ✅ | ✗ | ✗ | ✗ |
| `_stop_queued` | ✅ | **✗ (P1-3)** | ✗ | ✗ | ✗ |
| `_led_transition` | ✅ | ✗ | ✗ | ✗ | ✗ |
| `_tail` | ✗ (self-heals via `sync_from_sl`) | ✗ | ✗ | ✗ | ✗ |
| `_pad_down` / `_hold_fired` | ✗ | ✗ | ✗ | ✗ | ✗ |
| `_phase_reanchor_at` | ✗ | ✗ | ✗ | ✗ | ✗ |
| `_tracks` | — | — | ✅ | ✗ | pending only |
| `_deferred` / `_grid_wait` | — | — | ✅ | ✅ | ✅ |
| `_awaiting` | — | — | ✅ | ✅ | ✅ |
| `_flush` | — | — | **✗ (P0-2)** | ✗ | ✗ |
| `_sl_states` / `_loop_lens` | — | — | ✅ | ✗ | — |
| `SlotSurface._pad_down_note` | — | — | ✅ | ✗ | — |

Two structural problems visible in that table:

1. **Every reset is two half-resets in two modules, sequenced by the bench.** `reset` =
   `reset_all_loops(...)` then `slot_surface.reset()`; stop-all = `stop_all_loops(...)`
   then `slot_surface.on_stop_all()` (`sooperlooper-apc-bench.py:559–581`). Any new call
   site that forgets the second half gets a half-reset with no error. That is precisely
   how `on_stop_all` came to exist — its docstring records the incident.
2. **The two paths clear different subsets of the same object.** `reset_all_loops` clears
   `_stop_queued` and `stop_all_loops` does not, for no reason either function states.

`expect_cleared` is very nearly the right primitive. It should be renamed to what it is —
*forget this track's unfinished intent* — clear **every** intent field, and be the only
way any reset path touches a gesture.

### 2.6 Banking / view changes — does state survive and do bindings die?

**State survives: ✅.** `_tracks`, `sl_state`, `_pending`, `_sl_states` are all untouched
by `set_view`. `build_track_gestures`' docstring (`track_gesture.py:835–842`) states the
rule correctly and the code honours it: a gesture exists for every track, banked or not,
and only `note` goes away.

**Bindings die: partially.** ✅ `apply_view` clears the row, rebinds notes, and calls
`release_pad()`. ✅ `set_view` sets `_painted = None`, forcing a full repaint — right,
and the comment explaining why is right. ✗ `SlotSurface._pad_down_note` / `_pad_down_at`
/ `_hold_fired` survive — **P0-3**.

Two smaller binding problems:

- `TrackGesture._note` has three writers under multigrid — `apply_view` (`:900`, row-0
  note), `_sync_gesture_notes` (`:465`, active-cell note), `_hand_to_gesture` (`:255`) —
  and **zero readers**, because every path to `_set_led` returns early when
  `_multigrid`. Harmless today, and a trap: the correct value depends on `apply_view`
  running before `slot_surface.set_view`, an ordering only the bench knows about.
- `reopen_apc` (`sooperlooper-apc-bench.py:505–510`) calls `apply_view` then
  `slot_surface.repaint(force=True)` but **not** `set_view`/`_sync_gesture_notes`, so
  after a re-enumeration those notes are left at the row-0 values. Inert now; wrong the
  moment anything reads `_note` under multigrid.

---

## 3. Where two layers are genuinely right — and the contract they need

**Two layers is the correct answer here.** SooperLooper has one buffer per loop and no
concept of a slot; something has to own "which of eight clips this buffer currently is,
and what is on disk for the other seven," and that thing cannot be the engine-facing
gesture. `slot_matrix.py`'s module docstring already argues this correctly.

What is missing is the contract. It should be stated once, in code, and enforced by test:

```
TrackGesture owns, for loop N:
    the engine        — every /sl/N/* command, without exception
    the lifecycle     — record / close / ring-out / mute / launch / cancel / hold
    the colour        — current_led() is the only source for the active cell
    intent about the engine, and its expiry

SlotRuntime owns, for track N:
    the disk          — clip files, save/flush durability
    the binding       — active_slot: which slot this buffer currently is
    occupancy         — which slots hold audio
    intent about the binding, and its expiry

The seam is `active_slot`, and it is written by ONE method
(`bind_active_slot(track, slot | None, reason)`), never by six.

Neither layer reads the other's caches. `sl_state` and `loop_len` are asked for
by accessor, never mirrored.
```

Three invariants that a test can fail on, which is the charter's third question:

1. `SlotSurface` holds no `dict` keyed by track that duplicates a `TrackGesture` field.
   Enforceable by an import-time assertion or an AST test, the way the note registry is
   to be enforced.
2. Every intent field on either layer has an expiry, and expiry always means *drop and
   defer*, never *perform*. A table-driven test over the intent fields.
3. Every reset path clears every intent field. One test that constructs a gesture +
   runtime with all intents set, runs each reset entry point, and asserts every intent
   field is at its initial value. That single test catches P0-2, P1-3, and the
   `_phase_reanchor_at` / `_tail` rows above.

### Migration, in charter-stage order (each one commit, suite green, revertible alone)

| Step | Work | Closes |
|---|---|---|
| 0 | `self.repaint()` at `slot_surface.py:355` + a `SlotSurface`-level grid-wait test | P0-1 |
| 1 | Cancel `_flush` in `reset`/`_clear`/`forget_active_slot`; make `_ensure_flushed` validate the job's slot | P0-2 |
| 2 | `SlotSurface.set_view` releases pad state; then delete the surface's hold machine in favour of per-cell state | P0-3, P1-2 |
| 3 | One `forget_intent()` on `TrackGesture`, used by both reset paths; add `_stop_queued`, `_tail`, `_phase_reanchor_at`, `_pad_down` | P1-3 |
| 4 | Delete `_loop_lens` and `_sl_states`; `track_state`/`loop_len` by accessor everywhere; drop the dead `sl_states` param from `matrix_messages` | P1-5, P2-3 |
| 5 | `Pending.since` + expiry with the gesture's semantics; drop the `pending is None` gate in `matrix_messages` | P1-1 |
| 6 | `TrackGesture.on_take_closed` callback; delete `_maybe_mark_recorded`'s inference | P1-4 |
| 7 | Event queue between the OSC thread and the main loop; state the threading model in one docstring | P1-6 |
| 8 | `bind_active_slot` as the single writer; then the `TrackColumn` the integration plan specifies | the seam |

Steps 0–3 are the ones that stop data loss and process death; they are independent of
each other and of hardware. Step 8 is the plan's target architecture and should not be
started before 0–7, or it will carry the same duplicates into a new file.

---

## 4. What is good here, plainly

- `loop_model.py` is the standard the charter asks for. Pure, total, and every branch
  carries the incident that produced it.
- `slot_matrix.py`'s refusal to re-decide the active lane, and the comment explaining why
  (`:180–205`), is the single best ownership decision in the subsystem.
- `test_multigrid_equivalence.py`'s framing — equivalence to the validated path rather
  than enumerated behaviours, with the seam stated honestly in the docstring — is a
  better idea than most of what I review.
- `SlotRuntime.abandon()` and `_begin_flush`'s temp-file-then-rename are both correct
  post-incident designs, written up properly.
- `forget_active_slot`'s docstring states the two-layer contract in one sentence. Promote
  it from a comment to the architecture.

The problem is not that this code was written carelessly. It is that the careful parts
were each written to close one incident, inside one owner, and nobody has since been made
responsible for the space between them. Every defect above is in that space.

---

## State ownership map

| State item | Current stores | Authoritative source | Reconciliation trigger | Recommended single owner |
|---|---|---|---|---|
| Engine state of loop N | `SooperLooper`; `SlOscSession.last`; `TrackGesture.sl_state`; `SlotSurface._sl_states`; `Slot.sl_state`; `SlotEntry.sl_state` | SooperLooper | `sync_from_sl` / `on_state` on auto-update (**change-only**) | `TrackGesture.sl_state`; all others read it by accessor. Delete `_sl_states` and `Slot.sl_state` |
| Bench view of loop N | `TrackGesture.state` (derived), `_pending` | derived from `sl_state` + `_pending` | `_expire_pending` — lazily; **needs a ticker** | `TrackGesture` ✅ (already correct; add the tick) |
| Engine intent (record/mute/launch) | `TrackGesture._pending`, `_stop_queued`, `awaiting_quantize` | none — it is intent | `PENDING_TIMEOUT_S`, `QUANTIZE_WAIT_TIMEOUT_S`, **`_stop_queued` never** | `TrackGesture`, via one `forget_intent()`; give `_stop_queued` an expiry |
| Loop length of N | `TrackGesture.loop_len`; `SlotSurface._loop_lens`; `Slot.len_s`; `SlotEntry.len_s`; `SlOscSession.last` | SooperLooper | `sync_loop_len` / `on_loop_len` — **and they disagree on zero** | `TrackGesture.loop_len`. Delete `_loop_lens` |
| Which slot the buffer is (`active_slot`) | `Track.active_slot` — **six writers** | the bench (engine has no concept) | none — last writer wins | `SlotRuntime`, through one `bind_active_slot(track, slot, reason)` |
| Slot occupancy | `Track.slots`; the WAV on disk; the manifest | the WAV on disk | `mark_recorded` (**inference**), `_clear`, `forget_active_slot`, `poll_flush` | `SlotRuntime`, driven by an explicit `on_take_closed` from the gesture |
| Take durability | `Slot.dirty`; `SlotRuntime._flush`; the `.part` file | the file on disk | `poll_flush` — **and `_flush` survives every reset** | `SlotRuntime`; `_flush` cancelled by every reset path |
| Binding intent (launch/switch) | `Track.pending`; `_deferred`; `_grid_wait` | none — it is intent | `land_pending`, `on_wrap`, `expire_deferred`, `poll_grid_wait`; **no expiry for a non-deferred pending** | `SlotRuntime`; `Pending.since` + drop-on-expiry |
| Ring-out phase | `TrackGesture._tail` | `sl_state == OVERDUBBING` | `TailPhase.tick` / peak / wrap; **not cleared by any reset** | `TrackGesture` ✅ (add to `forget_intent`) |
| Pad-held state | `TrackGesture._pad_down` (per track); `SlotSurface._pad_down_note` (**one for 64 pads**) | the MIDI stream | `poll_hold` in both; surface survives bank change | `TrackGesture` per track; surface keeps per-note routing only |
| Painted LED cache | `SlotSurface._painted`, `_scene_painted`; `TrackGesture._led_last` | the physical APC | full repaint on bank change / re-enumerate; **RMW across two threads** | one compositor, one thread (charter stage 2) |
| Grid / tempo / phase | `GridState`; `_deferred_grid_clock`; `_phase_reanchor_at`; `_grid_wait` | `GridState` | `_maybe_establish_grid`, `mark_phase_zero` | out of this review's dimension — see the clock reviewer; note `_flush_deferred_grid_side_effects` is dead code |

---

*Reproductions live in the session scratchpad (`repro_gridwait.py`, `repro_flush_reset.py`,
`repro_twofinger.py`, `repro_stopqueued.py`, `repro_pending_forever.py`). No product code
or test was modified by this review. Nothing here was verified on hardware; every finding
above is reproducible offline, which is deliberate — none of it needs Mitch's ears, and
none of it made a sound.*
