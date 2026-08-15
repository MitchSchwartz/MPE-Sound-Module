# Grumpy review — looper control layer (2026-08-15)

Impartial fresh-context pass, no history with the code. Branch `dev`.

**Read:** all of `scripts/sooperlooper-apc-bench.py`, `scripts/sooperlooper/{apc_footswitch,apc_grid,apc_transport,sl_bench_listener,sl_grid_state,sl_grid_sync,sl_hud_monitor,sl_loop_states}.py`, `sl-watchdog.py`, head of `sl-health.py`, `wire-jack-graph.sh` in full, `patch_browser/{looper_hud,sl_hud_state,looper_clock_monitor}.py`, the `_draw_looper_hud` path, the looper block in `touch_browser_layout.py`, all six named test files, spec §J/§K, DECISIONS 2026-08-15. Ran the six test files (55 pass, 0.26 s).
**Not read:** rest of `touch_browser_draw.py`, `spike-internal-sync-phase.py`, other shell scripts, SooperLooper C++ (engine-fact citations taken on trust).

## 1. First impressions

Not a hackathon — something rarer: **a very well-documented hackathon.** Comment density is extraordinary; nearly every non-obvious line carries a dated rationale naming the bug it fixed and what it cost. `sl_grid_state.py:8-17`, `sl_grid_sync.py:11-15`, `apc_footswitch.py:34-63` are better than most professional codebases carry. §K.4 is a first-rate artifact.

That is the tell. **The comments are load-bearing.** They are not explaining a design; they are the only thing holding a pile of point fixes in a legible shape. Strip them and `apc_footswitch.py` is 200 lines of interleaved flags with no discoverable model. When the prose is this good and the structure this poor, you are looking at a debugging log that was never turned into a design.

Strengths that survive a rewrite:

- **`sl_grid_state.py` is genuinely well-engineered** — 145 lines, one idea, no I/O, no threads, fully unit-tested, correctly separates "the clip that defined the tempo" from "the clock." Keep as-is.
- **The engine-fact catalogue** (§K.4, `sl_grid_sync.py:86-91`, `apc_footswitch.py:326-334`) is hard-won knowledge that cannot be cheaply re-derived. It is the actual asset.
- **`sl_bench_listener.py:73-87`** — refusing to start on bind failure, with reason and fix in the message — is exactly right.
- **`apc_transport.py`** is clean, boring, testable. It is what the rest should look like.
- **`sl-watchdog.py`'s doctrine** (fail open on audio, fail loud on control, never auto-repair what can destroy a take) is correct and non-obvious. Implementation is broken; policy is right.

Against that: `apc_footswitch.py` is 491 lines owning the state machine, LED renderer, OSC protocol, grid callbacks, pad timing, and two module-level transport functions.

## 2. Architecture & structure

The module split is not wrong — `apc_grid` / `apc_transport` / `sl_loop_states` / `sl_grid_state` each do one thing. Everything left over got dumped into `LoopFootswitch`, which is four objects in one coat: pad input decoder, loop state machine, engine mirror, LED renderer. Different lifetimes, different threads, different owners, all mutable attributes of one object with no lock.

Boundaries run the wrong way:

- `build_footswitches` gives all 16 pads a shared `GridState`, then `reset_all_loops` **excavates it from the collection** (`apc_footswitch.py:437-442`) with a `break`; `stop_all_loops:482` does the same with `next(...)`. A session-level object should be passed to session-level functions.
- **Callbacks flow downward.** `on_grid_established` lives in `main()` (bench:117) and is passed into 16 footswitches so whichever lands first reconfigures all 16 loops. The pad that recorded the take owns global engine configuration.
- `reset_all_loops` / `stop_all_loops` mutate private members of every footswitch — `fs.state`, `fs.awaiting_quantize`, `fs.sl_state`, `fs._sync_led()`, `fs._on_grid_dropped`. The class is a struct.
- **Four processes, four hand-rolled OSC clients**, four copies of subscribe/cache/re-register: `sl_bench_listener` (9953), `sl_hud_monitor` (9952), `sl-watchdog` (9961), `sl-health` (9954). Nobody wrote `SlClient` once.
- **The SL state enum exists in four places**: `sl_loop_states.py:3-9`, `sl_hud_monitor.py:38`, `sl_hud_state.py:68`, `sl-health.py:33-36`. Three use raw literals. The module that exists to solve this is imported by one consumer.

