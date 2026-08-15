# Looper grid clock — externalise the grid from clip 0

**Issue:** untracked
**Status:** **RESOLVED and shipped** on `yolo/looper-transport-clock`.
Canon is `DECISIONS.md` 2026-08-15 "Looper grid clock: RESOLVED".
**Last updated:** 2026-08-15 (America/Toronto)

> **⚠ §C.2, §D.1, §E and §G are SUPERSEDED.** They plan a JACK transport
> timebase master. That was never needed: SL's **internal tempo** provides the
> grid, and `Engine::set_tempo` provides the phase. Read §K for what was
> actually built. The task table in §E does **not** execute.

**What shipped (§K has the detail):**

| Concern | Answer |
|---|---|
| Clock | SL internal tempo, `sync_source = -3`. No extra process, nothing on the RT thread |
| Tempo | the first take's length — it *is* one bar, by definition |
| Unit | the first take; `eighth_per_cycle` sized to it |
| Phase | `set_tempo` zeroes the phase counters (engine.cpp) — re-sending tempo is the reset |
| Clip 0 | ordinary. Defines the tempo once, then deletable like any other |
| Grid lifetime | no clips, no grid — clearing the last clip drops it |

**Register:** everything below is a working hypothesis unless labelled
**measured**. The §B regression analysis and §I review remain accurate history.

---

## Problem statement

The grid is currently defined by clip 0's audio. `sl_grid_sync.py:27` sets
`sync_source` to the 1-indexed master loop, so every quantised slave derives its
cycle boundaries from loop 0 being alive and playing. Three consequences, all
**measured**:

1. **Deleting clip 0 breaks the grid.** Slaves keep `sync = 1` and
   `playback_sync = 1` (`sl_grid_sync.py:42-43`) pointed at a loop that no longer
   exists. `_clear_loop` (`apc_footswitch.py:136-145` at `e6d8ba6`) sends
   `undo_all` and nothing else.
2. **Clip 0 is not a clip.** It carries a second, hidden responsibility, so the
   UI has to special-case it and the user has to know not to delete it. Every
   `if self.loop == 0` branch in the control layer exists to serve this.
3. **The control layer pokes the master's transport to keep the grid alive.**
   `_ensure_master_playing` (`apc_footswitch.py:31-35`) sends `/sl/0/hit trigger`
   to make sure boundaries keep arriving. This is the machinery that grew into
   the wrap-pop regression (§B).

**Product requirement (Mitch, 2026-08-14):** *deleting clip 0 must be deleting a
clip like any other.* Not "a no-op for the clock" — an ordinary clip deletion,
indistinguishable from deleting clip 7, in behaviour and in code.

That requirement is the whole spec. It is not satisfiable while clip 0 is the
clock, so the clock has to move.

## §A — What is in scope

**In scope:** where the grid comes from, who owns tempo and bar phase, and the
control-layer state that follows from that.

**Out of scope — do not touch:** the JACK graph topology and fail-open wiring
(`wire-jack-graph.sh`, B1/B2 **pass**), the APC pad↔loop mapping
(`apc_grid.py`), the eval methodology, and the SooperLooper build/liblo patch.
These are working and independently validated.

**Explicitly deferred:** B8 persistence (`save_loop` writes nothing —
eval doc `:88`) and the mix-bus/limiter headroom work (`DECISIONS.md`). Neither
blocks this, and this does not fix either.

## §B — What we already know about the regression

**Measured, 2026-08-14:** the working tree is byte-identical to `e6d8ba6` for
all looper scripts (`git diff --stat e6d8ba6 -- scripts/sooperlooper/
scripts/sooperlooper-apc-bench.py` → empty). Mitch reports the wrap pop is
**absent** at this state and was **introduced by the most recent change**. The
pop therefore originates in `8d7a426`..`d08663d`, four commits.

What that range added, relative to the pop-free baseline:

