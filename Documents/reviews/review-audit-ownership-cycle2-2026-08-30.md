# Audit — ownership branch, cycle 2

**Branch:** `refactor/looper-ownership-2026-08-30` · **2026-08-30, overnight**
**Auditor:** me, reading the code myself. Every claim below was verified against
source or by execution; where a reviewer's number differed from mine, both
numbers are given and the discrepancy is explained rather than smoothed over.

Cycle 1 landed Stages 1 and 2 (`aeb7a61`, `8106513`). This cycle audits the
findings cycle 1 left as **"reported — audit pending"**, i.e. claims made by
subagents that I had not yet verified myself. Handing a builder an unverified
claim is the failure mode this branch exists to end, so none of them were acted
on until they appear below.

---

## 1. Stage 3 is absorbed, not skipped

The charter's Stage 3 is "Ownership — one owner per control; delete
`clear_unwired_surfaces`." Stage 2 did both:

- `clear_unwired_surfaces` is gone. The five remaining mentions are prose in
  comments and tests explaining what used to happen and why it can't now.
- Each module submits to exactly one compositor layer: `apc_transport.py` ->
  `LAYER_TRANSPORT`, `slot_surface.py` -> `LAYER_SURFACE` + `LAYER_HOLD`,
  `track_gesture.py` -> `LAYER_GESTURE`.

One owner per control is now **enforced by the compositor's layer priority**
rather than asserted in a comment. Residue: `RESERVED_GRID_NOTES`
(`apc_grid.py:64`) has no production reader left — it existed to feed
`clear_unwired_surfaces`. It is still read by `tests/test_apc_grid.py:38-39`.
Decide its fate in a later stage; a constant whose only consumer is a test
asserting its length is not load-bearing.

**Stage 3 therefore does not get its own cycle.** Stage 4 is split instead.

---

## 2. Verified this cycle

### VERIFIED — `_flush` is keyed by `loop`, but the unit of work is `(loop, slot)`
**P0. Silent take loss. Worse than reported.**

`slot_runtime.py:114` — `self._flush: dict[int, tuple[int, Path, Path, float]]`.
Keyed by loop. The tuple's first element is the slot, so the code knows the
slot matters; nothing ever compares it.

`_ensure_flushed(loop)` (`:590`) asks about the track's **active** slot:

```python
track = self.track(loop)
if track.active_slot is None: return FLUSH_CLEAN
active = track.slot(track.active_slot)
if active is None or not active.dirty: return FLUSH_CLEAN
if loop not in self._flush:
    self._begin_flush(loop, track.active_slot)
return self.poll_flush(loop)
```

`if loop not in self._flush` treats *"a job exists for this loop"* as *"a job
exists for this slot."* When a flush for slot 0 is still in flight and the
active slot has moved to slot 1 and gone dirty, no flush is ever started for
slot 1. `poll_flush(loop)` then resolves the **slot-0** job, renames slot 0's
temp file over slot 0's path, marks **slot 0** clean, and returns
`FLUSH_CLEAN`.

The caller asked about slot 1 and was told "clean." All three call sites
(`:364`, `:474`, `:496`) then proceed to switch or overwrite — and `:364`'s
whole purpose is the opposite:

```
f"track {loop + 1}: REFUSING to switch — the take on the "
f"current slot did not reach disk, and switching would "
f"overwrite the buffer holding it"
```

That refusal is the safety net for exactly this loss, and the wrong key routes
around it. **No reset is required to reach this.**

### VERIFIED — `reset()` does not clear `_flush`
**P0, same family, compounds the above.**

`slot_runtime.py:303` rebuilds `_tracks` and clears `_deferred`, `_grid_wait`
and `_awaiting`. `_flush` is not cleared. Two consequences:

1. A flush in flight when a clear-all happens still completes: `poll_flush`
   renames the temp over the clip path, resurrecting audio the player just
   cleared, onto a track the model now believes is empty.
2. Worse, combined with the key defect: after a reset, a **new** take in the
   same cell finds the stale job still present, is never saved, and is marked
   clean by the old job's completion. The bytes on disk are the pre-reset
   take; the model and the surface both say saved.

This is the measurement-integrity shape again — a reading that is identical
whether it worked or not.

### VERIFIED — `_end_tail` has a real TOCTOU, and the failure is audible
**P1, in Stage 4a's scope.**

Two threads reach it:
- OSC dispatcher: `sl_bench_listener.py:49` -> `sync_in_peak()` (`track_gesture.py:429`) -> `_end_tail`
- Idle loop: `track_gesture.py:96` -> `poll_tail()` (`:437`) -> `_end_tail`

`_end_tail` (`:397`) is read, guard, clear, unsynchronized:

```python
tail = self._tail
if tail is None: return
self._tail = None
```

