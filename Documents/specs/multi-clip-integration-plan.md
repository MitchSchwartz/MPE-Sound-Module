# Multi-clip integration plan — one owner per track column

**Status:** I0 + I1 implemented on `dev` (2026-08-27) — deploy + Pi gate pending Mitch  
**Last updated:** 2026-08-27 (America/Toronto)  
**Product spec:** [`multi-clip-per-track-spec.md`](multi-clip-per-track-spec.md) (rev 4)  
**Spike evidence:** [`docs/measurements/multi-clip-slot-spike-2026-08-26.md`](../../docs/measurements/multi-clip-slot-spike-2026-08-26.md)  
**Pi failure that triggered this plan:** first multigrid deploy (`8815bb4` → `d9124a6`) — pads toggle like single-clip; row 1 LED partially works; scene rows wrong until occupancy hook landed, then gestures still wrong.

**This document does not replace the product spec.** It says how to **integrate** Session View semantics with the shipped single-clip bench so P2/P3 can pass Mitch's ear gate.

---

## Document map

| Doc | Role |
|-----|------|
| [`multi-clip-per-track-spec.md`](multi-clip-per-track-spec.md) | **What** — pad matrix, LEDs, scenes, persistence (Gate A locked) |
| **This file** | **How** — architecture, phases, gates, stop rules |
| [`session-control-plane-spec.md`](session-control-plane-spec.md) | Pattern reference — one owner per fact, reconciliation |
| [`scripts/sooperlooper/slot_matrix.py`](../../scripts/sooperlooper/slot_matrix.py) | Pure planner — **keep**; extend, do not fork |
| [`scripts/sooperlooper/apc_footswitch.py`](../../scripts/sooperlooper/apc_footswitch.py) | Single-clip gesture + ring-out + grid — **legacy path** when `MPE_SL_MULTIGRID=0` |

---

## Problem statement

The multigrid feature (`MPE_SL_MULTIGRID=1`) was wired as a **second controller** on
top of the existing bench, not as a **replacement** for it.

Today, one SooperLooper loop index (= one APC column = one track) is driven by:

| Layer | Still active in multigrid? | Owns |
|-------|---------------------------|------|
| `LoopFootswitch` + `loop_model` | **Yes** — OSC sync, `poll_led`, row-0 pad bind | Single-clip gesture lifecycle, ring-out, grid arm, row-0 LED |
| `SlotRuntime` + `SlotSurface` | **Yes** — all 8×8 pad input, matrix LED, scenes | Slot occupancy, pending switch, `load_loop` / bare `record` hits |

Pad **input** goes to `SlotSurface` first (correct). Pad **LED on row 0** still gets
repainted by `LoopFootswitch.poll_led()` → `_sync_led()` on the same notes
(`view.note_for_loop` → bottom row only). Engine **state** updates both footswitch and
surface. Two models of "what this track is doing" run in parallel.

That produces the observed failure mode:

1. **Toggle semantics** — `slot_matrix` treats `occupied(slot)` as the gate for gesture
   planning. `occupied` is set only after `sync_engine` infers a finished take. While
   recording, `active_slot` is set but `occupied` is false → a second tap plans
   `ACT_RECORD` again → SooperLooper `record` toggles (single-clip behaviour).
2. **Missing close-take lifecycle** — Spec §Pad semantics: pad down while **recording this
   slot** → **close take**. Single-clip implements close via `plan_gesture` (down/up,
   ring-out overdub, wait_stop). Multigrid sends one `record` on pad down only; pad up
   does nothing for gestures.
3. **LED/audio drift** — Matrix paints row 1 from slot state; row 0 from footswitch
   `led_for(sl_state)`. User sees "second clip lights up" while behaviour still toggles
   one buffer.
4. **Buffer vs matrix** — Record into slot B while slot A occupied sends `undo_all` but
   leaves A marked occupied on disk metadata → launch/switch plans reference stale files.

**Root cause (one sentence):** there is no **single reducer per track** that owns gesture
phase, slot occupancy, engine reconciliation, and LED output for all eight rows in that
column.

---

## Target architecture

### Principle: one column controller, one buffer

Each track `T ∈ [0, 14]` gets one **`TrackColumn`** (name TBD in code) that is the only
writer of:

- OSC commands for loop `T`
- APC pad colours for all eight slot rows in column `T` (when visible)
- Mutable slot matrix state for track `T` (`Track` from `slot_matrix`)

SooperLooper remains authoritative for **audio** (what is in the one buffer, what state
code the engine reports). The column controller is authoritative for **intent** (which
slot is armed, recording, pending switch, occupied on disk).