| Commit | Added | Suspect |
|---|---|---|
| `8d7a426` | Internal master clock: `sync_source = -3` + BPM derived from `cycle_len` | **High** — see §C.1 |
| `d08663d` | Explicit `self._hit("trigger")` after loop-0 record ends | Medium — one-shot |
| `d08663d` | 350 ms deferred `_refresh_grid_sync` + a second `trigger` | Medium — one-shot |
| `d08663d` | Loop 0 desubscribed from state updates (`range(1, num_loops)`) | Not a pop cause; is the green-but-silent cause |

**Hypothesis:** the recurring component of the pop is `8d7a426`'s derived-tempo
grid (§C.1), and the one-shot clicks are `d08663d`'s triggers. Both are deleted
by this spec rather than fixed, so neither needs to be isolated first — **except**
as a guard against re-introducing them, which Task 5 covers.

**Independent of all of the above:** `fade_samples` — SooperLooper's
loop-boundary crossfade — is **never set anywhere in the repository** at any
commit (verified: zero matches for `fade_samples|crossfad|zero.?cross` across
all `.py`/`.sh`/`.md`). A loop whose end misses a zero crossing clicks every wrap
unless the engine crossfades it. This is a latent click source that survives
every fix in this document, and it is one line.

## §C — The design

**No loop is ever the clock.** That invariant is settled. *Where* the clock
lives is ranked, not settled — canon is `DECISIONS.md` 2026-08-14 "Who owns the
looper grid clock". Do not build down this list before falsifying up it:

| # | Option | RT-thread cost | Status |
|---|---|---|---|
| 1 | SL internal sync (`sync_source = -3`) + explicit phase anchor | **none** | **Check first — §D.0** |
| 2 | JACK transport (`sync_source = -1`) + **compiled C** timebase master | none (no DSP) | Fallback — §C.2 |
| 3 | MIDI clock (`sync_source = -2`) + SPP | none, but jittery | Rejected unless we need to clock outboard gear |

**Correction (2026-08-14):** an earlier revision of this spec declared option 1
dead and went straight to option 2, and the implementation on
`yolo/looper-transport-clock` followed it there. §C.1 below is rewritten. §D
onward is written for option 2 and **executes only if §D.0 falsifies option 1.**

### C.1 Internal sync is not dead — it was never tested properly

`8d7a426` externalised the grid by snapshotting clip 0's `cycle_len`, converting
it to a BPM float (`sl_master_clock.py:30-35`), and switching `sync_source` to
`-3` (internal). It failed, and was read as proving internal sync unusable.
**It proves no such thing.** It failed because it flipped `sync_source`
mid-flight and never established phase — a defect of that implementation, not a
property of internal sync.

If SL exposes any way to say *"the downbeat is now"*, option 1 delivers an
externalised grid with defined phase, no new process, and nothing on the
realtime thread. The candidate is `tap_tempo`, which `8d7a426` guessed at and
never verified (`sl_master_clock.py:127` — `send("/set", ["tap_tempo", 0.0])`,
commented *"noop pulse — anchors UI if needed"*). §D.0 is that check.

The two defects below are why option 1 needs an explicit phase anchor to work,
rather than being adoptable as `8d7a426` wrote it:

**Lossy.** A measured cycle length becomes a rounded BPM, then the grid is
regenerated from that BPM. With `playback_sync = 1`, each slave re-aligns to a
boundary every cycle against a grid that is a few samples off the audio it was
recorded against. That is a per-wrap nudge — a plausible mechanism for a
recurring artifact, unlike anything else in the regression window.

**No phase.** `apply_internal_master` (`sl_master_clock.py:114-148`) sends
tempo and `eighth_per_cycle` and nothing that says *where the downbeat is*.
Switching `sync_source` mid-flight puts bar 1 wherever the switch happened, not
where clip 0's downbeat was. Clips recorded before and after the switch land on
different grids. This is a correctness bug that would survive any pop fix.

**What that does and does not prove.** A tempo *scalar* carries no phase — so
`8d7a426` as written cannot be rescued. It does **not** follow that SL's
internal sync engine has no phase you can set. That is the open question §D.0
answers, and it is why option 1 sits above option 2 in the §C table.

### C.2 Why JACK transport

