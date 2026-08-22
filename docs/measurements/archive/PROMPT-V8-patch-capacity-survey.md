# Agent prompt — V8 patch capacity survey + playable 1024×2

Copy everything below the line.

---

**Invoke the `measurement-design` skill before designing or altering any cell.**

## Where this stands

Binding constraint: **Surge per-voice DSP cost.** V8 measures only that. Kernel, USB,
buffer geometry, and governor tuning are out of scope until V8 completes.

V7 gave capacity for **one** heavy patch (Crystals) at three buffer sizes. V8 maps the
**shipped Quick Select set** at **1024×3 only** so governor work gets a real spread, not a
single-point guess.

## Dead — do not test

Same dead list as `PROMPT-V7-capacity-curve.md` plus anything V7/V1 already retired.

## Standing conditions

- Poly governor **OFF** entire run.
- **Strict mode** during cells; restore softmode after.
- **1024×3** for V8-a only.
- Ramp with `midi-load-hold.py`; **no soak at each voice count** — probe to first overrun only.
- `midi-load-hold` caps at **15 voices**; report `≥15` if no overrun through 15.
- Capture **fxp metadata** (osc/unison/FX/filter) from embedded patch XML — zero extra Pi time.

## V8-a — patch capacity survey (~45 min) — PRIMARY

**Question:** at 1024×3, how many simultaneous hold voices does each Quick Select patch sustain
cleanly?

Per patch:

1. Parse metadata from `.fxp` (`parse-fxp-metadata.py`).
2. Load patch (`load-patch-osc.py`).
3. Ramp voices (start at **1**); first xrun delta > 0 = first overrun.

**Tier:**

| tier | sustained clean |
|---|---|
| polyphonic | ≥ 8 |
| limited | 3–7 |
| pad-only | 1–2 |
| unplayable | fails @ 1 voice |

**Report:** full table, spread (min/max/median), named unplayable list, Crystals anchor row.

Harness: `sudo ./scripts/measure-plan-v8.sh`

## V8-b — playable 1024×2 (~15 min)

**Question:** at a **playable** voice count (mid-weight patch from V8-a, not overload), is
**1024×2** clean vs **1024×3**?

V3 compared under **overload** (3-voice stagger load → 100% DSP). V8-b uses
`midi-load-hold` at the patch's sustained-clean count, **n = 3 × 45 s**, condition A.

**Verdict:** latency win (42.7 ms) only ships if x2 is clean at playable load.

## Rules

1. **Stop when V8-a + V8-b complete.** Improve the write-up if time remains — no extra cells.
2. One variable per comparison; stamp card index, git SHA, governor state in every block.
3. If a result is about anything other than per-voice DSP cost, it is a distraction — drop it.

## Deliverable

`docs/measurements/v8-patch-capacity-2026-08-21.md` on a branch off `dev`:

- V8-a table + tiers + governor spread summary
- V8-b playable comparison vs 1024×3 baseline
- **V8 before any governor tuning**