### Layering (unchanged intent, fixed boundaries)

```
                    ┌─────────────────────────────────────┐
  MIDI pads ───────►│  SlotSurface (bench delegate)       │
  Scene 1–7 ─────►│  — routing, hold timer, bank repaint  │
                    └──────────────┬──────────────────────┘
                                   │
                    ┌──────────────▼──────────────────────┐
                    │  SessionMatrix (15 × TrackColumn)     │
                    │  — sole mutable session state         │
                    └──────────────┬──────────────────────┘
           ┌───────────────────────┼───────────────────────┐
           │                       │                       │
    ┌──────▼──────┐        ┌───────▼────────┐      ┌───────▼────────┐
    │ slot_matrix │        │ GestureEngine  │      │ EngineSync     │
    │ (pure plans)│◄───────│ (record/ring-  │      │ (OSC in →      │
    │             │        │  out/quantize) │      │  events)       │
    └─────────────┘        └────────────────┘      └────────────────┘
```

| Module | Pure? | Responsibility |
|--------|-------|----------------|
| `slot_matrix.py` | Yes | Plans from frozen `(Track, sl_state, slot, hold)` — extend with **recording phase**, not only `occupied` |
| `GestureEngine` | No | Extract from `LoopFootswitch._gesture` / `plan_gesture` — **per-track** record/close/cancel/stop with ring-out |
| `TrackColumn` | No | Holds `Track`, `phase`, applies plans, executes OSC, emits LED column |
| `SlotSurface` | No | Maps notes ↔ (track, slot), fans out to columns, scene rows |
| `LoopFootswitch` | No | **Single-clip mode only** (`MPE_SL_MULTIGRID=0`) |

### Recording phase (new state — not in spec text, required by spec semantics)

`occupied` means "a clip exists for this slot (disk or confirmed buffer)." **Gesture**
planning also needs **`phase`** on the active arm:

| Phase | Meaning | Planner input |
|-------|---------|---------------|
| `idle` | No arm | Empty → record arm; occupied → launch/switch/stop |
| `arming` | `WAIT_START` | Cancel arm |
| `recording` | Buffer filling for slot S | **Pad down → close take** (not `ACT_RECORD`) |
| `closing` | Ring-out / `WAIT_STOP` | Wait; cancel per footswitch rules |
| `playing` | Engine playing active slot | Stop / switch |

Second tap on the same cell while `phase ∈ {arming, recording}` must plan **close**,
matching spec §Tap matrix. `occupied` stays false until close confirms.

### Engine reconciliation (replace ad-hoc `sync_engine`)

All OSC bench auto-updates (`state`, `loop_len`, `loop_pos`, optionally `wet`) become
**events** into `TrackColumn.on_engine_event(...)`.

Rules (ordered):

1. Update cached `sl_state`, `loop_len`, `loop_pos` for the track.
2. If `phase == closing` and wrap / state transition matches footswitch ring-out
   completion → `mark_recorded(slot)`, `phase → playing` or `idle`.
3. If pending switch/stop and state matches `_maybe_resolve` conditions →
   `resolve_at_boundary`.
4. Repaint column LEDs from unified model (never footswitch).

**Delete** the inference-only path that marks recorded when `not occupied(active)` and
`sl_state ∉ ACTIVE_RECORD` without an explicit close gesture — it was a band-aid.

### Quantized switch (defer to Phase C)

Spec: mute outgoing + load incoming + trigger at **one boundary**.

Phase B may ship **immediate** `load_loop` + deferred `mute_off` only if labelled
**technical debt** with a DECISIONS row. Phase C must wire pending + grid clock the same
way footswitch quantize wait works today.

### Multigrid mode: footswitch demotion

When `MPE_SL_MULTIGRID=1`:

| Do | Don't |
|----|-------|
| Build footswitches for stop-all / track-reset OSC helpers if needed | Bind footswitch pads or call `_sync_led` on matrix notes |
| Route all grid notes through `SlotSurface` | Fall through to `by_note[n].on_pad_down()` |
| Share `GridState` via one owner passed into `SessionMatrix` | Let footswitch arm grid independently of matrix |
| `poll_footswitches()` skips LED/hold for multigrid | Run footswitch hold/LED poll on visible matrix pads |

Single-clip mode (`MPE_SL_MULTIGRID=0`) keeps today's path unchanged.

---

## Phased delivery

Phases are **integration** phases. They sit **inside** product P2/P3 from the spec.