JACK transport carries **bar / beat / tick position**, not just tempo
(`jack_position_t` BBT fields). That is precisely what C.1 is missing and cannot
fake. A timebase master publishes tempo *and* phase every period, so:

- Phase is defined at all times, including before any clip exists.
- The grid survives clip 0's deletion, re-recording, or replacement at a
  different length — because it never depended on clip 0.
- Clip 0 becomes ordinary audio. Every `if self.loop == 0` branch in the control
  layer is deleted, not rewritten. **This is the requirement being satisfied.**

"First clip sets the tempo" still works and is the standard behaviour (Live does
exactly this): measure the first recorded clip once, set the timebase master's
BPM from it, and from that moment the transport is authoritative and the clip is
just audio.

**The timebase master must be compiled, not Python.** It runs on the JACK
realtime thread. `DECISIONS.md` 2026-08-13 — *"No Python on the JACK audio
thread"* — is the governing rule. A timebase callback does no DSP and touches no
audio buffers, so there is a genuine argument it falls outside that rule's
literal scope; **we are not taking that exception.** It is ~80–120 lines of C
that fills in a `jack_position_t`; Python sets BPM over OSC and stays the
control layer. Same reasoning already recorded for the limiter: if nothing
packaged fits, writing one is small and doubles as a dry run of the
compiled-JACK-client toolchain.

**Packaged alternatives surveyed 2026-08-14 — none fit.** `jack-midi-clock` and
`klick` both *consume* JACK transport rather than master it; `jack-tools`'
transport utility is start/stop control only. **Caveat:** surveyed against the
laptop archive, not trixie arm64 — needs the same `dak ls` treatment the loopers
got before this is treated as closed.

`scripts/sooperlooper/jack_timebase.py` (Python, on `yolo/looper-transport-clock`)
is a **spike instrument only**. It exists so §D.0/0.3 can be answered without
writing C first. It must not reach the appliance, and its two arithmetic defects
(§I) must be fixed before it is trusted as a measuring instrument.

### C.3 What falls out

- `sl_master_clock.py` (155 lines), `~/.mpe_sl_master_clock.json`, and
  `_master_sync_mode` are **deleted**. There is no detach path because there is
  nothing to detach from.
- `_master_loop_established` is **deleted**. Nothing needs to know whether a
  master exists.
- `_ensure_master_playing` is **deleted**. Boundaries come from the transport
  whether or not anything is playing.
- The HUD stops doing modulo arithmetic on `loop_pos`
  (`sl-hud-monitor.py:31-37`) and stops writing JSON at 10 Hz
  (`:129`, `WRITE_INTERVAL_S = 0.1`); it reads bar/beat from the transport.
- The four-copies-of-truth problem in the review's §2 collapses: one clock, and
  loop state read from SL rather than mirrored.

### C.4 What this deliberately does not solve

Tempo derived from a measured clip length is still a rounded float. This design
does not eliminate that; it makes it **stop mattering**, because every consumer
— including a future clip 0 — syncs to the one authoritative grid rather than to
one clip's audio. Drift becomes a fixed offset per clip rather than a per-cycle
correction against a moving reference.

## §D — Task 0: the gate (do this before anything else)

**Run in this order** (see `DECISIONS.md` 2026-08-14 looper grid clock):

### D.0 — Internal sync + `tap_tempo` (no new code, try first)

Script: `scripts/sooperlooper/spike-internal-sync-phase.py`

| Step | Action |
|---|---|
| 0.0a | Apply `sync_source=-3`, set `tempo`, send `tap_tempo` noop |
| 0.0b | Record loop A → wait ≥30 s → record loop B |
| **Pass** | Same downbeat (ear) → internal sync may be enough; JACK timebase optional |
| **Fail** | Proceed to D.1 |

### D.1 — JACK transport spike (Python, not for ship)

Requires fixed `jack_timebase.py` (fractional tick accumulator, relocate `pos.tick`).
**Do not run 0.3 ear test against an unfixed clock.**

