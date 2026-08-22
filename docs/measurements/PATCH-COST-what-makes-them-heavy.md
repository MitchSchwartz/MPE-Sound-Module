# What actually makes the heavy patches heavy — it is not unison

**2026-08-22.** Read directly from the `.fxp` files in
`MPE-Library/assets/user-data/quick-select/latest/Quick Select/`. No Pi time used.

## Retraction

`CEILING-ANALYSIS-what-maxed-out-means.md` proposed **unison** as the dominant cost and the
"only lever with an order of magnitude in it," reasoning from ~3,700 cycles/sample/voice
being ~8x a plain voice — the shape unison produces.

**That was wrong.** Unison is **1-2 across every heavy patch.** The `param0` values that
would have carried a voice count are **engine selectors**, not unison counts.

## What is actually in them

| patch | unmuted oscillators | unison | filters | polylimit |
|---|---|---|---|---|
| **Crystals** | **3 x Twist** (type 10) | n/a — Twist has none | 1, 0 | 16 |
| **Cloud Horn** | **2 x String** (type 9) | n/a | 1, 0 | 16 |
| **Bowed String** | 2 x Wavetable (type 2) | **1** | 8, 8 | 16 |
| **Analog Foundation** | 2 x Classic (type 0) | **1 and 2** | **14, 10** | 16 |

All are `scenemode=0` (scene A only), so scene B costs nothing.

### The expensive parts

**Twist** is Surge's port of Mutable Instruments **Plaits** — a complete synthesis engine per
oscillator, with internal models and resampling. **Crystals runs three per voice.** At its
measured ceiling of 3 voices, that is **nine Plaits instances concurrently.**

**String** is **Karplus-Strong physical modelling** — delay lines with per-sample feedback and
interpolation. Cloud Horn runs two per voice.

**Analog Foundation is the instructive one.** Plain Classic oscillators, essentially no
unison — so its cost is **not in the oscillators at all.** It is the **filters, types 14 and
10**, and it runs two. The high-numbered Surge filters are the expensive modern models
(ladder emulations, cutoff/resonance warp, tri-pole).

**So there are two independent cost centres — oscillator algorithm and filter algorithm — and
different heavy patches are heavy for different reasons.**

## The cycle count is not anomalous after all

The earlier analysis called ~3,700 cycles/sample/voice "an order of magnitude too high" for a
synth voice. Against **three Plaits engines plus two filters**, it is approximately right.

**The CPU is not being wasted. These patches are legitimately expensive.** That conclusion
matters more than the retraction: it removes the assumption that there is obvious fat to trim.

## The patch lever is real but smaller and costlier than claimed

There is no dial to halve. What exists:

| patch | change | saving | musical cost |
|---|---|---|---|
| Crystals | 3 Twist -> 2 | ~33% | **significant** — deletes a synthesis layer |
| Cloud Horn | 2 String -> 1 | up to ~50% | significant |
| Analog Foundation | cheaper filter types | unknown, possibly large | changes the character |
| Bowed String | already modest | — | — |

**These are redesigns, not settings tweaks.** Removing a Plaits engine from Crystals does not
thin it the way dropping unison would — it removes a layer of the sound.

`config/patch_normalization.json` (still `{}`) remains a viable delivery mechanism, but what it
would carry is per-patch surgery, not a global parameter.

## Consequences for the ceiling analysis

1. **The "order of magnitude" patch lever does not exist.** Withdraw it.
2. **The remaining levers become relatively more important**, because nothing larger is waiting
   behind them: overclock (~11-19%), `-mcpu=cortex-a72` (~5-15%). Together they plausibly turn
   **3 voices into 4** on Crystals.
3. **Multi-core remains the only lever with a multiple in it** — see
   `MULTITHREADING-ASSESSMENT.md`.
4. **The product question changes shape.** It is not "fix the patches." It is: *what does a
   Pi-based MPE instrument ship as its heavy presets?* Three-oscillator Plaits pads may simply
   be above this hardware's weight class, while Bowed String and the 38 patches that never hit
   the 15-voice probe cap are comfortable.

## Cheap follow-up, not yet run

**Census the oscillator and filter types across all ~53 Quick Select patches.** Free — it is
file parsing, no Pi. It answers whether Crystals-class construction (Twist stacks, String
stacks, type 10+ filters) is **rare or common** in the shipped library, which decides whether
this is an edge case to document or a curation problem to act on.

## Method note

This was a **free, offline check that overturned a hypothesis built on measured data** — the
`MEASUREMENT-DISCIPLINE.md` cheap-check-first rule working as intended. The unison theory was
internally consistent and wrong, and reading four files settled it in minutes. It would have
cost a Pi session and a patch-editing pass to discover the same thing empirically.