Both threads can pass the guard, and `_end_tail` then sends `self._hit("overdub")`.
The code's own comment states that **overdub is a TOGGLE**. So a double fire
leaves the overdub and immediately starts another one, welding room tone onto
the take. The `if self.sl_state == SL_STATE_OVERDUBBING` guard narrows the
window but cannot close it: `sl_state` is updated asynchronously by that same
OSC thread. `poll_tail` runs at the measured ~485 Hz idle rate, so this is a
window that gets hit, not a theoretical one.

### VERIFIED — `wet` has two writers; the README says it has one
**P1.**

`scripts/sooperlooper/README.md:113-118` states level composition lives in one
place and "the point of the seam is that nothing else ever writes `wet`."

`looper_songs.py:677` writes it directly, bypassing `loop_mix.wet_for()`:

```python
probe.send(f"/sl/{loop}/set", ["wet", float(entry.wet)])
```

`looper_songs.py:534` reads it back independently for song save. So song
load/save owns `wet` on its own axis. **Exempt, checked:** `sl_osc_session.py:192`
is `register_auto_update` — a subscription, not a write.

### VERIFIED — a song cannot restore its own grid
**P1, in Stage 4a's scope.**

`looper_songs.py:473-475`:

```python
tempo = probe.get("tempo", -1)
if tempo is not None and float(tempo) > 0:
    probe.send("/set", ["tempo", float(tempo)])
```

This reads the engine's **current** tempo and writes it straight back — it does
not restore the song's tempo, because the manifest has no field for one. And
per upstream `engine.cpp:2174`, `Engine::set_tempo` zeroes `_quarter_counter`
and `_tempo_counter`: **re-sending the tempo is the phase reset.** Loading a
song therefore resets grid phase to whatever the engine happened to hold.
Grep confirms no `bars` or `eighth_per_cycle` key anywhere in the manifest
format.

### VERIFIED — `player-env-parity.pi5.env` disagrees with `MAX_USABLE_LOOPS`
**P0 by blast radius, latent today.**

`config/platform/player-env-parity.pi5.env:24` sets `MPE_SL_LOOPS=16` and `:27`
sets `MPE_SL_SCRATCH_LOOP=14`, against `MAX_USABLE_LOOPS = 15`. (`pi4.env:20`
is 8 and consistent.) Latent because the bootstrap is the last writer at boot
today — which is a fact about ordering, not a guarantee, and ordering is
exactly what nobody re-checks.

---

## 3. Corrected — the periodic-loop lint, including a correction to myself

**Reported:** "`test_periodic_loop_lint` passes while 10 of 12 evasions get
through." **Measured by me: 9 of 12 escape.** The reviewer was substantially
right.

My **first** measurement said 12 of 12 escaped. That was my harness being
wrong, not the lint: I probed with `subprocess.run(['ls'])`, and the lint
deliberately only flags `subprocess.*` when an argv string constant names a
known-expensive command. `ls` is correctly ignored. Recording this because it
is the fifth time on this branch that a confident reading beat reading the
source, and the first four were also mine.

Re-measured against what the lint actually targets:

```
CAUGHT   subprocess.run(['jack_lsp']) in while True
CAUGHT   one function call deep
CAUGHT   method on self
ESCAPED  os.system('jack_lsp')          ESCAPED  argv in a variable
ESCAPED  os.popen('jack_lsp')           ESCAPED  argv via f-string
ESCAPED  import subprocess as sp        ESCAPED  while self.alive:
ESCAPED  from subprocess import run     ESCAPED  for x in items:
                                        ESCAPED  while not stop.wait(1.0)
3/12 caught, 9 escaped
```

The interprocedural core works — that is the valuable half and it should be
kept. The holes are three, and the third is the serious one:

**Hole 1, call shape.** `os.system`, `os.popen`, aliased imports and
from-imports all escape `_call_name`. (`pgrep` is also inconsistent: it appears
in the argv substring check but not in `FORBIDDEN_CALLEES`.)

**Hole 2, argv shape.** Only string *constants* are inspected, so any argv
built in a variable or an f-string escapes.

**Hole 3, loop shape — and this one is live.** `_is_periodic_loop` recognizes
`while True`, `while not X.is_set()`, `while running`, and `for … in range(…)`.
It does **not** recognize `while not self._stop.wait(interval)` — which is the
idiomatic Python periodic-poll shape, being the sleep and the stop-check in
one. That is the **main loop of four of the nine modules the lint is pointed
at**:

| Module | Main loop | Seen by lint? |
|---|---|---|
| `patch_browser/surge_cpu_monitor.py:101` | `while not self._stop.wait(...)` | **no** |
| `patch_browser/engine_state_monitor.py:44` | `while not self._stop.wait(...)` | **no** |
| `patch_browser/looper_clock_monitor.py:53` | `while not self._stop.wait(...)` | **no** |
| `patch_browser/surge_peak_monitor.py:98` | `while not self._stop.wait(...)` | **no** |
| `patch_browser/surge_poly_governor.py:633` | `while not self._stop.is_set()` | yes |