| # | Check | Pass condition |
|---|---|---|
| 0.1 | Start minimal JACK timebase master at a fixed BPM | `jack_transport` reports rolling with BBT advancing |
| 0.2 | Set SL `sync_source = -1`, `quantize = cycle`, record a slave | Record **ends on a bar boundary** of the transport, not on finger release |
| 0.3 | Does SL honour **bar phase** or only tempo? | Two clips recorded minutes apart start on the **same downbeat** |
| 0.4 | Behaviour with transport rolling and **no loops recorded** | No crash, no xrun storm; SL idles |
| 0.5 | Stop/relocate the transport mid-playback | Defined, non-crashing behaviour; note what it is |

**Kill criterion:** if 0.3 fails — SL follows tempo but not bar phase — §G executes
(or C timebase master if 0.3 passes). Record in `docs/measurements/` either way.

## §E — Task table (executes only if Task 0 passes)

| # | Task | Files | Depends on |
|---|---|---|---|
| 1 | Timebase master process: fixed BPM, publishes BBT, `set_bpm` over OSC/IPC | new `scripts/sooperlooper/jack_timebase.py` | 0 |
| 2 | `apply_grid_sync` → `sync_source = -1` for all loops; **set `fade_samples`**; drop the master/slave asymmetry | `sl_grid_sync.py:14-43` | 1 |
| 3 | Delete `_master_loop_established`, `_ensure_master_playing`, `_schedule_grid_sync`, all `if self.loop == 0` branches | `apc_footswitch.py` | 2 |
| 4 | Delete `sl_master_clock.py`, its test, and the master-clock JSON | `sl_master_clock.py`, `tests/test_sl_master_clock.py` | 3 |
| 5 | LED/state driven from SL state, not mirrored: subscribe **all** loops incl. 0, handle states 0/2/14 | `apc_footswitch.py`, `sl_bench_listener.py:34` | 3 |
| 6 | Apply grid config **once at startup** in the bench (today it is applied only by a separate process and a 350 ms defer) | `sooperlooper-apc-bench.py` | 2 |
| 7 | HUD reads bar/beat from transport; drop the 10 Hz JSON write | `sl-hud-monitor.py`, `sl_hud_state.py` | 1 |
| 8 | `--dump-midi` raw monitor; verify `0x77`/`0x7A` on the actual APC | `sooperlooper-apc-bench.py`, `apc_transport.py:8-9` | — |
| 9 | Fix Shift+Stop All using whatever Task 8 reveals | `apc_transport.py` | 8 |
| 10 | Drop `\|\| true` from `wire-jack-graph.sh`; count failures, exit non-zero | `wire-jack-graph.sh:35-55` | — |
| 11 | Fake SL engine + tests for the state listener | `tests/` | 5 |

**Tasks 8 and 10 are independent of Task 0** and should land regardless — they
are diagnostics, and two of the four ship-blockers currently have no diagnostic
path at all.

**Delete, do not adapt, `tests/test_apc_footswitch.py:35`** — it asserts
`calls.count("trigger") == 1`, encoding the pop-causing trigger as spec. It will
otherwise resist Task 3.

## §F — Acceptance

Against the four open items in `docs/measurements/sooperlooper-eval-2026-08-14.md:168-173`,
Mitch-verified by ear, one variable at a time, against the `e6d8ba6` baseline:

1. Clip 0 records and **audibly loops with no wrap pop**.
2. Clip 2+ completes record → play on a bar boundary.
3. **Deleting clip 0 is indistinguishable from deleting clip 7** — remaining
   clips keep playing, keep their grid, and new clips still quantise. *(This is
   the new acceptance criterion and the point of the spec.)*
4. Shift+Stop All stop and 3 s reset work on the APC mini mk2.
5. Beat HUD tracks the transport, including with **no clips recorded at all** —
   which is impossible today and is the cheapest proof the grid is externalised.

## §G — Fallback if Task 0 fails

If SL follows tempo but not bar phase, the fallback is **not** the internal-clock
design (§C.1 — same phase defect). It is a **silent always-running reference
loop**: loop 15 (or a hidden index) holds one bar of silence, runs from session
start, and is the `sync_source`. Clip 0 becomes an ordinary clip because it is no
longer the master.

