# Tier 3 seam weld spike — 2026-08-18

**Verdict (2026-08-18 ear):** **Option B failed defining take** — tail often missing, HUD showed bar 2 from scratch loop, `load_loop` jumped to loop start. **Defining take reverted to Tier 2** (stay Recording until release quiet, then stop). Tier 3 code remains behind `MPE_SL_SEAM_WELD=1` for future grid-clip experiments; **default is now `0`.**

**Spec:** `Documents/specs/looper-loop-seam-spec.md` Tier 3 spike (P2).

---

## Problem

Overdub-after-immediate-stop writes tail audio at **playhead 0+**, not at `[N−M, N)`. The spec predicted this (`§Rejected approaches`, `§Background`). Gain/fade tuning cannot fix wrong buffer geometry.

## Pi ear failures (Option B)

| Symptom | Cause |
|---------|--------|
| HUD bar 2 during tail | Scratch loop 15 growing while playing drove `_phrase_reference` |
| Release tail missing | Peak meter / merge skip / scratch mis-alignment |
| Jump to loop start on tail end | `load_loop` + `trigger` resets playhead |

## Current defining-take path — Option E (seam overdub, default)

**Verdict (2026-08-18 ear, Tier 2):** Release captured but **tail stacks louder at wrap**
(end-of-buffer tail + loop head) — crossfade is not the fix; geometry is.

**Option E (2026-08-18):** `MPE_SL_TAIL_MODE=seam` (default):

1. Pad-down while Recording: **immediate** `record` stop → fixed `loop_len`.
2. Wait for `loop_pos ≥ TAIL_SEAM_RATIO × loop_len`.
3. `overdub` with reduced `input_gain` — release lands at the seam, not sample 0+.
4. When release quiet, wait for next seam zone → `overdub` off.
5. Grid establishes on fixed length — no clip growth, no `load_loop`.

Tier 2 (`MPE_SL_TAIL_MODE=extend`) retained for A/B.

## Previous defining-take path — Tier 2 (extend)

1. Pad-down while Recording: **do not** send record stop.
2. Peak poll until release quiet (or timeout).
3. Send record stop → Playing with tail captured in-loop by SooperLooper.
4. Grid establishes once on final `loop_len` — no reload, no seam jump.

## Candidates (spec table)

| Option | Verdict |
|--------|---------|
| **A. Stay in Record until silence** | **Active for defining take (clip 0)** |
| B. Scratch loop + merge | Ear-failed defining take; code kept, default off |
| C. JACK-side tail tap | Correct geometry; deferred — new audio path, CPU |
| D. SL substitute at wrap | Fails timing — tail gone before playhead reaches N |

## Option B (Tier 3 — retained, not default)

1. **Main loop (0):** immediate `record` stop on defining-take pad-down → fixed length N, Playing.
2. **Scratch loop (15):** parallel capture + offline merge (see prior spike notes).

SooperLooper has no “weld here” OSC; save/load + Python crossfade was the minimal viable seam merge.

## Implementation

| File | Role |
|------|------|
| `scripts/sooperlooper/seam_merge.py` | Crossfade tail onto loop end + wrap `[0,M)` |
| `scripts/sooperlooper/sl_seam_weld.py` | OSC save/merge/load worker (background thread) |
| `scripts/sooperlooper/apc_footswitch.py` | Tier 2 tail on defining take; Tier 3 when `_tail_stop_sent` |
| `scripts/sooperlooper/sl_hud_monitor.py` | Excludes scratch loop from phrase bar count |
| `scripts/sooperlooper-apc-bench.py` | Wires `SeamWeldWorker` when seam weld enabled |
| `tests/test_seam_merge.py` | Offline merge unit tests |

## Configuration

| Variable | Default | Meaning |
|----------|---------|---------|
| `MPE_SL_SEAM_WELD` | **`0`** | Tier 3 scratch+merge (1 = enable for experiments) |
| `MPE_SL_SCRATCH_LOOP` | `15` | Parallel tail capture slot — **must be idle** |
| `MPE_SL_SEAM_MERGE_SAMPLES` | `2048` | Wrap crossfade width M |

## Ear test procedure (Tier 2)

1. Defining take: play phrase, pad-down while release rings.
2. LED stays red→green blink (recording tail); HUD stays 1 bar.
3. After release fades: loop plays from natural wrap — **no jump to head**.
4. **Pass:** release audible through wrap; no pop at N→0; bar count stable.

## What this supersedes

- Overdub-based “tail weld” — **withdrawn**; wrong mechanism.
- Option B as default defining-take path — **withdrawn** after Pi ear fail.

**Next step:** Mitch ear test Tier 2 on Pi after `mpe looper deploy dev` + `mpe looper sl-bench restart`.