| Product | Integration phase | Delivers |
|---------|-------------------|----------|
| P2 partial (broken) | **I0–I2** | Playable multigrid on Pi |
| P2 complete | **I3** | Quantized switch + cancel |
| P3 | **I4** | Scene rows stable on unified state |
| P1 manifest v2 | **I5** | Save/load active + lazy slots |

**Code landing ≠ phase closed.** Each phase closes on **unit tests + Pi checklist** below.

---

### I0 — Stop dual control (1 session)

**Goal:** One LED writer per pad; no footswitch/matrix fight.

**Work:**

1. `poll_footswitches`: when multigrid, skip `poll_led` / hold on loops whose pad is in
   the visible matrix (or skip entirely for pad-bound footswitches).
2. `apply_view`: do not call `fs._sync_led()` for multigrid; let `SlotSurface.repaint`
   own the full 8×8 after bank change.
3. Guard `by_note` fall-through — already blocked; add assert/log if a matrix note hits
   footswitch handler.

**Gate — Pi:**

- [ ] Row 0 LED stable for 10 s idle (no blink/flicker without engine change)
- [ ] Journal: pad press logs `slots:` only, not `loop N: pad down` from footswitch
- [ ] Scene rows dark when matrix empty

**Gate — unit:** existing `test_slot_surface` + new test that multigrid flag disables
footswitch LED sync (mock).

---

### I1 — Recording phase + close take (2–3 sessions)

**Goal:** Record / close / occupied matches spec §Tap matrix for one column.

**Work:**

1. Add `TrackPhase` to column state (or extend `Track` with explicit phase field in
   runtime only — keep `slot_matrix` dataclasses frozen; pass phase into planner).
2. Extend `plan_cell_press` (or wrapper) so `phase == recording` and `slot == active_slot`
   → `ACT_CLOSE` (new action) instead of `ACT_RECORD`.
3. Extract **`GestureEngine.close_take()`** from footswitch: same OSC sequence as
   `plan_gesture` tail capture (ring-out when enabled).
4. `mark_recorded` on **close confirmed** (state + loop_len), not on arm.
5. Record into non-active slot: **flush dirty active to disk**, then `undo_all`, then arm
   — update matrix so outgoing slot remains occupied **on disk** only, not as active
   buffer.

**Gate — Pi (column 0, one track):**

- [ ] Tap row 0 → red while recording; tap again → take closes → green/yellow solid
- [ ] No accidental toggle-stop on second tap mid-take
- [ ] Row 1 record while row 0 has clip → row 0 yellow, row 1 red → close → row 1 green,
      row 0 yellow

**Gate — unit:** table-driven tests for planner + fake engine closing sequence (reuse
`tests/fake_sl_engine.py` patterns).

---

### I2 — Switch / launch / stop (2 sessions)

**Goal:** Multi-slot same column without toggle confusion.

**Work:**

1. Launch/switch: verify `load_loop` path exists before unmute; fail loud in log if WAV
   missing (already partial).
2. Stop: `mute_on` + pending stop; re-tap cancel per P0/spec.
3. Pending LED blink from `slot_leds` only.
4. Wire `boundary()` from `_maybe_resolve` when engine confirms (keep).

**Gate — Pi:**

- [ ] Two slots recorded on track 0; tap row 0 while row 1 playing → row 1 stops, row 0
      plays (immediate or at bar per interim decision)
- [ ] Hold-clear empties slot; file removed; LED off
- [ ] Stop-all + long Shift+Stop All reset clears matrix

**Gate — unit:** existing `test_slot_matrix`, `test_slot_runtime`, `test_slot_surface`
green + switch/cancel cases.

---

### I3 — Quantized switch parity (2–3 sessions)

**Goal:** Match spec and single-clip feel at bar lines.

**Work:**

1. Share **`GridState`** between column controller and gesture engine (single owner).
2. Queue switch: `load_loop` early, `mute_off`/`trigger` at boundary; pending until
   `_maybe_resolve`.
3. SP7 / OPEN-4: defer switch if ring-out overdub running until wrap (spec leaning (b)).

**Gate — Pi:**

- [ ] Switch blink until bar; audio crosses on bar
- [ ] Cancel pending switch by re-tapping outgoing slot
- [ ] SP7 audible seam check with Surge playing (not silent)

**Gate — measurement:** optional `slot_matrix_spike.py --sp7` with audio fixture.

---

### I4 — Scene rows on unified state (1 session)

**Goal:** P3 complete.

**Work:**

1. Scene press uses same `SessionMatrix` dispatch (already partial).
2. Scene LED = `scene_row_led_on` only; no footswitch transport LED conflict.

**Gate — Pi:**

