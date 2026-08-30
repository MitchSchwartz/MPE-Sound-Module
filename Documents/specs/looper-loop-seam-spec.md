# Looper loop seam — tail capture and wrap continuity

**Issue:** untracked  
**Status:** Approved (P0 + P1); Tier 3 implementation on branch `yolo/pi5-looper-seam-wrap` (Pi 5 finish)  
**Last updated:** 2026-08-18 (America/Toronto)

**Register:** working hypothesis unless labelled **measured**. Builds on shipped grid
clock work (`looper-transport-clock-spec.md`, `DECISIONS.md` 2026-08-15).

---

## Problem statement

When a take closes (pad-down while recording), the loop often **clicks or drops
content at the wrap** — sample N−1 → sample 0. Mitch reports:

- Wrap pop on repeat (partially addressed by `fade_samples`; still audible on
  some material).
- **Missing release / tail** when stop is immediate — audio still in the JACK
  graph after the OSC stop is not in the buffer.
- Confusion between **pre-roll** (capture before record arm) and **post-roll**
  (capture through release and weld onto the seam). **This spec is post-roll /
  seam only.**

**Product requirement:** closing a take should produce a loop whose **end flows
into its start** on playback, without the player changing technique (pad before
note, EDP threshold tricks, etc.).

**Non-goals:**

- Pre-roll / missing **attack** at loop head (separate concern; may share
  `input_latency` calibration).
- Replacing phase re-anchor (`MPE_SL_GRID_ANCHOR_*`) — orthogonal.
- Overdub as a **performance** mode (Mitch's model is finished clips, not
  EDP-style open-loop layering — `DECISIONS.md` 2026-08-14).

---

## Background — what “seam good” means

At pad-down close:

1. **Playback starts** from sample 0 (SooperLooper already does this on
   record-off → Playing).
2. **Release tail** is still audible (Surge envelope, JACK pipeline).
3. That tail belongs at the **end** of the buffer (near N), not the start — the
   wrap seam is **end → start**.
4. When the envelope is quiet, the tail is **welded** onto `[N−M, N)` and
   crossfaded into `[0, M)` so repeated wraps are continuous.

**Live vs buffer geometry:** hearing “tail blending into the first part” at the
moment of stop is correct **live**; storing tail on sample 0 via overdub-at-playhead-0
fixes the wrong index and can **leak** into the next gesture if the player
switches loops quickly while SL is still in overdub.

---

## Rejected approaches

| Approach | Why rejected |
|----------|----------------|
| Fixed `POST_ROLL_MS` defer before stop | Extends bar by constant ms → wrong BPM on clip 0, fights quantized stop on grid clips. |
| Extend phase re-anchor wrap window | Timing/phase only; does not add audio to the buffer. |
| Naive stop → overdub until silence (all clips, no guard) | Tail lands at playhead 0+; **fast pad switch** leaves another loop/channel in overdub, capturing bleed. |
| `rec_thresh` silence-close in SL alone | SL **ignores threshold on finish** (current docs); not available without bench logic or engine change. |

---

## Design — three tiers (build in order)

### Tier 1 — Latency + crossfade (baseline, all clips)

**Intent:** align SL’s record/stop boundaries with JACK pipeline delay; smooth
wrap regardless of content.

| Control | Action |
|---------|--------|
| `input_latency` | Set per loop (or `-1` all) from JACK reported latency; optional `autoset_latency=1` at engine start. |
| `fade_samples` | Already set via `MPE_SL_FADE_SAMPLES` (default 256); tune after Tier 2/3 if needed. |
| `trigger_latency` | Default 0 unless ear tests say otherwise (forum pattern: drop trigger, tune input only). |

**Where:** `sl_grid_sync.py` → `apply_grid_sync()` and/or
`configure-grid-sync.sh` after engine start.

**Acceptance:** A/B same take with latency unset vs set — wrap pop reduced or
unchanged (not worse). No change to loop length or grid BPM.

---

### ~~Tier 2~~ — REJECTED (do not ship)

