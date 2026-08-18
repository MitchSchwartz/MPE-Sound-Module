# Tier 3 seam weld spike — 2026-08-18

**Verdict:** **Option B adopted in code** — scratch loop parallel capture + offline merge at seam. Ear pass pending (Mitch B2).

**Spec:** `Documents/specs/looper-loop-seam-spec.md` Tier 3 spike (P2).

---

## Problem

Overdub-after-immediate-stop writes tail audio at **playhead 0+**, not at `[N−M, N)`. The spec predicted this (`§Rejected approaches`, `§Background`). Gain/fade tuning cannot fix wrong buffer geometry.

## Candidates (spec table)

| Option | Verdict |
|--------|---------|
| A. Stay in Record until silence | Rejected ear — loop extends past intended downbeat |
| **B. Scratch loop + merge** | **Shipped in this spike** |
| C. JACK-side tail tap | Correct geometry; deferred — new audio path, CPU |
| D. SL substitute at wrap | Fails timing — tail gone before playhead reaches N |

## Chosen path — Option B

1. **Main loop (0):** immediate `record` stop on defining-take pad-down → fixed length N, Playing.
2. **Scratch loop (15):** `undo_all`, then `record` while release is still audible (parallel capture, same JACK input).
3. **Silence** on `in_peak_meter` → stop scratch record.
4. **Offline merge:** `save_loop` main + scratch → `seam_merge.merge_tail_at_seam()` → `load_loop` merged buffer onto main.
5. **Cleanup:** `undo_all` scratch; deferred grid clock / phase re-anchor flush after merge.

SooperLooper has no “weld here” OSC; save/load + Python crossfade is the minimal viable seam merge.

## Implementation

| File | Role |
|------|------|
| `scripts/sooperlooper/seam_merge.py` | Crossfade tail onto loop end + wrap `[0,M)` |
| `scripts/sooperlooper/sl_seam_weld.py` | OSC save/merge/load worker (background thread) |
| `scripts/sooperlooper/apc_footswitch.py` | Scratch capture state machine (overdub removed) |
| `scripts/sooperlooper-apc-bench.py` | Wires `SeamWeldWorker` to all footswitches |
| `tests/test_seam_merge.py` | Offline merge unit tests |

## Configuration

| Variable | Default | Meaning |
|----------|---------|---------|
| `MPE_SL_SEAM_WELD` | `1` | Enable Tier 3 (0 = immediate stop only, Tier 1 fade) |
| `MPE_SL_SCRATCH_LOOP` | `15` | Parallel tail capture slot — **must be idle** |
| `MPE_SL_SEAM_MERGE_SAMPLES` | `2048` | Wrap crossfade width M |

## Preconditions (Pi ear test)

| Gate | Command |
|------|---------|
| RT live | `mpe rt check` |
| Bench up | `mpe looper sl-bench status` |
| Loop 15 empty | Do not use track 16 during defining take |

## Ear test procedure

1. Defining take: play phrase, pad-down on downbeat while release rings.
2. Log: `scratch tail record on loop 15` → `seam merge queued` → `seam-weld: done`.
3. **Pass:** loop length unchanged (same bar count); release audible through wrap; no pop at N→0.
4. **Fail signals:** pop persists; loop lengthens; tail only at head; scratch bleed on track 16.

## Risks / follow-ups

- **load_loop glitch:** brief interruption possible during reload — measure on Pi.
- **save_loop latency:** poll up to 8s; wedge if command path dead (see B8 history).
- **Track 16 reserved:** UX note — scratch uses last loop; document in operator guide.
- **Option C:** if B ear-fails, spike JACK tail tap next (full control, higher cost).

## What this supersedes

- Overdub-based “tail weld” (`b464a5e` and earlier) — **withdrawn**; wrong mechanism.

**Next step:** Mitch ear test on Pi after `mpe looper deploy dev` + `mpe looper sl-bench restart`.
