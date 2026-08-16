# Grumpy dev review — SooperLooper control layer (2026-08-14)

> **⚠️ SUPERSEDED IN PART — see the audit correction at the end of this file (2026-08-14).**
> This review is wrong about the cause of the wrap pop (§4 item 1) and wrong about the
> mechanism of the dead loop 2 (§4 item 2), and its "rewrite the control layer" verdict
> is not supported by its own evidence. Read the correction before acting on the backlog.

Scope read: `scripts/sooperlooper-apc-bench.py`, `scripts/sooperlooper/{apc_footswitch,apc_grid,apc_transport,sl_grid_sync,sl_bench_listener,sl_loop_states,sl_master_clock,sl-hud-monitor}.py`, `patch_browser/{sl_hud_state,looper_clock_monitor}.py`, `scripts/sooperlooper/wire-jack-graph.sh`, `tests/test_{apc_footswitch,apc_grid,apc_transport,apc_bench,sl_grid_sync,sl_hud_state,sl_master_clock}.py`, `docs/measurements/sooperlooper-eval-2026-08-14.md`.
Not read: the 867-line `Documents/specs/looper-jack-client-spec.md` in full, the rest of `patch_browser/`, SooperLooper's own source.

## 1. First impressions (the gut check)

It looks like professionals wrote it and then panicked. The module split is sane, the docstrings cite upstream docs, the naming is honest, there are tests, and the eval log is one of the more disciplined engineering documents I've read in a hobby-adjacent repo. That's real.

And then you open `apc_footswitch.py` and find a shadow state machine mirroring an engine that already has one, a module-level mutable global shared across threads with no lock, and a `threading.Thread(sleep(0.35))` fired per user gesture to re-poke the engine. That is not a codebase with three bugs in it. That is a codebase with one bad idea in it, expressed three times.

## 2. Architecture & structure

**The load-bearing mistake: two state machines, one truth.**

SooperLooper is a state machine. It publishes its state over OSC (`sl_loop_states.py` even enumerates the codes). The bench then maintains a *second*, independent state machine — `STATE_IDLE/RECORDING/PLAYING/STOPPED` — that drives the LEDs and decides what OSC to send next. Reconciliation between the two exists but is deliberately partial:

```python
def sync_from_sl(self, sl_state: int) -> bool:
    """Mirror SooperLooper state for quantized slaves only (loop > 0)."""
    if self.loop == 0:
        return False
```

Loop 0 — the master, the one whose behaviour the whole grid depends on — is **never reconciled with the engine**. Its LED and its state are pure bench fiction. This is exactly the reported symptom "loop 0 pad goes green but may not keep looping": the pad is green because the bench believes it is, not because anything is playing. The eval doc records `loop_len: 0, state: 0` in the HUD while the LED was green and calls it "bench/SL desync." It isn't desync. It's a design that has no mechanism to be in sync.

**State authority is smeared across four places:** the SL engine, the `LoopFootswitch` objects, `~/.mpe_sl_hud_state.json`, and `~/.mpe_sl_master_clock.json`. Four answers to "is loop 0 playing and how long is a bar." They disagree, and nothing arbitrates.

**Process topology is glue, not architecture.** Four processes (SL engine, bench, HUD monitor, touch UI) coordinating through two JSON files in `$HOME` and three UDP ports (9951/9952/9953), with two independent OSC listeners registering auto-updates against the same engine, no supervision, no startup ordering, and no health check. The eval doc's own line — *"services running via absolute python paths when wrapper scripts failed from wrong cwd"* — is the architecture telling you what it is.

**Import rooting is load-bearing luck.** `sooperlooper-apc-bench.py` does `sys.path.insert(...' /sooperlooper')` and imports `apc_footswitch`; the tests import `scripts.sooperlooper.apc_footswitch`. Same file, two module identities. Since `_master_loop_established` is a module global, loading both paths in one process gives you **two copies of the master-established flag**. Not currently triggered; one refactor away from a genuinely unfindable bug.

## 3. Code quality

Naming is good. Docstrings mostly earn their keep. The problems are structural, not cosmetic.

