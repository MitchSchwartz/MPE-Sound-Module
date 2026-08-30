# Grumpy review — clock, grid, phase, tail. Who owns musical time?

**Branch:** `refactor/looper-ownership-2026-08-30` @ `41d8541`
**Dimension:** tempo / grid / phase / bar / wrap / ring-out — musical time and its owner
**Reviewer:** fresh context, read-only. No product code or test was modified.
**Governed by:** `Documents/reviews/CHARTER-looper-ownership-2026-08-30.md`. **Tests are not canon.**

**What I read:** `sl_grid_state.py`, `sl_grid_sync.py`, `tail_phase.py`, `track_gesture.py`,
`loop_model.py`, `loop_model`'s callers, `sooperlooper-apc-bench.py`, `sl_hud_monitor.py`,
`sl_osc_session.py`, `sl_bench_listener.py`, `latency_tap.py`, `slot_runtime.py` (clock paths
only), `slot_surface.py` (wrap/grid wiring only), `looper_songs.py` (grid + tempo paths),
`patch_browser/looper_hud.py`, the four canon documents, `config/mpe.env.example`,
`config/platform/player-env-parity.pi5.env`, `scripts/bootstrap-pi5-looper.sh`, and the
timing tests. I did **not** read the LED compositor, the fader layer, `sl-watchdog.py`,
`sl-health.py`, or SooperLooper's own `engine.cpp` (not present on this laptop — every
engine claim below is sourced to a repo document that says it read the source, and I flag
where two such documents disagree).

I ran `tests/test_sl_grid_state.py test_sl_grid_sync.py test_tail_phase.py
test_tail_integration.py` — **64 passed**. Every defect below is green.

---

## 1. First impressions

This is not vibe-coded. `sl_grid_state.py` is the best-written file in the repo: it names
the wrong model, quotes the player who caught it, states the cost, and encodes the right
one. `tail_phase.py` is close behind. If the whole looper read like these two, this review
would be short.

The problem is that both of them are **models**, and the code that talks to the engine does
not use them. `GridState` knows `cycle_s`, `bars`, `beats_per_cycle` and `eighth_per_cycle`.
The function that actually configures SooperLooper — `establish_grid_clock` — takes `bpm`
and `bars` as loose scalars and recomputes `eighth_per_cycle` from its own module constant.
`GridState.eighth_per_cycle` is read by **exactly one caller in the repository, and it is a
test** (`tests/test_sl_grid_state.py:216`). The invariant "engine cycle == the take" is
proven on a property that production never evaluates.

That is the shape of everything below. The good model is there. It is not wired to the
thing that makes noise.

---

## 2. Who owns tempo? Every store and every writer

### The stores — there are five

| # | Store | Home | Lifetime |
|---|---|---|---|
| 1 | `GridState.bpm` / `.bars` / `.cycle_s` | `sl_grid_state.py:140-146` | bench process, in RAM |
| 2 | The engine's `tempo` + `eighth_per_cycle` | SooperLooper | engine process |
| 3 | `SlOscSession.last["-2:tempo"]` | `sl_osc_session.py:113-118` | bench process, a *cache of #2* |
| 4 | `bpm` in the song manifest | `looper_songs.py:320` | on disk |
| 5 | `DEFAULT_BPM = 120` | `sl_grid_sync.py:27` | import-time constant |

Plus `patch_browser/sl_hud_state.py`'s JSON file, which carries `bpm` copied from #3.

### The writers of the engine's tempo (#2) — five call sites

```
sl_grid_sync.py:196    send("/set", ["tempo", float(bpm)])          # apply_grid_sync, bpm=DEFAULT_BPM
sl_grid_sync.py:235    send("/set", ["tempo", float(bpm)])          # establish_grid_clock
track_gesture.py:966   osc.send_message("/set", ["tempo", ...])     # stop_all_loops, raw
```
reached from:
```
bench:232   on_grid_established   -> establish_grid_clock(bpm, bars=bars)
bench:245   on_phase_reanchor     -> establish_grid_clock(bpm, bars=grid.bars or 1)
bench:321   on_looper_engine_started -> apply_grid_sync()  (tempo = 120!)
bench:323   on_looper_engine_started -> establish_grid_clock(grid.bpm, bars=grid.bars or 1)
looper_songs.py:649    load_song -> establish_grid_clock(send, bpm)   # bars DEFAULTED
track_gesture.py:966   stop_all_loops -> raw /set tempo, bypassing both helpers
```

### Does "re-sending the tempo IS the phase reset" hold at every write site?

`scripts/sooperlooper/README.md` § Clock states the invariant. **It fails in both
directions.**

**Direction A — a tempo write that does not intend a phase reset.**
`sl_grid_sync.py:196`, inside `apply_grid_sync`, unconditionally writes
`tempo = DEFAULT_BPM (120)`. At cold start that is harmless. On `bench:321` —
`on_looper_engine_started`, i.e. every time SooperLooper is restarted mid-session — it
zeroes the engine's phase to 120 BPM, then line 323 zeroes it again at the real tempo, and
**neither call updates `grid.phase_zero_at`.** See §7 P1-1. It also poisons the "is there a
grid" probe: `looper_songs.py:525` decides `grid_active = tempo >= 20.0` by reading the
engine's tempo, which is 120 from boot whether or not a grid was ever established. A song
saved from a genuinely free-form session reloads with the grid switched on at 120 BPM.

**Direction B — a phase reset with no tempo write.**
`slot_runtime.py:399` calls `self._mark_phase_zero()` after launching a clip into silence.
The bench wires that to `lambda: grid.mark_phase_zero(time.monotonic())`
(`sooperlooper-apc-bench.py:365`). No `/set tempo` is sent. So the bench's downbeat moves
and the engine's `_tempo_counter` does not. From that instant the two disagree about where
the bar line is, and nothing reconciles them until the next `establish_grid_clock`. This is
the timing-model spec §4 row 1 ("starting into silence moves the PHASE") implemented on one
side of the wire only.

`stop_all_loops` (`track_gesture.py:960-967`) is the one site that gets it right — it sends
`/set tempo` *and* calls `mark_phase_zero`. It does so by hand-rolling the OSC rather than
calling `establish_grid_clock`, which means it is a fourth writer that a future change to
the establish path will silently miss.

**Verdict: refuted.** The invariant holds at two of five write sites.

### `derive_tempo` is called twice for one grid

`GridState.establish` (`sl_grid_state.py:168`) calls `derive_tempo(loop_len)` and stores
`bpm`/`bars`. `TrackGesture._try_commit_phase_reanchor` (`track_gesture.py:509-513`) calls
`derive_tempo(self.loop_len)` **again**, discards the `bars` it returns
(`bpm, _bars = derived`), and hands only `bpm` to `on_phase_reanchor` — which then takes
`bars` from `grid.bars` (`bench:245`). One `(bpm, bars)` tuple, assembled from two
independent derivations at two different moments against a mutable `self.loop_len`. It
agrees today by luck. `_try_commit_phase_reanchor` should read `self.grid.bpm`; the grid
owns the number.