Uglier — it burns a loop slot, and the reference loop must be created before any
user clip — but it preserves phase, which is the property §C.1 cannot provide.
The product requirement is still met: deleting clip 0 deletes a clip.

## §H — Risks

| Risk | Severity | Mitigation |
|---|---|---|
| SL ignores JACK BBT phase (Task 0.3) | Blocks §C | Task 0 is a gate, §G is the fallback |
| Timebase master is a new realtime-adjacent process | Medium | Publishes position only, no audio; must not allocate in the timebase callback |
| Reworking the control layer re-breaks free-form (B2 **pass**) | Medium | Free-form becomes `sync_source = 0`, untouched by §C; re-run B2 as a regression check |
| Scope creep back into a full control-layer rewrite | Medium | Tasks 3–5 are deletions. If a task adds net lines to `apc_footswitch.py`, stop and re-read this line |
| Pop turns out to have a cause outside the regression window | Low | `fade_samples` (§B) is the first thing to try, and it is one line |

## §I — Implementation review, 2026-08-14 (`yolo/looper-transport-clock`)

Review of `e8ff41a` against this spec. Full review:
[`../reviews/grumpy-review-looper-2026-08-14.md`](../reviews/grumpy-review-looper-2026-08-14.md).

### What the control layer got right — lands regardless of which clock wins

`_master_loop_established`, `_ensure_master_playing`, `_refresh_grid_sync` and
**every** `if self.loop == 0` branch are deleted. `sync_from_sl` now covers OFF
(0), RECORDING (2), PAUSED (14) and the quantize-wait states, for **all** loops
including 0; the listener subscribes `range(num_loops)` and re-registers every
15 s. Clip 0 is an ordinary clip in the code, not just in intent — **the product
requirement is met.** `fade_samples` is set, `--dump-midi` landed, grid config is
applied once at bench startup, and the HUD monitor is 121 lines lighter.

The spike script is honest: it marks 0.3 — the kill criterion — as a manual ear
test and does **not** claim it passes.

### Defects found, and their resolution (all closed 2026-08-14)

| # | Defect | Resolution | Verified |
|---|---|---|---|
| 1 | Timebase truncated the per-period tick increment — **2.34% slow**, 2.8 beats/min lost at 120 BPM | `advance_tick_remainder()` fractional accumulator | **measured** — 0.0000% drift over 60 s |
| 2 | `pos.tick` identically zero (`abs_beat_f` used as float in its own remainder) | `int(abs_beat_f)` in `bbt_at_frame()` | **measured** — tick 480 at half-beat, was 0 |
| 3 | `threading.Lock` acquired on the RT thread; docstring claimed "realtime-safe" | BPM is a plain float (GIL-atomic read); docstring corrected to "Task 0 spike" | code |
| 4 | Acceptance §F.5 unreachable — writer emitted `loop_len: 0.0`, reader computed `has_master = loop_len > 0.05` and ignored the producer's keys | `sl_hud_state.py` honours `source == "jack_transport"`, trusts producer `has_master`, passes `bpm`/`source`, separate transport staleness | code |
| 5 | `/bpm` never reached clients — `beats_per_minute` set only in the `new_pos` branch | assigned unconditionally each callback | code |
| 6 | Two new test files uncollectable (`ModuleNotFoundError: apc_grid`) — 91 lines of new tests had never run | `tests/conftest.py` puts `scripts/sooperlooper` on `sys.path` | **measured** — 500 pass |
| 7 | `test_apc_transport::test_short_on_release_before_hold` **red**, sitting directly on ship-blocker #3 (Shift+Stop All) | fixed in `apc_transport.py` | **measured** — green |
| 8 | `apply_freeform` never cleared `playback_sync`, which grid sets to 1.0 on all 16 loops — grid→free-form left every loop aligning to a dead sync source | `playback_sync 0.0` added | code |
| 9 | `stop_all_loops` dropped the `awaiting_quantize = False` reset | restored | code |

**Unrelated and still red:** `tests/test_scroll_momentum.py` (2 failures). Not
touched by this branch — `scroll_widgets.py` and its test are unchanged vs
`e6d8ba6`. Pre-existing, tracked separately, not a looper concern.