Confirmed independently: those four contain no `while True` at all, so the lint
walks them and finds **zero periodic loops**.

**Hole 4, list drift.** `PERIODIC_LOOP_MODULES` is a hand-maintained tuple of
nine paths. Eleven other modules contain `while True` and are not in it,
including **`scripts/sooperlooper-apc-bench.py`** — the looper's own control
surface, and the busiest loop in the system at the ~485 Hz measured in Stage 2.

**Honest severity: P1, latent.** I checked all five blind modules for
`subprocess`, `os.system`, `os.popen`, `jack_lsp`, `journalctl`,
`jack_cpu_load` and `pgrep`: **zero hits.** There is no CPU regression today.
The defect is that the only automated defense of the CPU doctrine — which
`AGENTS.md` calls the scarcest resource — passes green while blind to the main
loop of nearly half its own list and to the busiest looper process entirely.
It is a guard that reads the same whether it is guarding or not.

---

## 4. REFUTED — "banking while holding a pad unlinks another track's clip"

**Reported P0. It is already fixed, and was fixed before this branch opened.**

`track_gesture.py:203`, `release_pad()`, names the exact reported failure in its
own docstring — and names it as the thing it prevents:

> Banking while a pad is held would otherwise strand `_pad_down` on the track
> that just left the screen: `poll_hold()` runs for every track, visible or not,
> so ~`hold_s` later it would fire the long-press and clear a loop the player
> never let go of.

Traced rather than taken on the docstring's word, per the charter rule:

1. `apply_view()` (`:891`) calls `fs.release_pad()` for **every** gesture at
   `:919`, before `set_note`.
2. `apply_view` has exactly two production callers — `set_view`
   (`sooperlooper-apc-bench.py:422`) and `reopen_apc` (`:528`). There is no
   third bank-change path.
3. Both guards are real, not decorative:
   - `poll_hold`: `if not self._pad_down or self._hold_fired: return` — a
     released gesture cannot fire the long-press.
   - `on_pad_up`: `if self._pad_down and not self._hold_fired:` — the note-off
     that dispatches to whichever track *took over* that pad does nothing,
     because that gesture was released in the same call.
4. `git log -S"def release_pad"` gives `2fc657e`, and
   `git merge-base --is-ancestor 2fc657e 0bc0b63` confirms it predates this
   branch's point.

The residual behaviour is that a pad held across a bank change does nothing at
all. That is deliberate — `release_pad` is documented as "abandon an in-flight
pad gesture without firing it" — and banking mid-press is genuinely ambiguous.
Not a defect.

Recorded at length because of the shape: a docstring describing a bug that was
*fixed* was read as a bug that *exists*. That is the same confident-restatement
failure as the other five on this branch, and it is worth one paragraph to make
the pattern impossible to miss.

## 5. Still not audited by me

Carried forward honestly rather than quietly dropped. Still
**reviewer-reported and unverified**:

- `restart-sooperlooper.sh` never emits `looper.engine.started`, leaving a
  split brain after a restart (**P0, reported**; handed to Stage 4a only as a
  *check whether this is the same subject*, explicitly not as a fixed premise).

---

## 6. Staging from here

| Stage | Work | Status |
|---|---|---|
| 1 | Control registry | landed `aeb7a61` |
| 2 | LED compositor | landed `8106513` |
| 3 | One owner per control | absorbed by Stage 2 |
| **4a** | **Clock + tail one owner** — tail cap P0, `_end_tail` race, song grid restore | **in progress** |
| 4b | Track state one owner — `_flush` key, `reset()`, single `wet` writer | next |
| 5 | Binding table; periodic-loop lint holes | later |
| 6 | Grid behind the compositor | later |
| 0 | Capability probe — **Mitch, morning, eyes** | blocked on hardware |

## 7. For Mitch, in the morning

Unchanged and still the highest-value five minutes available: stop the session,
run `sooperlooper-apc-bench.py --dump-midi`, press Up/Down/Left/Right, and
record the four real mk2 bank-arrow notes at MEASURED tier in `device_facts.py`
and as rows in `control_registry.CONTROLS`. Until then tracks 9–15 are
unreachable from the surface. Do not fill them in by reasoning — reasoning has
produced three wrong answers about this panel already.

Panel **appearance** after Stage 2 is unverified by construction: no subagent
and no test can see the LEDs. The behaviour changes are listed with their canon
in `8106513`'s commit message. If something looks wrong, that message is the
list of things deliberately changed, and each is revertible alone.