---

## 3. Who owns phase? One full trace, and where ordering is incidental

### The stores

| Store | Home | Written by |
|---|---|---|
| `GridState.phase_zero_at` (monotonic) | `sl_grid_state.py:135` | 4 sites, below |
| engine `_quarter_counter` / `_tempo_counter` | SooperLooper | any `/set tempo` |
| per-loop `loop_pos` mirror | `track_gesture.py:146,156` | `sync_loop_pos` at 20 ms |
| HUD's beat/bar | `sl_hud_monitor.py:177` | `beat_and_bar_from_tempo(loop_pos, tempo)` |

`mark_phase_zero` writers: `bench:235` (establish), `bench:246` (re-anchor), `bench:365`
(SlotRuntime launch-into-silence), `track_gesture.py:967` (Stop All). Four writers, three
files, no single seam.

### The trace: first take lands → grid establishes → second clip quantizes

| # | Component | What happens | Ordering |
|---|---|---|---|
| 1 | `loop_model.plan_gesture:163-169` | pad down, no grid → `record`, `arm_grid=True` | deterministic |
| 2 | `track_gesture.py:730-731` | `self.grid.arm(self.loop)` — **return value discarded** | see §5 |
| 3 | `plan_gesture:180-195` | pad down again → `overdub` (closes take, starts ring-out at the same sample) | deterministic |
| 4 | `track_gesture.py:289-296` | engine reports `OVERDUBBING` → `_begin_tail()` | engine-timed |
| 5 | — | **grid does NOT establish here.** `_maybe_establish_grid` is gated on `sl_state == SL_STATE_PLAYING` (`:303-304`) and on `sync_loop_len` only while PLAYING (`:341`). OVERDUBBING is neither. | **incidental — see P0-3** |
| 6 | `track_gesture.py:395-410` | ring-out ends (decay/cap/wrap) → `overdub` off | meter-timed, unbounded |
| 7 | `track_gesture.py:303-304` | engine reports `PLAYING` → `_maybe_establish_grid()` | engine-timed |
| 8 | `sl_grid_state.py:163-175` | `establish()` captures `bpm`, `bars`, `cycle_s = loop_len` | deterministic |
| 9 | `track_gesture.py:477-478` | `_on_grid_established(bpm, bars)` → engine phase zeroed **now**, mid-loop | **unconditional — see P1-6** |
| 10 | `bench:235-236` | `grid.mark_phase_zero(time.monotonic())`, then `set_grid_active(active=True)` | deterministic |
| 11 | `track_gesture.py:479-488` | *then* asks `should_defer_phase_anchor(loop_pos, ...)` and arms a second reset | after the fact |
| 12 | `track_gesture.py:359-362` | at the next wrap, `detect_loop_wrap(self._last_loop_pos, pos, ...)` fires the re-anchor | **one loop_pos update late, by design** |
| 13 | `bench:245-246` | second `establish_grid_clock` + second `mark_phase_zero` | deterministic |
| 14 | second clip: `slot_runtime.py:383` | `_session_sounding()` true → `_defer_launch` | deterministic |
| 15 | `slot_runtime.py:418-421` | boundary **snapshotted** from `grid.next_boundary(...)` | **stale if step 13 lands after — see P1-3** |
| 16 | `slot_surface.py:354` → `poll_grid_wait` | fires when `self._now() >= at` | mixes two clocks — §8 |

**Components touching phase on this one path: eight.** `plan_gesture`, `GridState`,
`TrackGesture._maybe_establish_grid`, `TrackGesture._try_commit_phase_reanchor`,
`sl_grid_sync.establish_grid_clock`, three bench closures, `SlotRuntime._defer_launch`,
`SlotSurface.poll_grid_wait`. Ordering between steps 9/10/11 is **incidental** — the code
comment at `:454-460` says the phase reset may defer and the code defers nothing. Ordering
between 13 and 15 is **unguaranteed and racy**. Ordering between 5 and 7 is **engine-timed
and unbounded**.

---

## 4. Code smells — ranked

### 🔴 P0-1 — Loading a song resets the engine's cycle to one bar

```python
# scripts/sooperlooper/looper_songs.py:646-650
bpm = song.bpm
grid_active = song.grid_active
if grid_active:
    establish_grid_clock(send, bpm)          # <-- bars defaults to 1
    set_grid_active(send, num_loops=num_loops, active=True)
```

`establish_grid_clock`'s own docstring (`sl_grid_sync.py:217-222`) says exactly what this
does, in the same words the timing-model spec §1 uses for the bug Mitch caught in review:

> "A 6.939 s first loop read as 4 bars at 138 BPM would give the engine a 1.735 s cycle, and
> clips would join four times inside the loop the player thinks of as one bar."

The save side is why it cannot be fixed at the call: `save_song` stores only `bpm`
(`looper_songs.py:320,570`). `bars` / `cycle_s` are never persisted. `MANIFEST_VERSION = 2`
has no field for them.

**Numbers.** Take = 6.939 s → `derive_tempo` → `(138.35 BPM, 4 bars)` → live session sends
`eighth_per_cycle = 32`, engine cycle = `32 × 30 / 138.35` = **6.939 s** ✓. Save → manifest
holds `bpm: 138.35`. Load → `eighth_per_cycle = 8`, engine cycle = `8 × 30 / 138.35` =
**1.735 s**. Every clip in the reloaded song now quantizes to a boundary that falls four
times inside the loop. Nothing on the surface says so.

Second half of the same bug: `load_song` never touches `GridState`. It runs in the
`looper_songs` OSC probe, not the bench, so after a load the bench's `grid.established` is
whatever it was, `cycle_s` is whatever it was, and `phase_zero_at` is stale.

**Fix direction:** persist `bars` (and `cycle_s`) in the manifest at `MANIFEST_VERSION = 3`,
default `bars = 1` only for v1/v2 manifests, and give `GridState` a `restore(bpm, bars,
cycle_s)` that the load path drives — one owner, one seam.

---

### 🔴 P0-2 — The ring-out cap shrank by the bar count on 2026-08-30. This is the audible one.

```python
# scripts/sooperlooper/tail_phase.py:96-97
if bpm and bpm > 0.0:
    return (60.0 / bpm) * BEATS_PER_BAR, "one bar"
```

The cap is **one bar**. `Documents/specs/looper-timing-model-spec.md` §6, updated
2026-08-30, says:

> **cap** — one **cycle**; a ring-out longer than that is not a ring-out

Before commit `d06fb08` (2026-08-30, "the cycle is the first take") every take was read as
one bar, so bar == cycle and the two readings coincided. `d06fb08` made takes read as
1/2/4/8 bars. Because `bpm = bars × 4 × 60 / loop_len`, it is exactly true that
`bar_s = cycle_s / bars`. **The cap silently divided by the bar count.**

