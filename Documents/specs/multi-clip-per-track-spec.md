# Multi-clip per track — Ableton-style slot matrix

**Issue:** [#115 autosave](https://github.com/MitchSchwartz/MPE-Sound-Module/issues/115) (follow-on; out of scope v1)  
**Status:** Approved (Gate A — Mitch 2026-08-26)  
**Last updated:** 2026-08-26 (America/Toronto) — **rev 2**, code-audit corrections

**Register:** working hypothesis unless labelled **measured**. Builds on shipped
grid clock (`looper-transport-clock-spec.md`), seam weld (`looper-loop-seam-spec.md`),
and song save/load v1 (`looper_songs.py` / touch HUD).

**Locked product decisions (Mitch 2026-08-26):** see §TL;DR and body — do not
re-litigate without a dated DECISIONS row.

> **Rev 2 (2026-08-26) — corrections from a code audit.** Rev 1 stated 16 tracks,
> scratch on loop 15, and Scene Launch 1–8. All three are wrong against the
> shipped code; every affected figure and rule below is corrected, and the
> reasoning is recorded in §Rev 2 corrections. Two items became **OPEN** rather
> than corrected because they are product calls, not facts — see §Open decisions.

---

## TL;DR

Move from **one clip per SooperLooper loop** (today: 16 loop indices → 16 pad
positions on row 0 only, banked 8 at a time) to **Ableton Session View semantics**:

| Axis | Meaning |
|------|---------|
| **Column** | One **track** (**15** total — loop 14 is the scratch/weld buffer, never a track; APC shows 8, banked) |
| **Row 0–7** | **Clip slot** on that track (8 slots per column) |
| **Audible rule** | At most **one slot playing per column** — not polyphonic stacking |
| **Switch** | Quantized: **mute/stop outgoing + launch incoming** on the same bar |
| **Cancel** | Re-tap the **outgoing** slot before the boundary → abort pending switch |
| **Scene Launch 1–7** | Toggle **slot row 0–6** across **all 15 tracks** (not visible-8 only). Row 7 is pad-only on mk1 — see [OPEN-1](#open-decisions) |
| **Persistence** | Manifest **v2** — full 15×8 slot matrix; **manual save only** |
| **Touch** | Save/Load whole session via existing HUD — **in scope**; per-slot matrix UI — **not** |

**Prerequisite (Phase 0):** pending-mute **cancel** on the **current single-slot**
model must ship first — same gesture muscle memory the multi-clip switch reuses.

**Out of scope v1:** autosave (file GitHub issue), touch matrix editor, scene/row
clear gesture (Stop All + hold-clear per pad unchanged).

---

## Problem statement

Today's bench maps **one SooperLooper loop ↔ one APC pad** on clip row 0 only
(`apc_grid.py`: `CLIP_ROW = 0`, rows 1–7 reserved). That matches 16 independent
loops, not **multiple finished takes per track** with quantized switching.

Mitch's play style (DECISIONS.md 2026-08-14 "Loop UX") is Session View: several
recorded clips per track, one audible at a time, row launches across the set.
Song save v1 already persists loop WAVs + manifest, but only the **flat 16-loop**
layout — one file per loop index, no slot dimension.

**Product requirement:** 8 clip slots × 15 tracks; switch clips on a column
without polyphony; scene rows launch/stop a horizontal slice; save/load restores
the full matrix from touch (and later APC/bench).

---

## Non-goals

| Item | Notes |
|------|-------|
| Polyphonic clips per column | Two slots on the same track never play together |
| Touch UI for editing the 8×15 matrix | Save/load whole session is enough for v1 |
| Autosave on stop/switch/power | Manual save only; track as future GitHub issue |
| Scene row clear / "exclusive row off" gesture | Only Stop All + per-pad hold-clear (`undo_all`) |
| Replacing seam weld or grid clock | Orthogonal; slot switch uses same quantize path |
| Second SooperLooper instance / >16 engine loops | 16 SL loops = **15 tracks + 1 scratch**; slots are bench-managed storage |
| **Recording a new clip while another plays on the same track** | **Out of scope v1** — arming a record on a track silences it. See §One buffer per track for why, what it would cost, and the cheap forward-compat step taken now |
| Full APC shift-layer multiply/reverse in this spec | Separate work; shares persistence layer when ready |

---

## Grid model

### APC 8×8 layout (after this spec)

```
        col0   col1   …   col7     ← 8 visible tracks (viewport offset)
row 7   slot7  slot7  …  slot7    (no scene button on mk1 — OPEN-1)
  …
row 1   slot1  slot1  …  slot1    Scene Launch 2 ↔ row 1
row …   (rows 2–6 ↔ Scene Launch 3–7)
row 0   slot0  slot0  …  slot0    Scene Launch 1 ↔ row 0  (bottom row)
        track  track  …  track
        +off   +off+1      +off+7
```

- **Columns = tracks.** Viewport `offset` banks which 8 tracks appear (same travel
  model as `GridView` today — `PAGE_STEP = 8`, `NUDGE_STEP = 1` with Shift).
- **Rows = slot index** `S ∈ [0, 7]`. Row 0 is the APC bottom row (`pad_note` convention).
- **All 8 rows** are clip slots — no separate "controller rows" in v1.

**⚠️ The track index space is NOT contiguous.** SooperLooper runs
`MPE_SL_LOOPS = 16` loops and loop **14** is the scratch/weld buffer
(`sl_seam_weld.SCRATCH_LOOP`), so the tracks are
`[0…13, 15]` — 15 of them, with a hole at 14. Naive `track = offset + col`
addressing will hand a pad the seam-weld buffer, and `MAX_VIEW_OFFSET =
NUM_LOOPS - GRID_COLS = 8` means the viewport genuinely can place it in a column.

**Addressing rule (rev 2):** columns index into
`musical_loop_indices()` — the existing helper in `looper_songs.py` that already
excludes the scratch loop — **not** into `range(NUM_LOOPS)`:

```
tracks = musical_loop_indices()      # [0..13, 15]  — length 15
track  = tracks[offset + col]        # offset ∈ [0, 7]
```

`MAX_VIEW_OFFSET` therefore becomes `len(tracks) - GRID_COLS = 7`, not 8. Every
iteration over "all tracks" in this spec means `musical_loop_indices()`.
Hardcoding `range(16)` anywhere is a defect.

**This is already true today, harmlessly.** `GridView.loop_for_pad` returns
`self.offset + col` with no scratch exclusion, so at `offset ≥ 7` a pad already
addresses loop 14. Today that pad merely shows scratch state on a row nobody
launches from. Under the slot matrix the same pad would *record into* and *launch*
the seam-weld buffer. Excluding it is therefore new work, not a regression to
avoid — and it needs its own test (see §Risks).

### Engine mapping

| Concept | SooperLooper | Bench layer |
|---------|--------------|-------------|
| Track `T` | Loop index ∈ `[0…13, 15]` | Column position in `musical_loop_indices()`; one **active slot** pointer per track |
| Slot `S` | Not a native SL object | WAV + metadata in manifest; loaded into loop `T` on launch |
| Scratch / weld | Loop **14** (`sl_seam_weld.SCRATCH_LOOP`, unchanged) | Never a track; excluded from songs and from the grid |
| Audible on track | Loop `T` playing or muted | Exactly one slot's audio loaded in loop `T` when occupied |

**Implication:** up to **120 occupied slot WAVs** (15 × 8) in storage, but still
**≤15 loops playing** across tracks (one per column max). Recorded-but-idle slots add memory like
today's idle loops (see DIRECTION.md memory table).

---

## Pad semantics (per cell)

Gestures apply to **(track, slot)**. Existing record/stop/clear vocabulary from
`loop_model.py` extends with **slot selection** and **pending switch**.

### Tap matrix (grid established, quantized)

| Cell state | Pad down | Pad up | Result |
|------------|----------|--------|--------|
| **Empty** | Arm record into this slot | — | Record starts (free-form if no grid; else quantize arm per existing rules) |
| **Recording (this slot)** | Close take (quantize stop if grid clip) | — | Slot occupied; may become active if first on track |
| **Playing (this slot, active)** | Queue **mute** at boundary (stop) | — | Pending stop; LED blink |
| **Playing (this slot, active)** + pending stop | **Cancel** — `mute_off` or equivalent abort | — | Stay playing; clear pending |
| **Stopped (occupied, not active)** | Select + queue **launch** at boundary | — | Pending switch: mute active slot on track, load this slot, trigger |
| **Stopped (occupied)** while **other slot active** on same track | Queue **switch** at boundary | — | Outgoing = active slot; incoming = this slot |
| **Stopped (occupied)** while **this slot is active** (muted) | Queue **launch** | — | Relaunch same slot on bar |
| **Empty (non-active slot)** while **another slot active** on same track | Arm record into this slot | — | **Swap first:** save active slot to disk if dirty, clear loop `T`, then record into slot `S` (Gate A). **The track goes silent at the moment of arming** — see §One buffer per track |
| **Any occupied** | Hold ≥ clear threshold | — | `undo_all` on track loop + remove slot from matrix (unchanged clear gesture) |

**Not polyphonic:** launching slot B on a track with slot A playing never layers A+B.
The bench always schedules **mute/stop A** and **load+trigger B** for the same
quantize boundary.

**Defining take / seam weld:** first take that establishes grid tempo may still use
seam weld on the **track's active slot** — same as today on that loop index; slot
index recorded in matrix metadata.

### LED hints (contract)

| Meaning | LED |
|---------|-----|
| Empty | Off |
| Occupied, stopped | Yellow (unchanged from today — Gate A) |
| Playing (confirmed) | Green solid |
| Recording | Red / red blink (WAIT_START) |
| Pending launch or pending stop/switch | Green or yellow **blink** on affected pad(s) |
| Outgoing slot with pending switch | Blink until boundary or cancel |
| Seam weld active on track | Amber on active slot (existing seam spec) |

Solid = engine confirmed; blink = bench `pending` (same contract as `loop_model.py`).

---

## Scene Launch rows (1–7)

Scene Launch button `R` (1-based, `R ∈ [1, 7]`) ↔ **slot row** `S = R − 1`.

**Only rows 0–6 have a scene button.** On the mk1 the eighth scene-launch note
**is** Stop All Clips (`apc_transport.NOTE_STOP_ALL_CLIPS_MK1 = 0x59`; the module
docstring says so outright), so it cannot also launch row 7. Row 7 is reachable by
pad only. The mk2 has a dedicated Stop All (`0x77`) and may have all eight scene
buttons free — resolving that difference is [OPEN-1](#open-decisions).

**⚠️ The scene-launch note numbers do not exist in the codebase yet.** Only
Shift, Stop All and Track-8 are defined per variant. New per-variant constants
are needed, and they fall in the same category as `ARROW_NOTES_MK2`, which
carries an explicit *"UNVERIFIED against hardware"* warning. Confirm by
`sooperlooper-apc-bench.py --dump-midi` before P3 — see [SP6](#spike-gate-a--before-p1p2-implementation).

**Scope:** affects **all 15 tracks**, including tracks banked off the visible 8.
Implementation must iterate `musical_loop_indices()`, not `visible_loops()` and
not `range(16)`.

### Row state

**Occupied slot** = slot has audio (`loop_len > MIN` or manifest entry present).

| Scene row LED | Condition |
|---------------|-----------|
| **OFF** (dark) | For row `S`: every **occupied** slot `(T, S)` across all 15 tracks is **playing** (active on its track). Empty columns **do not count** — if track T has no slot S, skip T. |
| **ON** (lit) | At least one occupied `(T, S)` is **not** playing (stopped or another slot active on T) |

### Toggle on press

| Row LED before | Action |
|----------------|--------|
| **ON** | **Launch row:** for each track `T` where slot `(T, S)` is **occupied** and **not currently playing**, queue the same **switch** as a pad launch (mute active on T if any, load `(T,S)`, trigger at boundary). Tracks with empty slot S: no-op. |
| **OFF** | **Stop row:** for each track `T` where slot `(T, S)` is **occupied** and **playing** (active), queue **mute** at boundary. Do not stop other slots on T. |

No separate "scene clear" — stopping the row mutes playing cells in that row only.

**Stop All Clips** (mk1: scene-launch note `0x59`; mk2: dedicated `0x77`): unchanged
— immediate mute all tracks (`stop_all_loops`), lift/restore `mute_quantized`. This
is precisely why row 7 has no scene button on mk1.

---

## Cancel and transition state machine

Per **track** `T`, bench holds:

```
active_slot: S | None          # which slot is loaded / considered "live" on loop T
pending: None | STOP | LAUNCH(slot) | SWITCH(from, to)
```

Global quantize fires on bar boundary (existing `mute_quantized` + SL sync).

```mermaid
stateDiagram-v2
    [*] --> Idle: no pending
    Idle --> PendingStop: tap active playing slot
    PendingStop --> Idle: boundary → mute confirmed
    PendingStop --> Idle: re-tap outgoing → cancel mute
    Idle --> PendingLaunch: tap stopped occupied slot
    PendingLaunch --> Idle: boundary → load + trigger
    PendingLaunch --> Idle: re-tap incoming → cancel launch
    Idle --> PendingSwitch: tap different occupied slot while active playing
    PendingSwitch --> Idle: boundary → mute old + load new + trigger
    PendingSwitch --> Idle: re-tap outgoing slot → cancel
```

**Cancel rule (locked):** re-tap the **outgoing** slot before the boundary:

- **Pending stop** on active slot → send cancel (`mute_off` if SL still playing unmuted,
  or clear pending without sending mute — spike § below).
- **Pending switch** → clear pending; outgoing keeps playing; incoming stays stopped.
- **Pending launch with nothing playing on the track** → there is no outgoing slot,
  so "re-tap the outgoing slot" is undefined. Cancel is a **re-tap of the incoming
  slot**. (Rev 2: rev 1's diagram said "re-tap outgoing active", which cannot
  happen in this case.)

**Phase 0 prerequisite:** implement cancel on **today's** single-slot-per-track model
(one slot per loop, row 0 only) so WAIT_STOP / pending-mute cancel is proven before
the slot matrix multiplies surface area.

**Interaction with seam weld:** while track `T` is in `SEAM_WELD`, ignore launch/switch
on that track except hold-clear abort (per seam spec single-loop lock).

---

## Persistence — manifest v2 (sketch)

Manual save only. v1 songs remain loadable (reader accepts `version: 1`).

### File layout

```
~/.mpe/looper-songs/
  {slug}.json
  {slug}_t{TT}_s{S}.wav    # TT = track 00–15, S = slot 0–7
```

**v1 compat (Gate A):** reader loads `version: 1` forever (maps each loop to slot 0 on
that track). **Overwrite Save** upgrades to v2 (`{slug}_{loop:02d}.wav` →
`{slug}_t{TT}_s0.wav`); stale v1 WAVs pruned on overwrite like today.

### Manifest v2 (example)

```json
{
  "version": 2,
  "name": "Evening jam",
  "slug": "evening-jam",
  "saved_at": "2026-08-26T11:00:00-04:00",
  "bpm": 120.0,
  "grid_active": true,
  "tracks": [
    {
      "track": 0,
      "wet": 1.0,
      "active_slot": 2,
      "slots": [
        null,
        null,
        {
          "file": "evening-jam_t00_s2.wav",
          "len_s": 4.0,
          "sl_state": 2
        }
      ]
    }
  ]
}
```

| Field | Meaning |
|-------|---------|
| `tracks[].track` | Loop index 0–15 |
| `tracks[].active_slot` | Which slot was loaded at save time (`null` if track empty) |
| `tracks[].slots` | Length-8 array; `null` = empty slot |
| `slots[].sl_state` | SL state enum at save (playing → reload triggers; muted → mute_on) |
| `bpm` / `grid_active` | Same as v1 |

**Slot residency (Gate A — locked):** SooperLooper has **one loop buffer per track**
(16 buffers = 15 tracks + scratch). Inactive occupied slots live on **disk only**; bench holds manifest paths.
Swap-to-disk on slot switch or record-into-non-active-slot before `load_loop` /
record arm.

**Save path (`looper_songs.py`):**

1. `stop_playback` (all tracks muted/paused, quantize lifted briefly).
2. For each occupied `(T, S)`: `save_loop` from loop `T` **only if** `(T,S)` is the
   loaded active slot; other occupied slots must already be on disk from prior
   swap/save (dirty active slots flushed before switch per runtime rules).
3. **Verify before writing the manifest (rev 2).** For every slot the manifest is
   about to reference, assert the WAV exists and exceeds `MIN_TAIL_WAV_BYTES`
   (already defined in `sl_seam_weld.py`). Fail loudly, naming the offending
   `(track, slot)`, rather than writing the manifest.

   *Why this is not optional:* step 2 makes save correctness depend on an
   invariant maintained by a different code path at a different time. If any
   earlier swap failed to flush, the manifest silently references a stale or
   missing take — and **the save looks exactly the same whether it captured the
   audio or not.** That is the recurring defect shape on this appliance: a
   reading that is identical when broken and when fine. The check costs one
   `stat` per slot.
4. Write atomic manifest v2.

**Load path:**

1. `stop_playback` + `clear_all_loops`.
2. `apply_grid_sync` + establish grid if `grid_active`.
3. **Lazy load:** for each track with an occupied `active_slot`, `load_loop` that
   slot's WAV into loop `T` and restore `sl_state`. Inactive occupied slots: manifest
   + file paths only — **no** `load_loop` until APC/touch launch (SP1 times latency).

**Touch HUD (`touch_browser_looper_songs.py`):** no UI change to menu flow; must
save/load v2 without error when matrix has multiple slots per track. Confirm
overwrite / load-replace dialogs still correct.

---

## Touch HUD acceptance (save/load v2)

| ID | Criterion | Test |
|----|-----------|------|
| T1 | Save with 0 slots occupied → same error as v1 ("Nothing to save") | Unit |
| T2 | Save with ≥2 slots on same track → manifest v2 + ≥2 WAVs for that track, **with different content** (assert differing length or checksum, not mere existence — an existence check passes on a stale WAV) | Unit + disk |
| T3 | Load v2 restores BPM/grid and active slots audibly | Manual ear |
| T4 | Load v2 with inactive slots → launch from APC/touch path works without re-save | Manual |
| T5 | v1 song still loads (backward compat) | Unit |
| T7 | Save aborts loudly, naming `(track, slot)`, when a referenced WAV is missing or under `MIN_TAIL_WAV_BYTES` | Unit |
| T6 | Busy/confirm/toast UX unchanged; no matrix editor added | Visual |

---

## One buffer per track

SooperLooper gives each loop index exactly **one** audio buffer, and this spec
binds track `T` to loop index `T`. Two consequences follow, and the second is a
real divergence from the Session View model this spec opens by claiming.

**Consequence 1 — inactive slots live on disk.** Already handled: Gate A decision
1, lazy `load_loop` on launch.

**Consequence 2 — you cannot record a new clip while another plays on the same
track.** Arming a record on track `T` requires the buffer that is currently
producing sound, so the bench must flush and clear it first. **The track goes
silent the instant you arm.** In Ableton, recording into an empty slot leaves the
playing clip running until the new take is launched. This is the one place v1
knowingly departs from the model.

### What lifting it would cost

The fix is not more buffers — it is breaking the `track == loop index` identity.
SL has 16 buffers; if fewer than 15 tracks are occupied, spare loop indices exist.
Record the new take into a **spare** index, then repoint the track at it. No copy,
no disk round-trip: a clip switch becomes a pointer move.

| Work | Size |
|------|------|
| `slot_matrix` owns `track → loop_index` + a free-index pool | Moderate; pure and testable |
| `apc_grid` pad→loop resolution becomes indirect | Small |
| `looper_songs` v2 | **Simpler** — files key on `(track, slot)`, loop index becomes an ephemeral runtime detail |
| LED / state fan-in (`sl_hud_monitor`, `sl_bench_listener`, `led_table`) | Moderate; every loop→pad map needs the same indirection |
| Seam weld | **The risk.** It keys on the active take's loop index and already owns a reserved buffer; it would have to follow a moving pointer |

The pure layer is perhaps a day. The cost is not lines — it is that
`column == loop index` is threaded through roughly six modules including the most
delicate code in the looper.

### Why v1 does not do it

**The behaviour would be conditional.** Record-while-playing works only while a
spare loop index exists. With many tracks occupied there is no spare and the
gesture must fall back to silencing the track. A gesture that sometimes keeps the
track alive and sometimes kills it is worse for playing than one that always
behaves the same way — you cannot build muscle memory on it. Making it
unconditional means reserving spares up front, which roughly halves the usable
track count.

That is a product trade (fewer tracks, richer per-track recording), not an
engineering blocker. It is worth taking deliberately, not as a side effect.

### Forward-compatibility step taken now (rev 2)

`slot_matrix` **owns the `track → loop_index` mapping from day one**, even though
v1 always returns the identity mapping via `musical_loop_indices()`. Nothing else
may compute a loop index from a column directly.

This costs almost nothing now — the indirection already has to exist because the
track space is non-contiguous — and it is the whole retrofit later. Skipping it
means re-threading six modules under a shipped instrument instead.

---

## Open decisions

Not facts to be corrected — product calls. Each needs a dated DECISIONS row.

| # | Question | Options | Recommendation |
|---|----------|---------|----------------|
| **OPEN-1** | Row 7 has no scene button on mk1 (note `0x59` is Stop All). mk2 has a dedicated Stop All (`0x77`) and may have 8 free scene buttons. | (a) 7 scene rows on both variants — row 7 pad-only, behaviour identical everywhere. (b) 8 rows on mk2, 7 on mk1 — full use of the hardware, divergent muscle memory. (c) 7 slots per track, dropping row 7 entirely — perfectly regular, loses 15 slots. | **(a)** until SP6 confirms mk2's notes. Identical behaviour across variants beats one extra row; (c) stays available if the asymmetry grates. |
| **OPEN-2** | Record-while-playing (see §One buffer per track). | (a) v1 ships silence-on-arm; revisit later on the indirection laid down now. (b) Reserve spare loop indices now, ~halving track count, for unconditional record-while-playing. | **(a)**. 15 tracks with a known limitation beats ~7 tracks with a richer gesture, and rev 2's indirection keeps (b) cheap later. Mitch's call — it is a playing-feel question, not a technical one. |
| **OPEN-3** | Scratch loop index — today **14** (Pi: loop 15 `save_loop` is empty). Hole at 14 breaks naive column math. | (a) Keep **14** until SL fixes loop 15, then move scratch to **15**. (b) Move scratch to **0** now — tracks **1–15**, column 0 → loop 1; needs Pi `save_loop` spike on loop 0 + song/index migration. (c) Stay on 14; fix addressing only via `musical_loop_indices()`. | **(b)** if Pi spike passes — clearest mapping. **(c)** is zero-migration fallback. Needs dated DECISIONS row before env/default change. |

---

## Rev 2 corrections

What changed from rev 1 and the evidence, so none of it is re-litigated from memory.

| Rev 1 claim | Reality | Evidence |
|-------------|---------|----------|
| "16 tracks", `T ∈ [0, 15]`, iterate `range(16)` | **15 tracks**; the scratch loop is excluded | `looper_songs.musical_loop_indices()` filters `scratch_loop` out of `range(NUM_LOOPS)` |
| Scratch is "Loop 15" | Scratch is loop **14** | `sl_seam_weld.SCRATCH_LOOP = int(os.environ.get("MPE_SL_SCRATCH_LOOP", "14"))`; `looper_songs.SCRATCH` resolves from it |
| "up to 128 occupied slot WAVs" | **120** (15 × 8) | arithmetic on the corrected track count |
| "Scene Launch 1–8" ↔ rows 0–7 | Only **1–7** on mk1; the 8th scene note is Stop All | `apc_transport.NOTE_STOP_ALL_CLIPS_MK1 = 0x59`; module docstring: *"Stop All 0x59 (scene launch 8)"* |
| Scene buttons "unused" and available | No scene note constants exist at all beyond Stop All | `apc_transport.py` defines only Shift, Stop All, Track-8, arrows |
| Cancel = "re-tap the outgoing slot" (all cases) | Undefined for a pure launch — there is no outgoing slot | rev 1 §state machine, `PendingLaunch` transition |
| Save relies on prior flushes with no check | Silent bad-save path | rev 1 §Save path step 2 |

Also found, outside this spec's scope but adjacent: `sl_hud_monitor.py` defaults
`MPE_SL_SCRATCH_LOOP` to `15` while `sl_seam_weld.py` defaults it to `14`. With
the env var unset they disagree. Fix before P1.

---

## Relationship to current code

| Today | After |
|-------|-------|
| `apc_grid.CLIP_ROW = 0` only | All rows 0–7 address slots |
| `loop_for_pad(row, col)` ignores row ≠ 0 | `slot_for_pad(row, col)` + `track_for_pad` (indirect through `musical_loop_indices()`, never `range(16)`) |
| `looper_songs` MANIFEST_VERSION = 1, one WAV per loop index | v2 track/slot matrix |
| Scene Launch buttons unused for clips; **no scene note constants defined** | Scene Launch 1–7 toggle slot rows 0–6; new per-variant note constants required (SP6) |
| One clip per loop index | Slot matrix + active pointer per track |

Multi-clip **APC/bench gesture** work is a **separate implementation track** from
Phase 0 cancel, but **shares** `looper_songs.py` v2 persistence with touch save/load.

---

## Files (expected touch)

| File | Change |
|------|--------|
| `scripts/sooperlooper/apc_grid.py` | All rows = slots; `(track, slot)` addressing |
| `scripts/sooperlooper/loop_model.py` | Slot-aware plans; pending switch/cancel |
| `scripts/sooperlooper/apc_footswitch.py` | Per-(track,slot) state; scene row handler |
| `scripts/sooperlooper/apc_transport.py` | Scene Launch 1–7 → rows 0–6 (mk1 note 8 = Stop All, unchanged); **new per-variant scene note constants**, unverified until SP6 |
| `scripts/sooperlooper/looper_songs.py` | Manifest v2 read/write; v1 compat; **keep `wav_path(slug, loop)` intact** for the v1 reader and add a `wav_path_v2(slug, track, slot)` sibling rather than changing the signature; save-time WAV verification |
| `scripts/sooperlooper/slot_matrix.py` | **New** — active slot, occupancy, pending (pure) |
| `patch_browser/touch_browser_looper_songs.py` | v2 save/load integration tests hook |
| `tests/test_loop_model.py` | Cancel + switch plans |
| `tests/test_slot_matrix.py` | **New** — scene row logic, occupancy |
| `tests/test_looper_songs.py` | v2 round-trip |
| `config/mpe.env.example` | Any matrix limits |
| `scripts/sooperlooper/sl_hud_monitor.py` | **Pre-existing bug:** defaults `MPE_SL_SCRATCH_LOOP` to `15` while `sl_seam_weld.py` defaults it to `14`. Unset env → the HUD excludes the wrong loop. Fix before slot work builds on an ambiguous scratch index |
| `Documents/DECISIONS.md` | Row on Gate A approval |

---

## Phases

| Phase | Deliverable | Gate |
|-------|-------------|------|
| **P0** | Pending-mute **cancel** on single-slot (row 0) model | Unit + Mitch tap test |
| **P1** | `slot_matrix` pure layer + manifest v2 save/load (touch HUD) | T1–T6 |
| **P2** | APC grid all rows; pad switch/stop/record per §semantics | Mitch ear + unit |
| **P3** | Scene Launch 1–7 row toggle (rows 0–6) across 15 tracks | SP6 note confirmation, then scene LED + launch/stop |
| **P4** | Spike outcomes wired (load timing, inactive slot residency) | Measurement note |

P0 is **blocking** for P2/P3. P1 can parallel P0 if v2 writer reads active slot only
first, then fills multi-slot once swap logic exists.

---

## Risks

| Risk | Mitigation |
|------|------------|
| 120 WAVs × load time on full load | Lazy load inactive slots; spike save_loop/load_loop timing |
| Memory with many occupied idle slots | Same as 64 idle loops — monitor VmRSS; `-t` tuning |
| Cancel via `mute_off` vs SL WAIT states | Spike on engine; mirror WAIT_STOP cancel (`record` cancel pattern) |
| Scene row + pending switch race | Single pending per track; scene applies after cancel clears |
| v1 song migration | Read v1 forever; upgrade on overwrite Save (Gate A) |
| Seam weld during switch | Block switch on track in SEAM_WELD |
| **Manifest references a stale or missing WAV** | Verify every referenced WAV at save (step 3); fail loudly. Without it a bad save is indistinguishable from a good one |
| **Scratch loop addressed as a track** | All track iteration goes through `musical_loop_indices()`; `range(16)` is a defect. Add a unit test asserting no pad maps to `SCRATCH_LOOP` at any viewport offset |
| **Scene note numbers wrong (recalled, not measured)** | SP6 `--dump-midi` before P3, same discipline as `ARROW_NOTES_MK2` |

---

## Spike (Gate A — before P1/P2 implementation)

Run on bench (Pi or laptop + SL):

| # | Question | Method | Pass |
|---|----------|--------|------|
| SP1 | `save_loop` / `load_loop` timing for 15 tracks × up to 8 slots (120 max) | Script: measure per-call latency, full matrix save | Document p95; touch UI timeout budget |
| SP2 | Inactive slot `load_loop` latency at launch | Measure single swap load_loop p95 | Within touch/APC timeout budget (disk-only lazy — **decided**) |
| SP3 | **mute_off cancel** for pending quantized mute | Tap play → tap stop → re-tap before bar | Outgoing keeps playing; no glitch |
| SP3b | **pause_on cancel** for pending quantized launch | Tap stop (muted) → tap launch → re-tap before bar | Stays stopped/muted; no launch at bar |
| SP4 | Switch: mute track A slot + load B + trigger same boundary | OSC sequence from bench | One audible clip after boundary |
| SP5 | Scene row launch with 15 tracks (7 off-screen) | Iterate `musical_loop_indices()` | All occupied cells in row queue; scratch loop never touched |
| **SP6** | **Scene Launch note numbers per APC variant** | `sooperlooper-apc-bench.py --dump-midi`, press each scene button on mk1 (and mk2 if available) | Confirmed note list; settles [OPEN-1](#open-decisions) for mk2 |

Spike write-up: `docs/measurements/multi-clip-slot-spike-YYYY-MM-DD.md`.

---

## Gate A decisions (locked 2026-08-26)

| # | Decision |
|---|----------|
| 1 | **Inactive slots:** disk-only; lazy `load_loop` on launch. Song load restores **active slot per track only**. |
| 2 | **Scene row empty cell:** skip `(T,S)` silently; does not affect row LED. |
| 3 | **v1 migration:** read v1 forever; **overwrite Save** upgrades to v2 + new filenames. |
| 4 | **Record into non-active slot:** save active slot to disk if dirty, clear loop `T`, record into slot `S`. |
| 5 | **APC LED:** no change — occupied stopped stays yellow. |
| 6 | **Autosave:** out of scope v1 — [#115](https://github.com/MitchSchwartz/MPE-Sound-Module/issues/115). |
| 7 | **P0 owner:** laptop session (pending-mute cancel). |
| 8 | **Track count (rev 2):** 15 tracks — loop 14 is scratch. Superseded rev 1's "16 tracks". |
| 9 | **Scene rows (rev 2):** Scene Launch 1–7 → rows 0–6. Row 7 pad-only pending [OPEN-1](#open-decisions). |

**Next step:** fix the `sl_hud_monitor` scratch-index default → P0 pending-mute cancel on
single-slot model → spikes SP1–SP6 → P1 touch v2.

---

## References

- Grid viewport: `scripts/sooperlooper/apc_grid.py`
- Gesture plans: `scripts/sooperlooper/loop_model.py`
- Songs v1: `scripts/sooperlooper/looper_songs.py`
- Touch HUD: `patch_browser/touch_browser_looper_songs.py`
- Seam / quantize: `Documents/specs/looper-loop-seam-spec.md`, `looper-transport-clock-spec.md`
- UX canon: `Documents/DECISIONS.md` 2026-08-14 "Loop UX"
