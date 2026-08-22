# Agent prompt — V9 probe duration + clean 1024×2

Copy everything below the line.

---

**Invoke `measurement-design` before running either cell.**

## Context

V8-a ceilings are **upper estimates** — V8-b showed Cloud Horn "clean @ 7" on an 8 s ramp **overrunning @ 7** on 45 s hold. See [`V8-REVIEW-ceiling-is-optimistic.md`](V8-REVIEW-ceiling-is-optimistic.md).

**Do not tune the governor from V8-a tables.**

## Standing conditions

- Poly governor **OFF**, strict mode, card index live-stamped
- `midi-load-hold.py` for voice count
- Every latency log opens with **`PROVENANCE patch=… hold_voices=…`** (harness enforces)
- Fail loudly if patch file missing — never run with wrong/default patch

---

## V9-a — duration sensitivity (~15 min) — RUN FIRST

**Question:** how much does sustained-clean count drop when confirm window lengthens?

| patch | role |
|---|---|
| **Crystals** | heavy knee (V7/V8 anchor) |
| **Cloud Horn** | mid (V8-b contradiction) |
| **Closed Hat** (or similar light) | cap-hit control |

Per patch @ **1024×3**:

1. Ramp (8 s probe, step +2, start @ 1) → record `ramp_sustained_clean`
2. **60 s hold** at `ramp_sustained_clean` → xruns, DSP p99.9/max
3. Optional: if 60 s overruns, repeat at `ramp_sustained_clean - 2` (one step down only)

**Report:**

| patch | ramp clean | 60 s confirm clean? | xruns @ 60 s | DSP @ confirm |

**Falsifier:** all three match ramp → 8 s probe is adequate; V8-a table stands.

**Prediction:** Cloud Horn and Crystals **drop ≥ 2 voices** at 60 s vs 8 s ramp.

---

## V9-b — 1024×2 at verified-clean load (~15 min)

**Only after V9-a.** Pick a patch + voice count where **×3 passes 60 s with xruns=0** (or use `ramp_clean - 2` if knee is fuzzy).

**Question:** at a load where ×3 is **actually** clean, is **1024×2** also clean?

- n ≥ 3 × 45–60 s, condition A, same patch/voices
- Compare ×2 vs ×3: xruns, DSP median/p99.9, ALSA lines

**Verdict line:** "1024×2 shippable at playable load: yes/no — voice count N, patch name."

This is the cell V3 and V8-b never ran (both were overload arms).

---

## Rules

1. **V9-a before V9-b** — clean-load pick depends on duration truth
2. Stop after both cells; improve write-up if time remains
3. Binding constraint: **Surge per-voice DSP** only — anything else is distraction

## Deliverable

`docs/measurements/v9-probe-duration-2026-08-22.md` on branch off `dev`