There is no seam to test at, other than a `MagicMock` — which is what every test uses.

## 3. Code quality

**Naming.** `state` vs `sl_state` vs `quantized` vs `awaiting_quantize` is the central sin. `quantized` means "grid mode was on at process start," which is not what the word means anywhere else here.

**Dead code, grep-confirmed:** `anchor_phase`, `display_bpm` (imported at bench:26,28, never called — `anchor_phase` has a 12-line comment explaining the most important insight in the design and zero call sites); `_osc_send` (`:71`); `LED_YELLOW_BLINK` (`:32`); `self.num_loops` (`:96`); `ACTIVE_RECORD`; `LOOP_CLIP_ROWS`/`CONTROLLER_ROWS`; `set_count_in` ("Deprecated alias", no call sites, no date); `beat_and_bar` and `beat_and_bar_from_transport` in `sl_hud_monitor.py`, whose docstring says *"kept for tests"* and whose subsystem was deleted in `e279d6f`.

**There is production code that exists only so a test of it can exist.** That is the purest statement of "tests cementing accidents."

**Error handling — two extremes, neither considered.** `sl_bench_listener.py:81` turns a bind failure into a fatal `SystemExit` with a fix. `sl-watchdog.py:152-161` catches bare `Exception` and ignores the return code. `sl_hud_monitor.py` has none — `_from_sl` returns `None`, caller returns `False`, HUD goes stale with no log. `playback_sources()` swallows everything into an empty set, read by the caller as "no problem."

**Import hygiene.** `import time` inside two functions (`sl_bench_listener.py:49,59`). Production imports `apc_footswitch` top-level via `sys.path.insert`; tests import `scripts.sooperlooper.apc_footswitch` — two module identities for one file, live under pytest.

## 4. Code smells (hall of shame)

### 🔴 A. The watchdog's repair discards the one fact it needs

`sl-watchdog.py:151-161`:

```python
subprocess.run(["bash", str(script), "connect"], capture_output=True, timeout=60)
if any(s.startswith(f"{JACK_CLIENT}:common_out") for s in playback_sources()):
    repaired.append("reconnected common_out -> playback")
```

`capture_output=True`, no `check=True`, no read of `returncode`/`stdout`/`stderr`. A non-zero exit is not an exception, so `except` never fires. **This is the whole answer to "logs PROBLEM every cycle, never `repaired`."** The script is failing and reporting it into a discarded buffer.

Likely cause, `wire-jack-graph.sh:96-97`: `wire_connect` calls `need_cmd oscsend`, which `exit 1`s before `connect_graph` runs. Under a minimal PATH, **liblo-tools gates the audio repair** though it is unnecessary for reconnecting a port. Second candidate: `set -euo pipefail` + `try_oscsend` returning 1 aborts after connections are made — a repair that worked but exits non-zero. Third: `JACK_CLIENT` mismatch if the engine was started with a different `-j` name makes the detector permanently true and the repair permanently ineffective.

*Fix:* capture the result, log `stdout`/`stderr` on non-zero, drop `need_cmd oscsend` from `wire_connect`, log the observed `playback_sources()` on failure.

### 🔴 B. `_clear_loop` doesn't clear the loop from the grid's bookkeeping

`apc_footswitch.py:269-279`. `GridState.cancel()` clears only `_pending`; `_occupied` is untouched. The grid can only drop if the engine later reports state exactly `0`. Any other state for a cleared loop — `Paused` (14), which the bench itself provokes via `pause` in `reset_all_loops:444` — leaves `_occupied` non-empty and **the grid established forever. Root cause 1 for the pad-hold path.**

`self.sl_state = SL_STATE_OFF` makes it worse: the bench asserts a state it did not observe; the next update overwrites it. Bench state is authoritative for exactly one poll interval.

*Fix:* call `grid.note_loop_content(loop, False)` and fire `_on_grid_dropped` on a `True` return, as `sync_from_sl` does. Stop fabricating `sl_state`.

### 🔴 C. Optimistic bench state paints a green pad over an empty loop