- **Two-phase construction:** `LoopFootswitch.__init__` sets `self._osc = None`, then `bind()` fills it. `_tap()` on an unbound switch is an `AttributeError` at the worst possible moment. Pass the deps to the constructor.
- **Decorative privacy:** `fs._sync_led()`, `fs._pad_down`, `fs._tap()` are called from outside the class in `sooperlooper-apc-bench.py` and `apc_footswitch.reset_all_loops`. If it's the API, name it like the API.
- **Stale docstring:** `LooperClockMonitor` — *"Background reader for ~/.mpe_midi_clock_state.json"* — also reads SL HUD state now. Small, but this is the class the UI trusts.
- **~20 `MPE_*` env knobs** across these files with no registry and no validation. `int(os.environ.get("MPE_SL_LOOPS", ...))` dies with a raw `ValueError` on a typo in `/etc/mpe/mpe.env`.
- **Dead code:** `apc_transport.poll()` is documented as a "backward-compatible alias" — nothing in the tree calls it. `sl_master_clock.apply_internal_master()` (155-line module) is recorded as *"unwired in stabilization pass"* by your own eval doc, and still sits there looking callable.
- **`|| true` on every line defeats `set -euo pipefail`.** `wire-jack-graph.sh` suppresses failure on every `jack_connect` and every `oscsend`. A wiring pass where *nothing* connected exits 0 and logs "dry=0 on loops 0..15". The one script whose failure explains "no loop audio" cannot report failure.

## 4. Code smells (the hall of shame)

### 🔴 The pop generator — `trigger` spam at and after the loop boundary

Three separate mechanisms retrigger loop 0 from position 0. Every one of them is a mid-buffer discontinuity, i.e. a click. All three arrived with the grid work, which is precisely when the pop appeared.

**(a)** `apc_footswitch.py:161-167` — ending record on loop 0 sends `record` (which already starts playback in SL) and then an *additional* `trigger`:

```python
elif self.state == STATE_RECORDING:
    self._hit("record")
    if self.loop == 0:
        _master_loop_established = True
        self.state = STATE_PLAYING
        self._hit("trigger")          # <- redundant restart
        _schedule_grid_sync(self._osc, num_loops=self.num_loops)
```

**(b)** `_schedule_grid_sync` → `_ensure_master_playing` fires *another* `/sl/0/hit trigger` 350 ms later, mid-loop:

```python
def _run() -> None:
    time.sleep(delay_s)
    _refresh_grid_sync(osc, num_loops=num_loops)
    _ensure_master_playing(osc)
```

**(c)** `_ensure_master_playing(self._osc)` is also called on **every slave tap** from IDLE (line 158) and from STOPPED (line 177). Touch pad 3 → loop 0 jumps to zero. That's a pop *and* it moves the cycle boundary the slaves are quantizing to.

On top of that, `_refresh_grid_sync` re-sends `sync_source` and `eighth_per_cycle` to a *playing* engine, which can re-derive cycle length under the running loop.

*Fix direction:* delete `_ensure_master_playing` entirely and delete the post-record `trigger`. Ending a record starts playback; nothing else should touch loop 0's transport. Apply grid sync **once at startup**, never mid-performance.

### 🔴 Bricked slave pads — the `awaiting_quantize` trap

`apc_footswitch.py:168-170`, slave end-of-record:

```python
elif _master_loop_established:
    self.awaiting_quantize = True    # note: self.state is NOT updated
```

State stays `STATE_RECORDING` (LED red) and clearing it depends *entirely* on `SlBenchStateListener` delivering a `state` update. Meanwhile:

```python
def _waiting_for_quantize(self) -> bool:
    return self.loop > 0 and (self.awaiting_quantize or self.sl_state in QUANTIZE_WAIT)
```

...causes `_tap()` to **ignore every subsequent press on that pad**. If the update never arrives, the pad is dead for the rest of the session with no way to recover short of a hold-clear. That is "loop 2 does not play after record."