- [ ] Scene 1 dark when row 0 empty; lit when row has stopped clips; press launches row
- [ ] Scene press with mixed row occupancy matches spec (skip empty cells)

---

### I5 — Manifest v2 + touch save/load (parallel after I2)

**Goal:** P1 persistence on real matrix state.

Per spec §Persistence — not blocking I0–I4 Pi validation with `MPE_SL_CLIPS_DIR` live files.

---

## Test strategy

| Layer | Tool | Must cover |
|-------|------|------------|
| Pure planner | `test_slot_matrix.py` | Phase-aware tap matrix; scene rows |
| Column + OSC | `test_track_column.py` (new) | Record/close/switch sequences on fake engine |
| Surface integration | `test_slot_surface.py` | No footswitch LED overlap; bank change |
| Regression single-clip | `test_apc_footswitch.py` | Unchanged when `MPE_SL_MULTIGRID=0` |
| Pi soak | Mitch ear checklist per phase | Ring-out seam, switch bar, scene row |

**Harness rule:** extend `FakeSlEngine` / `fake_sl_engine.py` — do not Pi-test gesture
logic that can run offline.

---

## Stop-doing list (hard rules for agents)

1. **No** second `record` hit to close a take in multigrid.
2. **No** footswitch `_sync_led` on any note handled by `SlotSurface`.
3. **No** new occupancy inference without a corresponding engine event or explicit close.
4. **No** `undo_all` without updating active_slot / phase / dirty flush semantics.
5. **No** Pi deploy claiming P2 done until **I2 Pi gate** passes.
6. **Keep** `slot_matrix` pure — side effects only in runtime/column layer.

---

## Risks

| Risk | Mitigation |
|------|------------|
| Gesture extract from footswitch breaks single-clip | Shared module called from both paths; multigrid flag branches at bench top only |
| Ring-out + switch race (OPEN-4) | I3 implements defer-at-wrap; document in DECISIONS |
| `save_loop` is page-cache fast (SP1) | Flush verification before switch (existing `_flush_active`) |
| 15 tracks × 8 slots LED diff cost | Keep matrix diff repaint; full repaint on bank only |
| Scope creep into record-while-playing (OPEN-2) | Out of scope v1 — silence-on-arm stays |

---

## Files (expected touch)

| File | Change |
|------|--------|
| `scripts/sooperlooper/track_column.py` | **New** — column controller |
| `scripts/sooperlooper/gesture_engine.py` | **New** — extract from footswitch / loop_model |
| `scripts/sooperlooper/slot_runtime.py` | Shrink to thin wrapper or fold into column |
| `scripts/sooperlooper/slot_surface.py` | Delegate to SessionMatrix |
| `scripts/sooperlooper/slot_matrix.py` | `ACT_CLOSE`, phase-aware planning |
| `scripts/sooperlooper/apc_footswitch.py` | Call shared gesture engine; multigrid LED skip |
| `scripts/sooperlooper-apc-bench.py` | Wire SessionMatrix; demote footswitch when multigrid |
| `tests/test_track_column.py` | **New** |
| `Documents/DECISIONS.md` | Row for integration plan approval |
| `PROGRESS.md` | P2 blocked on I0–I2 until Pi gates pass |

---

## Pi validation master checklist (P2 sign-off)

Run on Pi 5, `MPE_SL_MULTIGRID=1`, one bank visible, Surge audible:

1. Record slot row 0 → close → solid green.
2. Record slot row 1 same column → row 0 yellow, row 1 active.
3. Tap row 0 while row 1 playing → hear row 0, row 1 yellow.
4. Hold ~2 s on occupied slot → cleared, LED off.
5. Scene 1 dark when row empty; lit when row has stopped clips; press launches.
6. Shift+Stop All short → stop; long → full matrix reset.
7. Bank up/down → LEDs match clips for visible tracks only.

Pass all seven → P2 ear gate closed. SP7 audible seam → P3 / OPEN-4 closed.

---

## Relation to spec phases (honest status 2026-08-27)

| Spec phase | Status | Blocker |
|------------|--------|---------|
| P0 cancel | Shipped single-clip | Reuse in I2/I3 |
| P1 manifest v2 | Code partial | I5 |
| P2 grid semantics | **Broken on Pi** | I0–I2 |
| P3 scene rows | Code partial, wrong without I0–I2 | I4 |
| P4 spikes | SP1/2/4/6 done | SP7 audible in I3 |

---

## Next action

Implement **I0** on `dev`, deploy, Mitch runs I0 Pi gate (5 minutes). If stable, proceed
**I1** without further product decisions.