`apc_footswitch.py:313-317` asserts `STATE_PLAYING` with no engine confirmation; `:231` paints the LED from bench state, not `sl_state`. If an in-flight update has pushed the pad to `STATE_STOPPED`, the next tap takes the `STATE_STOPPED` branch and sends `pause_off`+`trigger` to an **empty loop** instead of `record`. Green pad, nothing recorded. `grid.arm()` at `:293` never runs, so no take is armed as definer and the engine's grid config stays in force — **root cause 1 for the reset path.**

*Fix:* render the LED from `sl_state` only (the code's own comment at `:213-215` says this and then doesn't do it); dispatch `_tap` on engine state.

### 🔴 D. The queued-launch flag is destroyed by the next poll

`apc_footswitch.py:130-133` clears `_launch_queued` on every `PAUSED`/`MUTE` update. A quantized trigger leaves the clip `Mute` until the boundary — up to a bar — and the 100 ms update clears the flag. `changed` is `False` so the blink survives by luck, but any later `_sync_led` drops the pad to solid yellow with the launch still pending. Exactly the failure the comment at `:43-47` says cost an evening.

*Fix:* clear only on the transition into `PLAYING`, or derive the LED from `(sl_state, pending_command)`.

### 🟡 E. `problems.pop()`

`sl-watchdog.py:159` un-reports a problem by popping a list. Correct only because exactly one problem is appended above it. *Fix:* build `problems` after the repair attempt.

### 🟡 F. The health probe mutates the instrument it monitors

`sl-watchdog.py:167-186` writes `dry` on loop 0 every 10 s and restores it 0.4 s later — **only if `before` was readable**. On a read timeout the write still happens and `dry` is left at 0.5 permanently, undoing `wire-jack-graph.sh:92`'s deliberate `dry 0.0`. *Fix:* probe an inaudible control, restore unconditionally.

### 🟡 G. Two module identities · 🟡 H. A test contradicting the spec · 🟢 I. `__main__` above a test class · 🟢 J. Inconsistent LED de-dup

H is the notable one: `tests/test_apc_footswitch.py:120-133` asserts `grid.established` after clearing the only clip — the opposite of §K.3 — and passes only because of bug B. It conflates "the grid doesn't depend on the defining clip" (true) with "the grid survives zero clips" (false). Its second `on_pad_up()` has no matching `on_pad_down` and is a silent no-op, so the test never stopped the recording it believes it stopped.

## 5. Logic & business rules

### The specification, recovered from the code

> **Grid.** Two modes only: *no grid*, *grid at tempo T*.
> **Establishment.** The first take while no grid exists is free-form — no count-in, no length quantization, no sync. On landing, its length defines one bar; T = 240/len (4/4), written exactly, rounded only for display. The grid then stands alone; the defining clip is ordinary.
> **Teardown.** When clips holding audio reach zero, by any route, the grid ceases and the engine returns to free-form.
> **Per-clip, with a grid.** Record counts in to the next bar, length snaps to whole bars. Stop = quantized `mute_on`. Start = quantized `trigger` (from clip start, lifts mute). Clear = `undo_all`, immediate.
> **Global.** Stop All immediate and un-quantized, re-anchors phase. Track Reset clears everything and tears down the grid.
> **Feedback.** Solid = happened. Blink = queued. Off/red/off/green = recording, queued to play. A quantized wait must be time-bounded and announce its own failure.

Coherent, small, correct. **It fits on a page. The code implementing it is ~1,000 lines and cannot be reasoned about.**

### The real state machine

Current pad state is `(state) × (sl_state) × awaiting_quantize × _stop_queued × _launch_queued × _pad_down × _hold_fired × _led_transition` — 224 nominal combinations before pad flags, against a shared `GridState` and a constructor-time `quantized`. The **actual** states are nine:

| # | State | Engine | LED |
|---|---|---|---|
| 1 | Empty | Off | off |
| 2 | Queued to record | WaitStart | red blink |
| 3 | Recording (free-form) | Recording | red |
| 4 | Recording, stop queued | WaitStop | off/red/off/green |
| 5 | Playing | Playing | green |
| 6 | **Queued to stop** | Playing + pending mute | **missing** |
| 7 | Stopped | Mute | yellow |
| 8 | Queued to play | Mute + pending trigger | green blink |
| 9 | Stopped by Stop All | Paused | yellow |