It bites clips 2..N, not the defining take. On the defining take `_begin_tail` runs while
the grid is still unestablished, so `self.grid.bpm` is `None` and `cap_for` falls to the
`loop_len` branch — the full cycle. On every clip after, `grid.bpm` is set:

```python
# scripts/sooperlooper/track_gesture.py:378-380
bpm = self.grid.bpm if self.grid is not None else None
cap, cap_source = cap_for(bpm, loop_len=self.loop_len)
```

**Numbers, from this repo's own measurements.** `tail_phase.py:36-40` records seven takes
measured on the appliance 2026-08-30, decay half-life **0.25–0.48 s**, threshold −30 dB.
Reaching −30 dB takes `log₂(31.6) = 4.98` half-lives = **1.24–2.39 s**.

| Take | Reading | Clip 1 cap | Clip 2+ cap | Slow tail (t½=0.48 s) cut at |
|---|---|---|---|---|
| 4.0 s | 2 bars @ 120 | 4.00 s | **2.00 s** | −26 dB |
| 6.939 s | 4 bars @ 138.35 | 6.94 s | **1.735 s** | **−22 dB** |
| 8.0 s | 4 bars @ 120 | 8.00 s | **2.00 s** | −26 dB |

−22 dB is *louder* than the −20 dB setting Mitch rejected the same day with "possible the
decay is a bit steep" — the finding that produced commit `3febb38` ("take the ring-out to
-30 dB, on measured decay curves"). `d06fb08` landed hours later and gave most of it back
for every clip after the first.

The audible symptom: **the first loop's tail breathes and every later loop's tail is
chopped**, on the same patch and the same gesture. The log will say `ring-out ended on cap`
rather than `on decay` — so the evidence is already being written to `MPE_SL_TAIL_TRACE`,
and nobody has read it since the bar counts changed.

Note that `derive_tempo` returns `bars ≥ 2` for essentially any take over ~3 s (a 4.0 s take
scores 120 BPM/2 bars at `|log(1.2)| = 0.18` against 60 BPM/1 bar at `0.51`), so this is not
an edge case — it is the normal path.

**Fix direction:** `cap_for` should take the **cycle**, not the tempo:
`cap_for(cycle_s, loop_len=...)`. `GridState.cycle_s` already exists and is already the
"never use `bar_s` to place a boundary" rule the model file is built around; the tail is a
boundary.

---

### 🔴 P0-3 — Grid establishment waits out the whole ring-out. Any clip started in that window is free-form forever.

`_maybe_establish_grid` fires only on `PLAYING`:

```python
# scripts/sooperlooper/track_gesture.py:303-304
if sl_state == SL_STATE_PLAYING:
    self._maybe_establish_grid()
```
```python
# scripts/sooperlooper/track_gesture.py:337-344
def sync_loop_len(self, loop_len: float) -> None:
    self.loop_len = float(loop_len)
    if self.sl_state == SL_STATE_PLAYING:
        self._maybe_establish_grid()
```

The defining take closes with `overdub` (`loop_model.py:190-195`), so the engine reports
`OVERDUBBING` — not `PLAYING` — for the entire ring-out. `set_grid_active(active=True)` is
sent from `on_grid_established` and nowhere else (`bench:236`), so for the whole ring-out
**every loop still has `quantize=0, sync=0, mute_quantized=0`** from
`apply_grid_sync`'s closing `set_grid_active(..., active=False)` (`sl_grid_sync.py:202`).

Press a second pad in that window and:

* `GridState.arm(1)` returns **False** (`sl_grid_state.py:149-154`: `_pending` is still loop
  0, because `establish` is what clears it). The return value is discarded at
  `track_gesture.py:730-731`, so nothing notices.
* The plan still logged `"defining the grid (free-form, no count-in)"` — a false log line.
* The take records **with no count-in** (sync=0) and **with no length quantize**
  (quantize=0), so its length is whatever the player played.
* Closing it: `is_defining` is False and `grid_established` is still False, so
  `loop_model.py:208` fails and it falls through to `:219` — a plain `record` stop with
  **no ring-out at all**, plus a quantize wait for a boundary the engine is not producing.
* Loop 0's ring-out then ends, the grid establishes from loop 0, and loop 1 is left as an
  arbitrary-length clip inside an established grid.

**Result: the second loop drifts against the first, permanently, and nothing recovers it.**
Window length = the ring-out, typically 0.5–2.5 s and up to a full cycle. The gesture that
hits it — close take 1 on the downbeat, immediately arm take 2 — is the normal live-looping
gesture.

This is the exact failure shape the timing spec §7 names: *"the grid inferred from clips
existing"*, wearing a new hat — the grid inferred from a loop having reached `PLAYING`.

**Fix direction:** establish the grid the moment the take's length is fixed, which is the
`RECORDING → OVERDUBBING` transition, not `PLAYING`. `loop_len` is already correct there
(overdub does not change length). The `_begin_tail` branch at `:295` is the natural place.

---

### 🟡 P1-1 — Engine restart re-anchors the engine and not the bench

```python
# scripts/sooperlooper-apc-bench.py:318-328
if not grid_active:
    return
print("bench: looper.engine.started — re-applying grid config", flush=True)
apply_grid_sync(_send, num_loops=num_loops)                     # writes tempo=120 -> phase zero
if grid.established and grid.bpm:
    establish_grid_clock(_send, grid.bpm, bars=grid.bars or 1)  # phase zero again
    set_grid_active(_send, num_loops=num_loops, active=True)
```

No `grid.mark_phase_zero(...)`. Compare `on_grid_established` (`:232-236`) and
`on_phase_reanchor` (`:245-246`), which both do. The engine's downbeat is now "restart
time"; the bench's `phase_zero_at` is still whatever it was minutes ago.

**Numbers.** cycle 6.939 s, `phase_zero_at = 40.0`, engine restarts at t = 100.0. Offset =
`(100.0 − 40.0) mod 6.939` = **4.488 s**. The bench's `next_boundary` and the engine's are
4.49 s apart on a 6.94 s cycle — worse than random. `poll_grid_wait` fires a silent-session
launch 4.49 s off the beat; a `mute_quantized` unmute lands 2.45 s later still.

**Fix direction:** one function that writes the clock — engine and `GridState` together —
and let all four sites call it. The `mark_phase_zero` immediately after every
`establish_grid_clock` at three of four sites is a pairing the compiler should be enforcing,
not the reviewer.

---

### 🟡 P1-2 — `_tail` is shared across threads with no lock, and a lost race turns overdub back on

`SlOscSession` runs a `ThreadingOSCUDPServer` (`sl_osc_session.py:87-101`) and dispatches to
`sync_from_sl` / `sync_loop_pos` / `sync_in_peak` from server threads.
`poll_track_gestures` → `poll_tail` runs on the **main** bench thread every ~2 ms
(`sooperlooper-apc-bench.py:527,607,614`). Both write `self._tail`.