And the update has several ways to never arrive: `SlBenchStateListener.register()` runs **once at startup** and never re-registers (the HUD monitor re-registers every 15 s — someone already learned this lesson in the other file and it didn't propagate). If SL restarts, or port 9953 is taken, or the registration is dropped, silence. There is no timeout, no fallback poll, and no log line saying "I am waiting and nothing came back."

Also `sync_from_sl` has **no branch for `SL_STATE_OFF` (0) or `SL_STATE_PAUSED` (14)** — the two states a loop actually lands in when things go wrong. The bench can never converge back to idle.

*Fix direction:* poll `/sl/#/get state` as a fallback, time out `awaiting_quantize` after ~2 cycles with a visible LED state, and handle the full state enum.

### 🔴 Unsynchronized shared mutable state across three threads

`_master_loop_established` is a module-level global written from the MIDI thread (`_tap`, `_clear_loop`, `reset_all_loops`) and read from the timer thread (`_ensure_master_playing`). `LoopFootswitch.state` / `.sl_state` / `.awaiting_quantize` are written from the MIDI loop **and** from the OSC listener thread (`sync_from_sl`) with **no lock anywhere**. A tap and a state update interleaving leaves state and LED permanently inconsistent — which is the "desync" you've been chasing by eye.

The tests admit it: `setUp` does `footswitch_mod._master_loop_established = False`. When your unit test has to reach into another module's globals to get a clean fixture, the global is the bug.

### 🟡 Unbounded fire-and-forget timer threads

```python
threading.Thread(target=_run, daemon=True, name="sl-grid-sync-delay").start()
```

One per master-loop landing, uncancellable, unjoinable. Tap-tap-tap during a set and you stack N of them, each waking up 350 ms later to re-trigger loop 0 and re-blast grid sync. Nothing cancels the previous one. *Fix:* a single scheduler, or (better, per §4 item 1) don't defer anything at all.

### 🟡 SD-card grinder in the HUD hot path

`sl-hud-monitor.py:84-88` writes the HUD JSON every 100 ms — and inside that same tick calls `capture_from_hud_snapshot(payload)`, which does *another* tmp-write + rename of the master-clock JSON. That's **20 file writes per second, forever**, on a Pi SD card, to persist a tempo that changes once a take. *Fix:* write the master clock on change only, debounced to seconds.

### 🟡 Unverified hardware constants with no way to observe them

```python
NOTE_STOP_ALL_CLIPS_MK2 = 0x77
NOTE_SHIFT_MK2 = 0x7A
```

Cited to a protocol doc, never confirmed against the device on the bench. Shift+Stop All "doesn't work" and the eval doc can only say *"code exists — Mitch cannot use it."* That's not a debugging problem, it's an **observability** problem: there is no `--dump-midi` mode, so nobody can answer the ten-second question "does the APC even send 0x7A?" *Fix:* a 10-line raw MIDI monitor before touching the combo logic.

### 🟢 `reset_all_loops` sends every LED off twice

Once via `_reset_footswitch_state` → `_sync_led`, once via the explicit `all_loop_pads()` loop. Harmless, but it takes `midi_out` as a parameter when every footswitch already holds one.

## 5. Logic & business rules

The business rule — *loop 0 is a free-running master, loops 1-15 quantize to its cycle* — is a good rule and it is **nowhere stated in one place**. It's smeared across `apply_grid_sync`, the `if self.loop == 0` branches in `_tap`, the `loop == 0` early-return in `sync_from_sl`, and the `MASTER_LOOP` env var in the HUD monitor. Four files each know a piece of it. Change the rule and you'll miss one.

Race conditions and edge cases the current design cannot express:
- What happens when loop 0 is cleared while slaves are quantized to it? `_clear_loop` sets `_master_loop_established = False` and walks away. The slaves keep `sync=1, playback_sync=1` pointed at a loop that no longer exists.
- `_debounced()` (200 ms) is shared by taps and holds; a legitimate fast double-tap in a performance gets eaten silently.
- `poll_hold` sets `self._pad_down = False` before the note-off arrives, so the eventual note-off does nothing — correct, but only by accident of ordering.

## 6. Test strategy & execution

Six test files exist and they're competently written. The problem is what they test.

`test_apc_footswitch.py::test_master_triggers_playback_after_record` asserts `calls.count("trigger") == 1` — it **encodes the pop-causing redundant trigger as the specification**. The suite locks in the shadow state machine's fiction. It will stay green through every one of the 🔴 bugs above.

Dangerously untested: `SlBenchStateListener` (zero tests — and it's the single point of failure for every slave loop), the registration/re-registration path, thread interleaving between MIDI and OSC callbacks, and anything resembling an integration test against a real or faked SL engine. There is no fake engine. There should be: SL's OSC surface is small enough to stub, and a fake would have caught the bricked-pad path in an afternoon.

## 7. Security & performance

No security surface worth worrying about — localhost UDP, no auth, which is correct for the context. Note that `SlBenchStateListener` binds `LISTEN_HOST` from env and will happily bind `0.0.0.0` if someone sets it; keep it pinned to loopback.

Performance: the 20 writes/sec SD grinder above is the real one. The 2 ms MIDI poll spin is fine. `jack_cpu_load ~15-28%` at 16 loops is genuinely encouraging. The 17-way direct fan-in to `system:playback` is already correctly identified in your own docs as a headroom bug, not a CPU one — that analysis was good work.

## 8. Developer experience

A new dev could not run this in a day. They'd need: the liblo 0.32 patch, the right cwd, three env-configured UDP ports, a JACK graph applied by a script that silently succeeds when it fails, and knowledge of which of two similarly-named entry points is the real one. Against that: `docs/measurements/sooperlooper-eval-2026-08-14.md` is an unusually good document — it records what failed, what was improvised, and what was *not* run. Most teams don't write that down. It's the reason this review could be specific.

---

## Verdict

Your instinct is right, and it's right for a more precise reason than "the code is bad." The plumbing is fine — JACK wiring, OSC transport, the grid layout, the eval methodology all passed B1/B2 on the bench and should be kept. What's broken is one architectural decision in the control layer: **the bench mirrors SooperLooper's state instead of reading it**, and then papers over the resulting drift with retriggers and timer threads. Every documented symptom — green-but-silent loop 0, the wrap pop, dead loop 2, unverifiable transport — is a direct consequence of that one choice, which is why they appeared together the moment grid mode raised the coupling between loops. Three clips free-form worked because free-form has no cross-loop coupling for the fiction to contradict.

So: rewrite the control layer, not the system. One process, SL-authoritative state, LEDs as a pure projection of engine state, zero deferred threads, transport applied once at startup. That's a few hundred lines and it deletes most of what's here. Do **not** rewrite the JACK/OSC/eval layer — it's earning its keep.

## Priority backlog

1. **🔴 Kill every mid-performance `trigger`** — the post-record trigger, `_ensure_master_playing`, and the deferred `_refresh_grid_sync`. Highest-probability cause of the wrap pop, and it's a deletion, so it's testable in one bench pass against the pre-`792b490` baseline.
2. **🔴 Make SL the single source of truth** — remove the `loop == 0` exemption in `sync_from_sl`, handle states 0 and 14, and drive LEDs from engine state only. Fixes green-but-silent.
3. **🔴 Unbrick the slave pads** — time out `awaiting_quantize`, add a polling fallback for `state`, and re-register auto-updates periodically as `sl-hud-monitor.py` already does. Fixes loop 2.
4. **🔴 Lock or single-thread the shared state** — `_master_loop_established` and the per-switch fields are written from three threads. Best fix is to eliminate them (item 2), not to add locks.
5. **🟡 Add a raw MIDI dump mode** before another hour goes into Shift+Stop All. You cannot fix what you cannot observe, and the note constants have never been verified against the hardware.

---

# Audit correction (2026-08-14, adversarial re-verification)

Independent audit of the review above against the code. ~70% of the factual claims
confirmed; the two headline diagnoses and the top-line verdict did not survive.

## Where this review was wrong

**1. The wrap pop (§4 item 1) — category error.** All three mechanisms blamed
(post-record `trigger`, `_ensure_master_playing`, the deferred `_refresh_grid_sync`)
are **one-shot** events. The symptom is a pop at **every loop wrap**
(`docs/measurements/sooperlooper-eval-2026-08-14.md:156`). One-shot events cannot
produce a periodic artifact. The review conflated a transition click with a boundary
click and built its verdict on the conflation.

Two better hypotheses, both missed:

- **`fade_samples` is never set anywhere in the repo** (verified: zero hits for
  `fade_samples|crossfad|zero.?cross`). SL's loop-boundary crossfade is left at
  whatever the default is. Combined with `round = 0.0` on the master
  (`sl_grid_sync.py:36`), a loop end that misses a zero crossing clicks *every wrap*.
  This is the textbook cause of this exact symptom.
- **`playback_sync = 1.0` on slaves** (`sl_grid_sync.py:43`) re-aligns each slave to a
  cycle boundary **every cycle** — the only per-wrap mechanism introduced by the grid
  diff. Note that loop 0's own params are byte-identical between `apply_grid_sync`
  (33-36) and `apply_freeform` (55-58), so for loop 0 the only things that changed at
  `792b490` are global `sync_source`, `eighth_per_cycle`, slave `playback_sync`, and
  the trigger machinery.

*Cheapest next step, no code change:* bench A/B with `sync_source=1` +
`eighth_per_cycle=8` but `playback_sync=0` on slaves.

**2. Dead loop 2 (§4 item 2) — right severity, wrong code path.** The review blamed the
*end*-of-record branch (`apc_footswitch.py:168-169`). The user can never reach it. The
real bug is on the *start*-of-record path and was reproduced:

```
loop 2: -> record (state=idle)   # tap 1
after SL=1: awaiting_quantize True    # WaitStart sets it via QUANTIZE_WAIT (:97-99)
after SL=2: awaiting_quantize True    # Recording — NO BRANCH for state 2 (:91-99)
loop 2: -> tap ignored (quantize wait)   # user can never stop the record
```

`sync_from_sl` handles states 4, 3 and `QUANTIZE_WAIT`, but **not `SL_STATE_RECORDING`
(2)**, so `awaiting_quantize` is never cleared and the pad latches shut *during*
recording. The review caught the missing branches for 0 and 14 but missed the one that
fires on the happy path. **Fix is one line:**
`elif sl_state == SL_STATE_RECORDING: self.awaiting_quantize = False`.

**3. Thread safety (§4 item 3) — overstated.** `_master_loop_established` is a single
bool, GIL-atomic: negligible, not 🔴. The per-switch field races are real but
self-correcting within 100 ms; "permanently inconsistent" is wrong — the permanent
desync is loop 0's, which is an authority problem, not a locking one. Understated in a
different direction: `ThreadingOSCUDPServer` (`sl_bench_listener.py:46`) is
thread-per-datagram, so state updates can be applied **out of order**.

**4. "Rewrite the control layer" — not supported.** The confirmed defects are individually
tiny: loop-2 is one line, loop-0 authority is one revert plus a two-line deletion, the
trigger fix is a deletion. All four ship-blockers in the eval log map to under ~50 lines
across three files. The rewrite thesis stood on the inflated thread-safety severity and
on a pop diagnosis that can't be right. Do the narrow fixes, re-run the B9 ear gate,
*then* decide.

## Where this review was right

- **Loop 0 is unreconciled (§2)** — confirmed and *understated*. Three independent
  exclusions, not one: the `loop == 0` early return (`apc_footswitch.py:87-88`),
  `sl_bench_listener.py:34` iterating `range(1, num_loops)` so loop 0's state is **never
  subscribed to at all**, and `slave_footswitches()` filtering `fs.loop > 0` (`:242-243`).
  `d08663d` deliberately narrowed that registration ("loop 0 is bench-authoritative")
  minutes before the failing ear gate. Highest-value revert in the tree.
- **The test encodes the bug** — `tests/test_apc_footswitch.py:35` asserts
  `trigger` count == 1. The suite will lobby against the fix.
- **No MIDI dump mode** — re-rated *up*. Two of four ship-blockers are "code exists,
  Mitch cannot use it" with no diagnostic path.
- **`|| true` in `wire-jack-graph.sh`** — re-rated *up* to High. "No loop audio → JACK
  graph disconnected" is a logged past failure (eval doc `:140`) that this masks.

## Additional findings the review missed

- **The bench never applies grid sync at startup at all** — verified: the only
  in-process calls are the 350 ms defer (`apc_footswitch.py:49`) and `reset_all_loops`
  (`:274`). Grid config arrives from a separate process (`sl_grid_sync.main`), so loops
  recorded before loop 0 lands run under whatever that process last set.
- **`stop_all_loops` leaves `_master_loop_established = True`** (`:277-290`), so the
  next slave tap calls `_ensure_master_playing` and **loop 0 silently restarts** while
  its LED reads yellow/stopped.
- **`sync_from_sl` only repaints the LED when `state` changes** (`:100-102`), so a pad
  latched in `awaiting_quantize` gives no visual signal at all.

## Corrected priority backlog

| P | Action | Effort |
|---|---|---|
| **P0** | Add the `SL_STATE_RECORDING` (2) branch clearing `awaiting_quantize` | 1 line |
| **P0** | Revert `sl_bench_listener.py:34` to `range(num_loops)`; drop the `loop == 0` early return; put loop 0 in `by_loop` | quick |
| **P1** | Bench A/B `playback_sync=0` on slaves — isolate the pop before writing code | bench only |
| **P1** | Set `fade_samples` explicitly in both sync modes; A/B against the pop | quick + bench |
| **P1** | Handle states 0/14; drive LEDs from `sl_state`, not `self.state` | half-day |
| **P1** | Delete `_ensure_master_playing` + post-record `trigger`; update `test_apc_footswitch.py:35` | quick |
| **P1** | Add `--dump-midi`; verify `0x77`/`0x7A` against the actual APC | quick |
| **P1** | Drop `\|\| true` from `wire-jack-graph.sh`; count failures, exit non-zero | quick |
| **P2** | Re-register auto-updates every 15 s (mirror `sl-hud-monitor.py:130-132`) | quick |
| **P2** | Reset `_master_loop_established` in `stop_all_loops` | quick |
| **P2** | Tests for `SlBenchStateListener` + a fake SL engine | multi-day |