- **State 6 does not exist.** `_tap:322-323` sends `mute_on` and immediately sets `STATE_STOPPED` → solid yellow while the clip sounds for up to a bar. Violates the design's own rule at `:47`.
- **7 and 9 are conflated** (`sync_from_sl:130`) but are not equivalent to the engine: `trigger` on Paused restarts it while still paused. `_tap:335-336` papers over this with `pause_off` then `trigger`, betting on queue ordering. Undocumented, untested.
- **`quantized` is dead weight and actively wrong** — set once at construction, never updated; the real predicate is `grid.established`. After a mid-session grid drop the pad still arms a wait no boundary will end.
- **`awaiting_quantize` has two meanings**, cleared from five sites plus a timeout, and gates all input.
- **Unreachable:** `STATE_PLAYING` with `_launch_queued`; the `else` at `:346-348`.
- **Contradictory and reachable:** `STATE_PLAYING` + `sl_state == OFF` (green over empty); `STATE_STOPPED` + `sl_state == PLAYING` (yellow over audible).
- **`_stop_queued`** is a deferred command with no timeout and no cancellation but `_clear_loop`.

### Races — three threads, no lock

1. MIDI/main at 2 ms → `_tap` mutates everything, sends MIDI.
2. **`ThreadingOSCUDPServer` is thread-per-datagram** — every state message runs `sync_from_sl` on a fresh thread, mutating the same fields, sending MIDI.
3. `poll_led` on main, reading/writing LED state.

- **🔴 Out-of-order state delivery.** No sequence number, no timestamp, no monotonic guard. A stale `Playing` landing after a fresh `Off` leaves a green pad over an empty loop indefinitely. **Direct mechanism for the reported symptom.**
- **🔴 Stale updates survive a reset.** Updates registered before `reset_all_loops` land after it and re-populate `state` and `GridState._occupied`. The next tap hits `STATE_STOPPED` → `pause_off`+`trigger` on an empty loop instead of `record`+`arm`. **Mechanism behind both "grid still on after reset" and "green with no audio."** No generation counter, no post-reset ignore window.
- **🔴 `GridState` mutated from both threads** — `arm()` on main, `establish()`/`note_loop_content()`/`reset()` on OSC. Interleaving clears `_pending` microseconds after it is set; the take never establishes the grid and there is no explanation.
- **🟡 `rtmidi.MidiOut` used from multiple threads** — not documented thread-safe; 3-byte sends can interleave.

### Engine-configuration reliability

`set_grid_active` sends **96 UDP datagrams** in a burst; `reset_all_loops` follows with 32 more. Nothing reads back, retries, or verifies. §K.6 admits mode cannot be read back. One dropped datagram leaves a pad quantized while `GridState` says free-form — an independent path to "the first clip was quantized."

## 6. Test strategy

55 tests, 0.26 s, all green, readable, good docstrings. And **almost entirely tests of implementation.** Every one drives `LoopFootswitch` against `MagicMock` and asserts on the OSC call list. There is no fake engine, so they can only assert *"we sent these strings in this order"* — the thing most likely to change and least likely to be the bug.

- **The bug is in the gap between the two state models, and nothing tests the gap.** No test delivers a stale, out-of-order, or concurrent update. The entire class of §5 defects is invisible by construction.
- **`reset_all_loops` has zero tests** — the function reported broken. **`sl-watchdog.py` has zero tests** — the other function reported broken.
- **Bug-cementing confirmed** (§4-H, plus two dead HUD helpers tested with no production callers). §J records two earlier instances, so this is the third and fourth of a known recurrence.
- **Assertions on private state** — `_stop_queued`, `_led_transition`, `awaiting_quantize`, `fs._wait_since -= ...`. These are how the tests will fight the refactor.
- **Missing wholesale:** integration against a real/faked SL, concurrency, property tests on `derive_tempo`, engine-config round-trip, LED table as a table.

`test_sl_grid_state.py` would survive a rewrite intact — not a coincidence; it is the only pure module.

## 7. Security & performance

Nothing alarming. Local MIDI/OSC bench, listeners bind `127.0.0.1`, no secrets. **20+ undocumented env vars** across six modules is an operability problem, and is what §K.6's mode-visibility gap is made of.