Two failures.

**(a) A double `overdub`, which is an ON.**

```python
# scripts/sooperlooper/track_gesture.py:395-405
def _end_tail(self, reason: str) -> None:
    tail = self._tail
    if tail is None:
        return
    self._tail = None                          # <-- not atomic with the check
    if self.sl_state == SL_STATE_OVERDUBBING:
        self._hit("overdub")
```

Interleave: OSC thread reads `tail = self._tail` (non-None) and is preempted. Main thread's
`poll_tail` sees `_tail` still non-None, gets `EXIT_CAP`, runs `_end_tail`, sets `_tail =
None`, sees `sl_state == OVERDUBBING` (the engine's reply is up to 100 ms away —
`BENCH_STATE_MS = 100`) and sends `overdub`. OSC thread resumes and sends `overdub` **again**.
`overdub` is a toggle. The engine re-enters overdub with `self._tail = None`, so nothing
will ever end it.

That is verbatim the failure this module's own comment says it already suffered:

```python
# track_gesture.py:291-294
# a repeated or stale one re-armed the phase after it had already
# ended — and the cap then sent `overdub` with nothing armed to
# turn off, which turns overdub back ON. A loop quietly recording
# the room behind a green pad.
```

Fixed for one door, still open on the other.

**(b) The bench dies.**

```python
# track_gesture.py:444-448
if self._tail is None:
    return
reason = self._tail.tick(self._now())     # <-- re-reads; may now be None
```

`AttributeError: 'NoneType' object has no attribute 'tick'` on the main loop. The only
handler is `except KeyboardInterrupt` at `sooperlooper-apc-bench.py:773-775`. The whole
control surface goes dead.

**Fix direction:** one `threading.Lock` on the gesture, or — better for the charter — the
tail lifecycle owned by a single object with an atomic `take()` that returns the phase and
clears it in one step.

---

### 🟡 P1-3 — A queued launch snapshots the bar line; re-anchoring invalidates it silently

```python
# scripts/sooperlooper/slot_runtime.py:418-421
self._deferred[loop] = (plan, self._now())
boundary = self._grid_boundary()
if boundary is not None:
    self._grid_wait[loop] = boundary
```

`_grid_wait` holds an absolute monotonic time computed from `phase_zero_at` **at press
time**. Any later `mark_phase_zero` — a phase re-anchor at the defining take's wrap
(`bench:246`), a Stop All (`track_gesture.py:967`), a launch into silence (`bench:365`) —
moves the bar line without touching `_grid_wait`. `poll_grid_wait` still fires at the old
number.

**Numbers.** cycle 6.939 s, `phase_zero_at = 100.0`. Press at 103.0 → boundary 106.939,
stored. At 104.0 the defining take's phase re-anchor commits →
`phase_zero_at = 104.0`, downbeats now 104.0, 110.939. The launch still fires at 106.939 —
**2.939 s into the cycle, 42% of the way through the bar.** This is on the second-clip path
the task asked me to trace, in the window right after grid establishment, which is exactly
when a player is queueing clip 2.

**Fix direction:** store the intent ("wait for the next boundary"), not the timestamp, and
resolve it against the grid at poll time.

---

### 🟡 P1-4 — `establish_grid_clock` sends `eighth_per_cycle` before `tempo`, contradicting a measured fix that the repo records as done

```python
# scripts/sooperlooper/sl_grid_sync.py:230-235
Order: smart_eighths off, eighth_per_cycle, then tempo (phase reset via
Engine::set_tempo — verified in engine.cpp).
"""
send("/set", ["smart_eighths", 0.0])
send("/set", ["eighth_per_cycle", float(EIGHTH_PER_CYCLE * max(1, bars))])
send("/set", ["tempo", float(bpm)])
```

`docs/measurements/PI5-LOOPER-SEAM-WRAP.md:327-343`, dated 2026-08-26 and headed
**"Fixed:"**, says the opposite:

> `establish_grid_clock` sent the cycle **before** the tempo, so it set one bar and the
> engine immediately doubled it back to two. Turning `smart_eighths` off does not prevent
> that rewrite. […] Fix: assert `eighth_per_cycle` **after** the tempo. `apply_grid_sync`
> already had the correct order; only `establish_grid_clock` was inverted. **The existing
> test pinned the inverted sequence while its own docstring stated the opposite intent, so
> the suite was green on the bug.**

`git log -L 212,240:scripts/sooperlooper/sl_grid_sync.py` shows the order has been
eighth-then-tempo since `5e8d222` and was never changed. `apply_grid_sync` does have the
other order (`:196` tempo, `:198` eighth_per_cycle). And the test is still there, still
pinning the inverted sequence, still green:

```python
# tests/test_sl_grid_sync.py:64-79
def test_establish_grid_clock_disables_smart_eighths_before_tempo(self) -> None:
    """Sub-60 BPM first takes must not double the cycle to two bars."""
    ...
    self.assertEqual(sent, [
        ("/set", ["smart_eighths", 0.0]),
        ("/set", ["eighth_per_cycle", 8.0]),
        ("/set", ["tempo", 30.0]),
    ])
```

So either the fix was written up and never landed, or it landed and was reverted without a
note. Either way the measurement doc is lying about the code, and the test is doing exactly
what the doc accused it of.

Note the two canon sources disagree about the mechanism: `DECISIONS.md` 2026-08-15 says
`smart_eighths` is what doubles below 60 BPM and disabling it is the fix; the 2026-08-26
measurement says disabling it does **not** prevent the rewrite. Later date wins (charter §2),
so the ordering matters. This is now reachable again after `d06fb08`: `MAX_BARS = 8` caps the
fit, so a 40 s first take scores `bars=8 → 48 BPM` (`|log(0.48)| = 0.73`) against
`bars=4 → 24 BPM` (`1.43`) — **48 BPM, under 60**, and the cycle doubles to 80 s with nothing
re-asserting it.

**Fix direction:** decide the order once from `engine.cpp` (the rule from `DECISIONS.md`
2026-08-15: *read the engine before changing a parameter*), make both functions use the same
helper, and rewrite the test to assert the reason rather than the sequence.

---

### 🟡 P1-5 — The phase reset does not actually defer, and the docstring says it does

```python
# scripts/sooperlooper/track_gesture.py:454-460
"""The defining take just landed — grid immediately, phase maybe at wrap.

Grid existence (tempo capture, quantize on for later clips) must land
the moment the take saves. Only the *phase reset* inside
establish_grid_clock may defer: a late PLAYING report mid-bar would
otherwise shove clip 2+ early.
"""
```
```python
# :477-488
if self._on_grid_established is not None:
    self._on_grid_established(bpm, bars)          # <-- contains the phase reset. Unconditional.
if should_defer_phase_anchor(...):
    self._phase_reanchor_at = time.monotonic()
    ...
```