**Status:** **Rejected 2026-08-19.** “Stay Recording until peak quiet” was an AI backup
plan, not product design. It grew the clip after pad-down, duplicated release in the
buffer, and did not stop on the pad. **No code path may reintroduce it.**

---

### Stop-then-weld (all clips — defining + grid)

**Intent:** pad fixes loop length **now**; release after the stop is captured **once**
in parallel and merged at the wrap seam only.

**Behavior on pad-down while recording:**

| Context | Stop timing | Tail pass |
|---------|-------------|-----------|
| **Defining take** (`grid.is_pending`) | Immediate `record` stop | Scratch + merge when PLAYING |
| **Grid clip** (grid established) | Quantised boundary (existing WAIT_STOP) | Same, after PLAYING lands |

**Steps (both contexts):**

1. Send `record` stop (immediate or quantised — unchanged grid stop path).
2. On **Playing** (length fixed at N): enter **`SEAM_WELD`** bench state on this loop.
3. Record tail on **scratch loop 15** in parallel; poll `in_peak_meter` until quiet or max ms.
4. If release was audible: **offline seam merge** on `[N−M, N)` ↔ `[0, M)`; reload main loop.
5. Exit weld; apply deferred grid clock / phase re-anchor if any.

**Guardrails (required — Mitch concern):**

- **Single-loop lock:** while loop *i* is in `SEAM_WELD`, ignore
  record/overdub/mute on *i* except explicit cancel (long-press clear).
- **No overdub leak:** before any command on loop *j≠i*, ensure loop *i* is not
  in SL overdub/recording for tail — force `overdub` off or `undo` tail pass if
  abandoned.
- **Bank switch:** if the welded loop scrolls off-screen, **finish or abort**
  weld within `MPE_SL_TAIL_MAX_MS`; do not leave SL overdubbing on a non-visible
  loop.
- **Abort:** long-press clear or second pad-down cancel → discard tail scratch,
  normal idle.

**Spike (Gate A blocker for Tier 3 implementation):**

SooperLooper has **no** “weld tail here” OSC. Candidates to prove on bench:

| Option | Pros | Cons |
|--------|------|------|
| **A. Stay in Record until silence** after quantize boundary | SL-native | **Extends** N past bar — breaks fixed-bar unless stop aligns perfectly. |
| **B. Scratch loop slot** — record tail on loop 15, substitute/copy | SL-native-ish | Needs command sequence; 16-loop limit; complex UX. |
| **C. JACK-side tail tap** (`mpe-looper` or tap port) + offline merge | Full control | New audio path; CPU; not Phase 1. |
| **D. SL substitute** at end of first playback cycle | One cycle replace | Playhead timing; tail may be gone before playhead reaches N. |

**Implementation (2026-08-19):** Option B — scratch loop slot + offline merge
(`sl_seam_weld.py`, `seam_merge.py`). Option E (seam overdub) rejected with Tier 2.

---

## State machine (bench layer)

New states in `apc_footswitch.py` (per loop, orthogonal to `sl_state`):

```
IDLE / RECORDING / PLAYING / …  (existing derive_state)

SEAM_WELD      — fixed length; scratch tail + merge pending
```

```mermaid
stateDiagram-v2
    [*] --> Recording: pad down (defining)
    Recording --> SeamWeld: pad down close (stop sent)
    SeamWeld --> Playing: merge done or abort
    Recording --> WaitStop: pad down close (grid, quantize)
    WaitStop --> SeamWeld: boundary → Playing
    SeamWeld --> Playing: merge done or abort
```

LED hint: blink **amber** during `SEAM_WELD` (“still finishing take”).

---

## Configuration (env)