### Still open

- **No timeout on `awaiting_quantize`.** Hold-clear recovers the pad, but the
  latch is unbounded if state updates stop arriving. Low risk now that all loops
  are subscribed and re-registration runs, but unbounded is unbounded.
- **The clock question itself** — §C table, `DECISIONS.md` 2026-08-14. §D.0
  (internal sync + `tap_tempo`) has not been run, and may make §D.1 moot.

### Process note worth keeping

The spec's own tripwire — *"if a task adds net lines to `apc_footswitch.py`,
stop"* — fired (net **+22**) and was correctly overridden: the additions are
`sync_from_sl`, which is Task 5 and legitimately additive. A tripwire that fires
on a good change is working as intended; the check is whether the override is
argued, not whether it is never used.

## §J — Grid mode must never be applied without a running clock

**Root cause of the 2026-08-14 evening loop** — "switch to grid, second tap
won't leave record", "clip 0 ends up free-form but clip 2 shouldn't be", "the
clock HUD never kicks in after saving clip 0". One cause, three faces:

`apply_grid_sync` set `sync_source = -1` (JACK transport) at bench startup.
**Nothing starts a timebase master.** `scripts/start-jack-timebase.sh` is manual
and referenced only by the Task 0 spike; there is no unit, no supervisor, no
call site. With no rolling transport there are **no cycle boundaries at all**, so:

| Symptom | Mechanism |
|---|---|
| 2nd tap won't leave record | SL parks in `WaitStart` forever; the bench had three stacked gates that swallow taps in that state |
| HUD beat never appears | HUD reads transport BBT; transport is stopped, `beat` is `None` |
| "it's free-form now" | Each attempted fix backed further out of grid — ending at clip 0 free-form, which reintroduces the clip-0-is-special design this spec exists to delete |

**The three gates were the trap.** Each fix for "the pad doesn't respond" added
another `return` in `_tap` rather than asking *why SL never leaves WaitStart*.
Three gates, all firing on the same missing clock, each one making the real
cause harder to see. When a fix adds a guard against a symptom, check the guard
is not hiding the cause.

**Fixes applied:**

1. **Grid defaults to internal sync** (`sync_source = -3` + explicit `tempo`,
   `MPE_SL_GRID_CLOCK=internal`). SL owns the pulse, so boundaries exist with no
   extra process. Grid mode now works standalone. `transport` stays available
   for the §D.1 spike behind the same env var.
2. **The bench refuses transport mode without a rolling transport** — probes
   JACK for a rolling state *and* a non-`None` beat, and falls back to free-form
   with a message naming the fix. It never silently applies a sync source whose
   clock is absent.
3. **The quantize wait is time-bounded** (`QUANTIZE_WAIT_TIMEOUT_S`, default 6 s).
   No boundary in that time releases the pad and logs why. A dead pad with no
   explanation is the worst available failure.
4. **The stacked `_tap` gates are gone.** While armed (`WaitStart`) a second tap
   reaches SL as cancel, which is what the player means by it.

**Test guard:** `test_grid_default_clock_is_internal_and_self_sufficient` fails
if the default grid clock goes back to something we don't start, and
`test_quantize_wait_times_out_instead_of_latching` fails if the latch becomes
unbounded again. Two tests that asserted the swallowed second tap were deleted —
they encoded the bug.

**Does not change the §C ranking.** Internal sync being *self-sufficient* is not
the same as internal sync carrying *phase*: §D.0 is still unrun, and the phase
question still decides the production clock. This makes grid playable today and
stops the pad dying silently; it does not close the gate.

## §K — What was actually built (2026-08-15)

Supersedes §C.2, §D.1, §E and §G. Implemented on `yolo/looper-transport-clock`.

### K.1 The grid model

A grid needs **tempo**, a **unit**, and a **phase**. We had been building two of
the three, and the missing phase was the single cause of clips joining out of
phase, record-stop landing on the wrong boundary, and position not resetting
when everything stopped.