The phase reset is inside `establish_grid_clock`, which `_on_grid_established` calls at
`bench:232`, before the defer question is asked. Nothing defers. What the code actually does
is *reset the phase to a moment it has already decided is wrong*, arm a second reset, and
sit with a knowingly-wrong bar line until the next wrap — with `set_grid_active(active=True)`
already sent at `bench:236`, so the engine is quantizing to it.

**Numbers.** With OSC arm latency of 65–139 ms (the figure `sl_grid_sync.py:36-37` cites from
SR&ED §3 U11) plus the ring-out, `loop_pos` at establishment is typically 0.7–0.8 s into a
6.939 s cycle. Any clip launched in the window before the wrap lands ~0.75 s — about 1.7
beats at 138 BPM — off the first loop's downbeat.

**Fix direction:** split `establish_grid_clock` into `set_cycle(bpm, bars)` and
`reset_phase()`. Send the first immediately; send the second only when the defer check says
now, and only ever paired with `mark_phase_zero`.

---

## 5. `TrackGesture` mutating a shared `GridState` — is that the right seam?

**No, and the race is already silent today.**

```python
# scripts/sooperlooper/track_gesture.py:730-731
if plan.arm_grid and self.grid is not None:
    self.grid.arm(self.loop)
```

`arm()` returns `bool` (`sl_grid_state.py:149-154`) and the only caller in the repository
throws it away. Every one of the 15 gestures holds the **same** `GridState` instance —
`build_track_gestures` passes one object to all of them (`:846-859`) — so a per-track object
owns a session-scoped singleton, and `reset_all_loops` has to `break` after the first
gesture (`:922-927`) because it knows they are all the same object.

**Two tracks racing.** Presses are serialised through the single MIDI event loop, so there
is no data race — the failure is worse than a race, it is a *silent loss*:

1. Track 0 pad down → `arm(0)` → True. `_pending = 0`.
2. Track 1 pad down before track 0's take lands → plan logs
   `"defining the grid (free-form, no count-in)"` → `arm(1)` → **False, discarded**.
3. Track 1 records with `sync=0, quantize=0` — genuinely free-form, arbitrary length.
4. Track 0 lands, grid establishes from track 0.
5. Track 1's `_maybe_establish_grid` returns at `:462` (`not is_pending`). Its length is
   never snapped to the cycle and never will be.

Track 1 is an off-grid clip inside an on-grid session, its LED is indistinguishable from a
grid clip, and the only trace is a log line that says the opposite of what happened. This is
P0-3 by another route, and it is why the seam is wrong: **a per-track object cannot enforce
a session-wide singleton's precondition, because it has no way to tell the player "no".**