- **2 ms busy-poll** main loop (bench:201) — 500 wakeups/sec on a Pi; `rtmidi` supports a callback.
- **320 thread spawns/sec** in the bench, 480/s in the HUD monitor. Thread-per-datagram is the wrong primitive at that rate and is the direct cause of the ordering races.
- SL's OSC input is being treated as an infinitely reliable ordered channel. It is neither.

## 8. Developer experience

Docstrings and §K are exceptional — they record what was measured, where, and what the wrong belief cost. **But §J is now a lie**: it states as fix-applied that the bench refuses transport mode without a rolling transport. That probe was deleted with the JACK path in `e279d6f`. `sl_grid_sync.py:93-94` will send `sync_source = -1` on `MPE_SL_GRID_CLOCK=transport` with no probe — the exact failure §J exists to prevent, re-armed behind an env var.

No way to exercise any of this without an APC mini, JACK, and a running SooperLooper. No fake engine, no `--simulate`, no OSC replay. Given the development history, **that absence is the story of this codebase.**

## Verdict

**Yes, this is bad code — but bad in an unusually recoverable way, and the user's own diagnosis is precisely correct.** It is not incompetent; it is *undesigned*, and those are different diseases with different treatments. Every individual decision is defensible and most are well-argued in a comment. What is missing is the pass nobody made: sitting down once the behaviour was right and asking "what are the states?" The result is four notions of state where there should be one, three threads with no lock where there should be a queue, and a test suite pinning the accidents in place.

The expensive part is already done. The hard-won knowledge — SL's engine facts, the grid model, LED semantics, quantization rules — is written down and **portable**. The 491-line file wrapped around it is not.

**Rewrite the control layer, keep everything else.**

- **Keep as-is:** `sl_grid_state.py`, `apc_grid.py`, `apc_transport.py`, `sl_loop_states.py` (extend to be the *only* state table), `patch_browser/looper_hud.py`, `sl_hud_state.py`. ~400 lines, all fine.
- **Rewrite `apc_footswitch.py` into three modules:** `loop_model.py` (~120 lines) — pure `next_state(state, event) -> (state, [commands])` over the nine states, no I/O, no threads, no time. `led_table.py` (~40 lines) — pure map from `(engine_state, pending_command)` to pattern. `sl_session.py` (~150 lines) — owns the sixteen models, the `GridState`, one `queue.Queue` fed by MIDI and OSC, one thread draining it, **which deletes every race by construction rather than by locking.** Plus `sl_client.py` (~80 lines) shared by all four processes.
- **Fix in place today, independent of the rewrite:** the watchdog (§4-A/E/F) and `_clear_loop` (§4-B).

Target: **~400 lines of new control code replacing ~700**, roughly 300 of them pure and testable without a mock. A `FakeEngine` consuming commands and emitting a state feed is ~80 lines and would have caught every §5 defect.

The tests will fight you. Delete `test_apc_footswitch.py` wholesale and rewrite against the pure functions; keep `test_sl_grid_state.py` and `test_sl_grid_sync.py`; delete the dead HUD helpers and their tests. Do it in one commit **before** touching the implementation, so the fight is honest.

## Priority backlog (🔴 only)

1. **Serialize all state mutation onto one thread.** Replace `ThreadingOSCUDPServer` + direct `sync_from_sl` with a `queue.Queue` drained by the main loop, plus a generation counter discarding updates registered before a reset. Fixes reset-still-quantized and green-with-no-audio at the root; eliminates the `GridState` and `MidiOut` races.
2. **Make the watchdog report its repair.** Check `returncode`, log `stdout`/`stderr`, drop `need_cmd oscsend` from `wire_connect`. Three lines turning a silent permanent failure into a diagnosable one.
3. **`_clear_loop` must call `grid.note_loop_content(loop, False)`** and fire `_on_grid_dropped`; stop fabricating `sl_state`. Delete the contradicting test and replace it with the two it should have been.
4. **Render the LED from `sl_state` alone; dispatch `_tap` from `sl_state` alone.** Delete `self.state` and `self.quantized`. Collapses the two contradictory state models and closes the green-over-empty hole.
5. **Add the missing "queued to stop" state** and stop clearing `_launch_queued` on every `Mute` poll. Without these the design's core promise — solid means it happened, blink means it's coming — is false for half the transitions.
