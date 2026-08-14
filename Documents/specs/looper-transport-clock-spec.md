# Looper transport clock — externalise the grid from clip 0

**Issue:** untracked
**Status:** In review — gate fixes; **do not deploy ear test yet**
**Branch:** `yolo/looper-transport-clock`
**Last updated:** 2026-08-14 (America/Toronto)

**Register:** everything below is a working hypothesis unless labelled
**measured**. Measured facts carry a source — a file path with line numbers, a
git object, or a row from `docs/measurements/sooperlooper-eval-2026-08-14.md`.
The central architectural choice (§C, JACK transport as grid authority) is a
**decision taken on incomplete data** — it rests on an unverified assumption
about SooperLooper 1.7.9 that Task 0 exists to falsify. If Task 0 fails, §G is
the fallback and this document's task table does not execute.

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
different grids. This is a correctness bug that would survive any pop fix, and
it cannot be patched — a tempo scalar has no phase to carry.

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