**Fix direction:** the grid belongs to the session, not to a track. A `LooperSession` (or the
bench's existing owner role, made explicit) should own `GridState` and expose
`request_defining_take(loop) -> bool`; `TrackGesture` asks and *acts on the answer* — refuse
the arm, or record knowing it is an ordinary clip. Either is honest; discarding the bool is
not.

---

## 6. The tail's constants — one concept, two files? Neither.

The question assumed the two homes are either one concept split or two concepts badly named.
The answer is **six of them are fossils and nothing reads them.**

```python
# scripts/sooperlooper/sl_grid_sync.py:45-54
TAIL_SEAM_RATIO             = float(os.environ.get("MPE_SL_TAIL_SEAM_RATIO", "0.85"))
TAIL_SEAM_END_MAX_S         = float(os.environ.get("MPE_SL_TAIL_SEAM_END_MS", "500")) / 1000.0
TAIL_MIN_OVERDUB_S          = float(os.environ.get("MPE_SL_TAIL_MIN_OVERDUB_MS", "150")) / 1000.0
TAIL_WELD_INPUT_GAIN        = float(os.environ.get("MPE_SL_TAIL_INPUT_GAIN", "0.35"))
TAIL_WELD_FADE_SAMPLES      = int(os.environ.get("MPE_SL_TAIL_FADE_SAMPLES", "512"))
TAIL_WELD_RESTORE_INPUT_GAIN= float(os.environ.get("MPE_SL_TAIL_RESTORE_INPUT_GAIN", "1.0"))
```

`grep -rn` across `scripts/`, `tests/`, `patch_browser/`: **each name appears exactly once,
at its own definition.** Not imported, not used inside `sl_grid_sync` itself. Same for
`COUNT_IN` (`sl_grid_sync.py:99` — `set_count_in` takes the flag as a parameter) and
`anchor_phase` (`:238-240` — zero callers). They are the residue of the deleted seam-weld
pipeline that `sl_grid_sync.py:35-38` says was removed.

It gets worse outside the code:

| Variable | Status | Still asserted by |
|---|---|---|
| `MPE_SL_TAIL_CAPTURE` | **dead** | `config/platform/player-env-parity.pi5.env:25`, `config/mpe.env.example:227`, `DECISIONS.md` 2026-08-19 ("Defaults: …=1") |
| `MPE_SL_SEAM_WELD` | **dead** | `player-env-parity.pi5.env:26`, `mpe.env.example:233`, `DECISIONS.md` 2026-08-19 |
| `MPE_SL_SEAM_MERGE_SAMPLES` | **dead** | `player-env-parity.pi5.env:28`, `mpe.env.example:238` |
| `MPE_SL_TAIL_MAX_MS` | **dead** | `mpe.env.example:230`, seam spec §Configuration |
| `MPE_SL_TAIL_THRESH` | **dead** (superseded by `_RATIO`) | `mpe.env.example:228` |
| `MPE_SL_MIN_TAIL_WAV_BYTES` | **dead** | `mpe.env.example:237` |
| `MPE_SL_TAIL_PEAK_MS` | **renamed** to `MPE_SL_BENCH_PEAK_MS` | `mpe.env.example:231` |
| `MPE_SL_TAIL_RATIO`, `_FLOOR`, `_SILENT_MS`, `_CAP_MS`, `_TRACE`, `MPE_SL_RING_OUT` | **live** | *not in `mpe.env.example` at all* |

`config/mpe.env.example`'s tail block is **100% dead and 0% live**. And
`scripts/bootstrap-pi5-looper.sh:42-44` actively *removes* `MPE_SL_TAIL_CAPTURE` and
`MPE_SL_SEAM_WELD` from the Pi's env while `player-env-parity.pi5.env` — the file whose job
is to state the player's environment — still sets both to 1. Two config files disagreeing
about the same key, on an appliance, with one of them named "parity".

Set `MPE_SL_TAIL_INPUT_GAIN=0.1` to tame a loud tail weld and **nothing happens, silently.**
That is the charter's flagship complaint, in the tail.

### Who owns the tail lifecycle?

Split, and the two halves do not agree.

| | `TailPhase` | `TrackGesture` |
|---|---|---|
| owns | the decision (`peak`, `tick`, `exit_level`) | the OSC, the cap value, the trace, the callback |
| exits | `EXIT_DECAY`, `EXIT_SILENT`, `EXIT_CAP` | `_end_tail(reason)`, `_abandon_tail()` |

`TrackGesture` exit paths:

| Path | Trigger | Sends `overdub`? | Fires `on_tail_change`? | Traces? |
|---|---|---|---|---|
| `_end_tail(EXIT_DECAY)` | `sync_in_peak` (`:431-433`) | yes | yes | yes |
| `_end_tail(EXIT_CAP/SILENT)` | `poll_tail` (`:446-448`) | yes | yes | yes |
| `_end_tail(EXIT_WRAP)` | `sync_loop_pos` (`:356`) | yes | yes | yes |
| `_abandon_tail()` | `sync_from_sl`, `prev_sl == OVERDUBBING` (`:297-301`) | **no** | yes | **no** |

**They do not agree.** `EXIT_ABANDONED` is defined in `tail_phase.py:77` and never used
anywhere. A tail that ends because the player pressed the pad — the most common ending
after decay — writes **no trace row**, so the instrument that `tail_phase.py:42-51` says
turned the thresholds from guesses into measurements is blind to an entire exit class. Any
histogram of exit reasons drawn from that CSV is missing the pad-press population, and the
selection bias points the same way as P0-2: the takes that got cut short are the ones that
do not appear.

**Fix direction:** `TailPhase` should own the lifecycle end to end — one object, one
`finish(reason)` that traces every ending including `abandoned`, and `TrackGesture` reduced
to "give it peaks and ticks, execute what it returns". The cap belongs to it too, computed
from `cycle_s` (P0-2).

---

## 7. Environment-variable sprawl

**114 `os.environ.get("MPE_*")` reads across `scripts/sooperlooper/` +
`scripts/sooperlooper-apc-bench.py`, over 92 distinct names.** Roughly 82 are module-level
(import-time) and 32 are inside functions.

Read by more than one module:

| Variable | Modules | Consistent defaults? |
|---|---|---|
| `MPE_SL_OSC_HOST` | 10 | yes (`127.0.0.1`) |
| `MPE_SL_OSC_PORT` | 10 | yes (`9951`) |
| `MPE_SL_SYNC_MODE` | 2 — `bench:118`, `sl_grid_sync:263` | yes (`grid`) |
| `MPE_SL_SCRATCH_LOOP` | 2 — `looper_songs:27`, `sl_hud_monitor:35` | yes (`-1`) |
| `MPE_SL_LOOPS` | 2 — `bench:113`, `sl_limits:56` | **the charter's flagship row** |
| `MPE_SL_JACK_CLIENT` | 2 — `sl-health:168`, `sl-watchdog:66` | yes |

No *timing* variable is read by two modules, which is genuinely good. The sprawl in this
dimension is a different shape: **one concept, several env names, none of which agree.**

* **"one bar"** has five homes. `MPE_LOOPER_BEATS_PER_BAR` (`sl_grid_state.py:24`, live and
  configurable); `BEATS_PER_BAR = 4` hardcoded in `tail_phase.py:84`; `beats_per_bar: int = 4`
  in `sl_hud_monitor.py:56`; `4 * 60.0 / float(tempo)` inline at `sl_hud_monitor.py:173`;
  `DEFAULT_BEATS_PER_BAR = 4` in `patch_browser/looper_hud.py:16`. Set
  `MPE_LOOPER_BEATS_PER_BAR=3` and four of the five keep counting in 4.
* **"seconds per bar"** has three implementations: `GridState.bar_s`,
  `tail_phase.bar_seconds` (production-dead — only `tests/test_tail_phase.py` calls it), and
  `patch_browser/looper_hud.bar_seconds`.
* **`eighth_per_cycle`** has two: `MPE_LOOPER_EIGHTH_PER_CYCLE × max(1, bars)` at
  `sl_grid_sync.py:234` (the production path) and `GridState.eighth_per_cycle =
  beats_per_cycle * 2` at `sl_grid_state.py:265-273` (**read only by
  `tests/test_sl_grid_state.py:216`**). They derive from *different env vars*. Change either
  alone and the test that guards "the engine cycle matches the bench boundary" keeps passing
  while production diverges — the charter's "a test whose failure would not have caught the
  bug it is named for".

**Does anything depend on import-time reads being import-time?** No — I found no code that
mutates `os.environ` after start expecting an effect, and `apply_loop_latency`
(`sl_grid_sync.py:153-154`) is the one timing-relevant reader that goes to the environment
per call, so `MPE_SL_INPUT_LATENCY` and `MPE_SL_AUTOSET_LATENCY` *are* live-editable while
everything around them is not. That inconsistency is the finding: two knobs in the same file
behave differently and nothing says which.

---

## 8. How many notions of "now"?

**Six.**

1. `time.monotonic()` — the bench loop, `GridState.phase_zero_at`, `SlotRuntime._now`,
   `LatencyTapClient`, and 20 of the 24 clock reads in `track_gesture.py`.
2. `TrackGesture._now` — the *injected* clock, used at exactly four sites
   (`:380, 408, 431, 446`), all of them the tail.
3. `time.time()` — wall clock. `track_gesture.log()` (`:81`), `sl_hud_monitor.poll()`
   (`:212-222`).
4. **Engine loop position** — `loop_pos`, seconds within a loop, arriving at 20 ms
   (`BENCH_LOOP_POS_MS`).
5. **Engine grid phase** — `_quarter_counter` / `_tempo_counter`. Never observable; inferred
   entirely from "we last sent `/set tempo` at time X".
6. **Sample counts** — `fade_samples`, `input_latency`, `trigger_latency` (`sl_grid_sync.py`),
   `MPE_SL_SEAM_MERGE_SAMPLES` (dead). Never converted to seconds, and the bench never
   subtracts `input_latency` when it computes a boundary.

### Mixed in one comparison — two sites

**(a) `slot_runtime.poll_grid_wait`.**

```python
# scripts/sooperlooper/slot_runtime.py:289-290
now = self._now()                                   # injectable, default time.monotonic
due = [t for t, at in self._grid_wait.items() if now >= at]
```
`at` came from `_grid_boundary()`, which the bench wires as
`lambda: grid.next_boundary(time.monotonic())` (`sooperlooper-apc-bench.py:361`) — **hardcoded
`time.monotonic`, not `SlotRuntime._now`.** Production is fine because they are the same
function; any test that injects a fake clock into `SlotRuntime` compares fake-now against
real-monotonic, and the comparison is either always-true or never-true. Same for
`mark_phase_zero` at `:365`.

**(b) `sl_hud_monitor.poll`.**

```python
# scripts/sooperlooper/sl_hud_monitor.py:204-222
now_mono = time.monotonic()
if now_mono - self._last_health_sample >= WRITE_INTERVAL_S:   # monotonic interval
...
now = time.time()
if (... and (now - self._last_write) < WRITE_INTERVAL_S):     # wall-clock interval
```
The same `WRITE_INTERVAL_S` applied to two different clocks four lines apart. The Pi 5 has no
RTC, so the wall clock steps at boot; `_last_write` from before the step is far in the
future and the second condition goes permanently true. It is masked because `health_fresh`
(monotonic) forces a write every 0.5 s — a bug held shut by an unrelated branch.

### And the "one boundary signal" claim is not true

```python
# track_gesture.py:141-144
#: Called at each loop wrap. This is the ONE boundary signal in the
#: bench: the same detector that ends the ring-out overdub also
#: releases a queued slot switch, so the two cannot disagree about
#: where the bar line is.
```
```python
# track_gesture.py:353-362
if self._loop_pos_seen and detect_loop_wrap(self.loop_pos, pos, self.loop_len):
    self._end_tail(EXIT_WRAP)
    if self._on_wrap is not None:
        self._on_wrap()
if self._loop_pos_seen and detect_loop_wrap(self._last_loop_pos, pos, self.loop_len):
    self._try_commit_phase_reanchor(force_wrap=True)
```
Two detectors, two different `prev` values, ~20 ms apart. They *do* disagree, by exactly one
`loop_pos` update, and the phase re-anchor is the one that gets the late answer — so the
grid's downbeat is systematically ~20–40 ms behind the defining take's sample 0, forever.

Separately, `detect_loop_wrap` needs `loop_pos <= anchor_max_s * 3.0` = **45 ms**
(`sl_grid_sync.py:90`). Updates arrive every 20 ms **over UDP**. Lose two datagrams and the
wrap is missed entirely: `_end_tail(EXIT_WRAP)` never fires, `_on_wrap` never fires, and the
phase re-anchor falls through to the `GRID_ANCHOR_FALLBACK_CYCLES = 1.1` path
(`track_gesture.py:499-506`), which anchors the phase **1.1 cycles later at an arbitrary
point** and logs `!! phase re-anchor fallback`. That is a phase error of up to a full cycle
from two dropped packets.

---

## 9. Free-running correctness — does the grid outlive its clip?

**Yes for the model. No for the engine. Half for the bench.**

`GridState` is correct and well argued. `note_loop_content` returns `False`
unconditionally (`sl_grid_state.py:207`) with a first-rate explanation quoting Mitch, and
`reset()` is the only clearer (`:290-300`). Delete the defining clip and `bpm`, `bars`,
`cycle_s`, `phase_zero_at` all survive. `tests/test_sl_grid_state.py:144-175` covers it
properly. Commit `b4446a3`'s claim — "the grid owns the clock, and outlives the clips" —
**holds at the model layer**.

Three problems around it.

**(a) Dead code that still says the old rule.** Because `note_loop_content` can never return
`True`, this branch is unreachable:

```python
# track_gesture.py:306-311
if self.grid is not None:
    if self.grid.note_loop_content(self.loop, sl_state != SL_STATE_OFF):
        log(f"loop {self.loop}: last clip cleared — grid dropped, "
            f"next take defines a new one")
        if self._on_grid_dropped is not None:
            self._on_grid_dropped()
```
`on_grid_dropped` is now reachable only from `reset_all_loops` (`:925-926`). The dead branch
still advertises the "no clips, no grid" rule that `DECISIONS.md` 2026-08-15 stated and the
2026-08-30 timing spec §3 repealed. `_occupied` is maintained and read by nobody.

**(b) The grid's clock is dead code in the shipping default.** `GridState.next_boundary` has
exactly one production caller — `sooperlooper-apc-bench.py:361`, inside the
`if multigrid:` block — and `MPE_SL_MULTIGRID` defaults to `0` (`bench:273`). So in the mode
Mitch actually plays, `phase_zero_at` / `cycle_s` / `next_boundary` are **write-only**:
maintained on every establish, re-anchor and Stop All, consulted never. All quantization is
SL's. Which means P1-1 and P1-3 can be wrong for weeks without a symptom, and become
load-bearing the moment multigrid flips on. That is a bad place to leave a clock.

**(c) The HUD has a fourth notion of phase and it is unrelated to the grid.**

```python
# sl_hud_monitor.py:174-181
ref, phrase_len, bars = self._phrase_reference(bar_span)   # longest PLAYING loop
pos = float(self._sl.cached("loop_pos", ref) or 0.0)
beat, bar = beat_and_bar_from_tempo(pos, float(tempo))
```
Beat 1 is wherever the longest playing loop's playhead is zero. Stop that loop, start a
longer one, and the displayed downbeat jumps to a different clip's head. `GridState` is in
another process and the HUD has never heard of it. Meanwhile the `KNOWN ISSUE` docstring at
`:146-168` is now **stale canon**: it argues from "`derive_tempo()` already returns bars=1
for the defining take", which stopped being true at `d06fb08`. Its parting advice — "do NOT
try to correct it by adjusting the engine cycle — re-asserting `eighth_per_cycle` after the
tempo was tried on 2026-08-26 and made the readout THREE bars" — is a direct instruction not
to do the thing P1-4's measurement doc says was the fix. One of those two paragraphs is
wrong and both are load-bearing.

---

## 10. Smaller things worth a line each

| | |
|---|---|
| 🟡 | **`sync_in_peak` is defined twice** — `track_gesture.py:248` and `:427`. Python keeps the second. `self._in_peak` and `self._in_peak_seen` (`:181-182`) are therefore never written and never read. Delete the first definition and both fields. |
| 🟢 | `self._loop_pos_at = time.monotonic()` (`:365`) — written every 20 ms, read nowhere. |
| 🟢 | `EXIT_ABANDONED` (`tail_phase.py:77`) — defined, never used. See §6. |
| 🟢 | `anchor_phase` (`sl_grid_sync.py:238-240`) — zero callers, and if anyone did call it, it forwards with `bars=1` and would shrink a multi-bar cycle to a quarter. Delete it before someone finds it. |
| 🟢 | `COUNT_IN` (`sl_grid_sync.py:99`) and `set_count_in` (`:205-209`, "deprecated alias") — dead. |
| 🟢 | `TrackGesture._now` is documented as fixing exactly the problem it still has: *"a phase whose clock is only injectable at some call sites is one a test can drive halfway"* (`:152-154`). It covers 4 of 24 reads. `_phase_reanchor_at` (`:482`, `:500`) — the fallback that decides a phase anchor — is on raw `time.monotonic()`. |
| 🟢 | Stale log lines that will mislead the next debugger: `bench:239` prints `cycle=1 bar` for every grid regardless of `bars`; `bench:325` prints `1-bar cycle` on restore. Both were true before `d06fb08`. |
| 🟢 | `track_gesture.py:740` sets `self._pending_since = None` while every other site sets a float; safe only because `_expire_pending` returns early on `_pending is None`. One reordering away from a `TypeError` in the pad path. |
| 🟢 | `sl_grid_sync.py:136-141` explains `mute_quantized` as "clips are stopped by MUTING and launched by unmuting, and SL defers the unmute to the boundary". But `loop_model.py:248` launches with `("pause_off", "trigger")` and `slot_runtime.py:65` with the same pair — **not** `mute_off`. Either the comment or the launch verb is wrong, and which one decides whether a launch is quantized at all. Worth an engine read before anyone touches it. |

---

## 11. What's good — say it plainly

* `sl_grid_state.py` is the standard `sl_limits.py` set, met. The `cycle` / `bar count` /
  `BPM` separation, `beats_per_cycle` named as the real free variable, `display_bpm` as the
  only rounding site, and `note_loop_content` returning `False` with the reason attached —
  that is the module the charter is asking for.
* `TailPhase` is genuinely well designed: pure, injectable, self-calibrating to each take's
  own peak, with the measured evidence for every constant in the docstring. The `saw_loud`
  settle is subtle and correct.
* `loop_model.plan_gesture` being pure functions over engine state, with `pending` explicitly
  *not* truth, is the right answer to the "mirrored state" bug class and it is holding.
* `cap_for` returning `(value, source)` so the log cannot misattribute its own number is a
  small thing that shows someone had been burned and fixed the class, not the instance.
* `docs/measurements/PI5-LOOPER-SEAM-WRAP.md` and `DECISIONS.md` are the most valuable files
  here. P1-4 exists only because I could compare the code against a measurement someone
  bothered to write down.

---

## Verdict

The model layer is excellent and the wire layer has not caught up with it. Every finding
above is one of two shapes: **a value the model owns that the engine path recomputes for
itself** (tempo, `eighth_per_cycle`, the tail cap, bar length) or **a phase write that moves
one of the two clocks and not the other**. Fix those two shapes and P0-1, P0-2, P1-1, P1-3,
P1-4 and P1-5 close together, because they are the same bug six times.

The single most alarming thing is not any individual defect — it is that **all 64 timing
tests pass**, one of them pinning the exact byte sequence that a measurement in this repo
identifies as the bug, and another proving the central engine-cycle invariant against a
property no production code evaluates. The charter says a green suite means the code does
what an earlier session wrote down. That is precisely what happened here.

## Priority backlog

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

## Timing ownership map

| Concern | Current owners | Writers (file:line) | Recommended single owner |
|---|---|---|---|
| **tempo (bpm)** | `GridState.bpm`, engine `tempo`, `SlOscSession.last`, song manifest, `DEFAULT_BPM` | `sl_grid_state.py:170` · `sl_grid_sync.py:196` (apply_grid_sync, 120) · `:235` (establish_grid_clock) · `track_gesture.py:966` (raw, Stop All) · `looper_songs.py:649` (bars lost) · `bench:232,245,321,323` | **`GridState`.** One `LooperClock.set(bpm, bars)` writes engine *and* model together; no other module touches `/set tempo`. Delete `DEFAULT_BPM`'s unconditional write. |
| **bar count / `eighth_per_cycle`** | `GridState.bars` + `.eighth_per_cycle` (test-only) vs `sl_grid_sync.EIGHTH_PER_CYCLE × bars` | `sl_grid_state.py:170,265` · `sl_grid_sync.py:198,234` | **`GridState`.** `establish_grid_clock` should send `grid.eighth_per_cycle`, not recompute it. One env var (`MPE_LOOPER_BEATS_PER_BAR`), not two. |
| **phase (downbeat)** | `GridState.phase_zero_at`, engine `_tempo_counter`, per-loop `loop_pos`, HUD's reference loop | `bench:235,246,365` · `track_gesture.py:967` (the four `mark_phase_zero` sites) · every `/set tempo` above, implicitly | **`GridState`.** `reset_phase()` must be the only way to move it, must send the engine's phase reset and set `phase_zero_at` in the same call, and must be separable from `set(bpm, bars)`. |
| **bar length (`bar_s`)** | `GridState.bar_s`, `tail_phase.bar_seconds`, `patch_browser/looper_hud.bar_seconds`, `sl_hud_monitor:173` inline, `tail_phase.BEATS_PER_BAR` | 5 definitions, 5 files | **`GridState.bar_s`**, display only. Delete `tail_phase.bar_seconds`; the HUD reads the grid, not the engine's tempo. |
| **cycle (the quantize unit)** | `GridState.cycle_s`; engine's own cycle from `eighth_per_cycle × 30 / bpm`; `TrackGesture.loop_len` | `sl_grid_state.py:171` · `sl_grid_sync.py:234-235` (indirect) · `track_gesture.py:338` | **`GridState.cycle_s`.** Every boundary and the tail cap read it. Assert `cycle_s == engine cycle` in *production*, not only in a test. |
| **wrap detection** | `TrackGesture.sync_loop_pos` — **two** detectors with different `prev` | `track_gesture.py:353-362` | **One** detector in `TrackGesture`, feeding both the tail and the re-anchor. If the ring-out needs a fresher position than the re-anchor, say so at the call, not by duplicating the predicate. |
| **queued-launch boundary** | `SlotRuntime._grid_wait` (snapshot) + `GridState.next_boundary` | `slot_runtime.py:418-421` · `bench:361` | **`GridState`.** Store "waiting for the next boundary"; resolve at poll time so a re-anchor moves it. |
| **tail lifecycle** | `TailPhase` (decision) + `TrackGesture` (`_begin`/`_end`/`_abandon`/`poll`) — 4 exits, only 3 trace | `track_gesture.py:372-448` · `tail_phase.py:143-187` | **`TailPhase`.** One `finish(reason)` covering `abandoned`; cap computed from `cycle_s`; `TrackGesture` executes, never decides. |
| **tail constants** | `tail_phase.py` (5 live) + `sl_grid_sync.py` (6 dead) + `mpe.env.example` (7 dead, 0 live) + `player-env-parity.pi5.env` (3 dead) | see §6 | **`tail_phase.py`**, sole home. Delete the six dead constants and `COUNT_IN`; make `mpe.env.example` list the live ones; reconcile `player-env-parity.pi5.env` against `bootstrap-pi5-looper.sh:42-44`. |
| **latency (samples)** | `sl_grid_sync.apply_loop_latency` (env per call), engine `autoset_latency` | `sl_grid_sync.py:153-171` | **`sl_grid_sync`** is fine as owner. But it is the only timing knob read per-call while everything around it is import-time — pick one policy and document it. |
| **"now"** | `time.monotonic` (×20 in `track_gesture` alone), `TrackGesture._now` (×4), `time.time` (×2), `loop_pos`, engine phase, sample counts | see §8 | **One injected clock per object, used everywhere in that object.** `SlotRuntime._now` and the bench's `grid_boundary` lambda must be the same function. Wall clock only for log text and `updated_at`. |