| | Source |
|---|---|
| tempo | the first take's length. The take **is** one bar, by definition — no guessing, no BPM to set |
| unit | the first take. `eighth_per_cycle` is sized so one cycle equals it, so clip 2 waits for the end of clip 1 |
| phase | `Engine::set_tempo` zeroes `_quarter_counter` and `_tempo_counter`, so re-sending the tempo IS the phase reset — **measured in engine.cpp**, not inferred from `tap_tempo` |

Tempo is kept **exact** in the engine and rounded only for display
(`display_bpm`). Rounding the engine tempo makes the grid bar differ from the
recorded audio — 39.8672 → 40 shortens the bar by 20 ms, so the defining take
walks half a second away from later clips inside twenty loops.

### K.2 Two states, not settings

`set_grid_active(active=False)` — no grid: `quantize=0 sync=0 round=0`. The take
that will define the grid must be genuinely free-form; there is nothing to
quantize to. `set_grid_active(active=True)` — grid up: `quantize=1` (QUANT_CYCLE)
`sync=1 round=0`.

Tracking a *setting* (a count-in toggle) rather than the *state* (does a grid
exist) was the recurring bug shape: the bench skipped its own quantize-wait for
the defining take while the engine still had `quantize`/`round` on, and stretched
a short take to a bar belonging to the previous session's tempo.

### K.3 No clips, no grid

Clearing the last clip drops the grid and returns the engine to free-form,
landing in exactly the state a track reset produces. Driven by engine state
(`SL_STATE_OFF`), so it holds however the clips were cleared. Before this,
clearing pads individually left a stale grid and the next "first" take was
quantized to a tempo whose clips were gone — a 1-in-2 repro that depended only
on how the previous session had been cleared.

### K.4 Engine facts that cost us a session

All read from SooperLooper source. **Every correct answer this session came from
`engine.cpp`; none came from reasoning about parameter names.**

| Fact | Consequence when unknown |
|---|---|
| `smart_eighths` doubles `_eighth_cycle` below 60 BPM and pushes it to every loop | "First take = one bar" makes a 6 s take 40 BPM, so we tripped it every time — clips waited a bar and a half. **Disabled.** |
| `ports[PlaybackSync] = 0.0f` is the default | Forcing 1 made a fresh clip wait for the *next* boundary after record-stop had already landed on one |
| `round` on top of a quantized stop adds another cycle | Takes extended by a whole bar. Always 0 now |
| `pause`/`trigger` are toggles; `pause_on`/`pause_off` are explicit | A trigger on a paused loop restarts it *while still paused*: pad green, no audio, next press resumed mid-loop |
| `QUANT_OFF=0 QUANT_CYCLE=1 QUANT_8TH=2 QUANT_LOOP=3` | We used QUANT_CYCLE with an 8-eighth cycle, so clips quantized to one bar of a multi-bar take |

### K.5 Deliberately not built

- **JACK timebase master.** Its only purpose was phase, which `set_tempo`
  provides. `jack_timebase.py`, `spike-jack-transport.py`,
  `jack_transport_util.py` and `start-jack-timebase.sh` are dead — delete them.
- **Control-layer quantization.** Would need sample-accurate timing Python
  cannot give; the engine already quantizes correctly.
- **Warping.** Not needed while the take is the truth. It becomes mandatory only
  for explicit-BPM mode, where the grid is the truth and audio must conform.
  SL has `stretch_ratio`, `tempo_stretch` and rubberband already built in.

### K.6 Still open

- **Sub-take clips.** Every clip is a 1×, 2×, 3× multiple of the first take.
  A shorter clip needs `QUANT_8TH`, which with this cycle gives
  eighth-of-take resolution. Per-clip setting, not a redesign.
- **Phase anchor latency.** The anchor fires when the OSC state update reports
  the defining take started — up to ~100 ms late. The retrigger should absorb
  it. If clips land consistently early or late, this is the suspect.
- **Mode visibility.** The bench's sync mode comes from an env var and cannot be
  read back from the engine, so a restart can silently flip grid/free-form.
- **The wedge.** Unexplained after three occurrences, all during heavy OSC
  probing. Engine has since run 16 h clean. Needs a soak under normal use.