| Variable | Default | Meaning |
|----------|---------|---------|
| `MPE_SL_TAIL_RATIO` | `0.032` | Tail ends this far below ITS OWN peak (-30 dB). Replaced `MPE_SL_TAIL_THRESH`, which was absolute and therefore wrong for any patch quieter than the one it was tuned on — measured 2026-08-29 |
| `MPE_SL_TAIL_FLOOR` | `0.002` | Absolute noise floor: below this is silence regardless of ratio |
| `MPE_SL_TAIL_SILENT_MS` | `400` | A tail never rising above the floor stops here instead of holding a live overdub for a bar |
| `MPE_SL_TAIL_HOLD_MS` | `80` | Consecutive silence before close |
| `MPE_SL_TAIL_MAX_MS` | `750` | Force close / abort weld |
| `MPE_SL_SEAM_MERGE_SAMPLES` | `2048` | Crossfade width M at seam (Tier 3) |
| `MPE_SL_TAIL_CAPTURE` | `1` | Master enable stop-then-weld; `0` = Tier 1 only |
| `MPE_SL_SEAM_WELD` | `1` | Offline merge reload; `0` = stop + scratch poll, no merge |
| `MPE_SL_INPUT_LATENCY` | *(unset)* | Override; else JACK autoset |
| `MPE_SL_FADE_SAMPLES` | `256` | Existing global crossfade |

---

## Acceptance criteria

| ID | Tier | Criterion | Test type |
|----|------|-----------|-----------|
| S1 | 1 | `input_latency` / `fade_samples` applied on every `sl-grid-sync` / engine restart | Unit + `dump-loop-levels` |
| S2 | 1 | Wrap pop not worse than baseline on clip 0 (Mitch ear) | Manual B2 |
| S3 | 2 | Defining take includes release; no infinite record on stuck note (max ms) | Unit poll logic + ear |
| S4 | 2 | Long-press clear during tail capture → idle, no orphan SL Recording | Manual |
| S5 | 3 | Grid clip length = exactly one cycle after weld; tail at seam not at sample 0 only | Ear + `cycle_len` OSC |
| S6 | * | Fast switch to another pad during weld → **no** overdub bleed on other loop | Manual (Mitch scenario) |
| S7 | * | Grid BPM unchanged by tail capture on clips 1+ | `derive_tempo` unchanged |

---

## Implementation plan

| Phase | Deliverable | Gate |
|-------|-------------|------|
| **P0** | Tier 1: wire latency + document tuning procedure | Mitch ear S2 |
| **P1** | Tier 2: `TAIL_CAPTURE` in `apc_footswitch.py`, peak poll, tests | Spec review + S3–S4 |
| **P2** | Tier 3 spike write-up in `docs/measurements/` | Pick option A–D |
| **P3** | Tier 3 implementation (if spike passes) | S5–S7 |

**Files (expected touch):**

- `scripts/sooperlooper/sl_grid_sync.py` — Tier 1
- `scripts/sooperlooper/apc_footswitch.py` — state machine, peak poll
- `scripts/sooperlooper/loop_model.py` — gesture plans for tail vs immediate stop
- `tests/test_apc_footswitch.py` — tail timeout, thresh hold, abort
- `config/mpe.env.example` — new vars
- `Documents/DECISIONS.md` — row on seam policy when promoted

---

## Falsification

| If | Then |
|----|------|
| Tier 1 alone fixes wrap on clip 0 + grid clips | Stop at P0; defer P1–P3 |
| Tier 2 makes clip 0 length unstable / weird BPM | Lower max ms or disable Tier 2; rely on Tier 1 |
| No Tier 3 spike passes | Document “grid clips: quantize stop + Tier 1 only”; close Tier 3 |
| Peak meter too noisy on Pi | RMS from JACK tap or higher thresh; do not ship hot-loop overdub |

---

## References

- SooperLooper sync/latency: https://sonosaurus.com/sooperlooper/doc_sync.html  
- OSC `in_peak_meter`, `input_latency`: https://sonosaurus.com/sooperlooper/doc_osc.html  
- Grid / phase: `Documents/specs/looper-transport-clock-spec.md` §K  
- Pad gesture plan: `scripts/sooperlooper/loop_model.py`  
- Wrap pop history: `looper-transport-clock-spec.md` §B  

---

## Open questions (for spec review)

1. **Tier 2 on grid defining take only** — confirm clip 0 always free-form even
   if grid was dropped and re-armed on same pad.
2. **LED colour** during tail capture — amber vs faster blink green?
3. **Tier 3 spike owner** — bench session vs nerdrack YOLO after Gate A.

**Next step:** `spec-review` → Gate A → P0 implementation.
