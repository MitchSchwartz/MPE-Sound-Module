# V8 patch capacity survey + playable 1024×2

*Measured: 2026-08-21 / 2026-08-22 (America/Toronto)*  
*Pi artifacts: `/home/mitch/plan-v8-20260821-225953`*  
*Harness: `docs/v8-patch-capacity` @ `47166e8`*

**Review (2026-08-22):** Do not use ceiling table for governor tuning until V9 — see [`V8-REVIEW-ceiling-is-optimistic.md`](V8-REVIEW-ceiling-is-optimistic.md).

## Pre-registration

| field | value |
|---|---|
| Question | What per-voice DSP spread does Quick Select impose at 1024×3, and is 1024×2 clean at playable load? |
| Claim class | ranking + shape |
| n | 53 patches (V8-a); V8-b n=3×45 s × 2 configs |
| Premises | Compute-bound (W1); poly governor off; strict mode; hold load pattern |
| Prediction | Heavy patches ≪ 15 voices; x2 clean at sub-overrun voice count |
| Falsifier | Most patches fail @1 voice, or x2 xruns while x3 clean at same playable load |
| Shortest form | Sample 12 patches — **ran full 53** (~63 min) because per-patch probe ≈70 s |

## Standing conditions

| item | value |
|---|---|
| Buffer | **1024×3** (V8-a); V8-b compares ×2 vs ×3 |
| Poly governor | **OFF** |
| Strict mode | **ON** during cells; softmode restored after |
| Probe | 8 s hold, step +2, voices 1–15 (`midi-load-hold`) |
| Card | **2** |
| Git | `47166e8` |

**Note:** V7 used 12 s probe + confirm on Crystals only. V8-a 8 s probe gives Crystals **sustained clean = 3** (V7: **4**). Same ballpark; probe length matters at the knee.

---

## V8-a — Quick Select @ 1024×3

**53 patches** in `~/Documents/Surge XT/Patches/Quick Select/`.

### Spread (sustained-clean voices)

| stat | value |
|---|---|
| **Minimum (bounded)** | **3** — Crystals and six others |
| **Maximum (bounded)** | **13** — Fireflies, Scratched Arp |
| **Probe ceiling** | **15** — 38 patches never overran (**≥15**, censored — not measured at 15) |
| **Unplayable @ 1 voice** | **none** |

### Tiers

| tier | rule | count | patches |
|---|---|---|---|
| **polyphonic** | ≥ 8 | **38** | 34 at probe cap 15; plus Bonita Keys (11), Dark Cello (11), Fireflies (13), Scratched Arp (13) |
| **limited** | 3–7 | **15** | See table below |
| **pad-only** | 1–2 | **0** | — |
| **unplayable** | fails @ 1 | **0** | — |

### Limited patches (sustained clean ≤ 7)

| patch | clean | first overrun | osc | unison (per osc) | fx |
|---|---|---|---|---|---|
| A Robotic Mind | 3 | 5 | — | — | — |
| Brave New World | 3 | 5 | — | — | — |
| **Crystals** | **3** | **5** | **3** | **[1,1,1]** engines [4,4,6] | **3** |
| Cyber Pad | 3 | 5 | — | — | — |
| Duduk | 3 | 5 | — | — | — |
| Kick | 3 | 5 | — | — | — |
| Minor 7 | 3 | 5 | — | — | — |
| House Organ | 5 | 7 | — | — | — |
| Planar Device | 5 | 7 | — | — | — |
| Res Wave Shift | 5 | 7 | — | — | — |
| Cloud Horn | 7 | 9 | 2 | [1,1,1] engines [8,8] | 2 |
| Dreamscape | 7 | 9 | — | — | — |
| Forte Piano | 7 | 9 | — | — | — |
| Irrelevant Number | 7 | 9 | — | — | — |
| Warm Reception | 7 | 9 | — | — | — |

\*Unison from `parse-fxp-metadata.py` → `unison_per_osc` (list, not summed). Earlier `unison_voices` scalar was wrong (engine selectors summed); retracted 2026-08-22.

### Anchor — Crystals

| | V8-a (8 s probe) | V7 (12 s + confirm) |
|---|---|---|
| Sustained clean | **3** | **4** |
| First overrun | 5 | 6 |
| Metadata | 3× Twist osc, engines [4,4,6], unison [1,1,1], 3 FX, filter1=LP | same patch |

**Gods** (prior guess at “heavy”) sustained **≥15** — not representative of worst case.

### Governor input (before any tuning)

- **Spread to cover:** about **3 → 15** voices (5:1) on this patch set at 1024×3; worst shipped patches cluster at **3**.
- **`MPE_POLY_CEILING=12`** is still wrong for heavy content — use **per-patch or tier policy**, not one global guess.
- **V8 before governor tuning** — these numbers are the input.

### Unison vs capacity (retracted)

Prior pass cited summed `unison_voices` (Crystals 14, Cloud Horn 16) from a parser bug — param0 engine/mode selectors were summed. Corrected 2026-08-22: both patches are `[1,1,1]` per osc. **Do not use unison as a cost driver from that column.** Osc count, engine choice, FX, and filters remain the useful metadata.

---

## V8-b — playable 1024×2 vs ×3

**Patch:** **Cloud Horn** (mid-weight: sustained clean **7** from V8-a).  
**Load:** `midi-load-hold` **7 voices**, **n = 3 × 45 s**, condition A, strict mode.

| config | xruns per run | DSP median (approx) |
|---|---|---|
| **1024×2** | 20 / 28 / 18 | ~78–81% |
| **1024×3** | 18 / 24 / 28 | ~77–81% |

**Verdict:** At this patch’s **ramp-derived playable ceiling**, **both configs overrun** — graph xruns, ~80% DSP, no ALSA drain. **1024×2 is not cleaner than ×3** when holding the voice count the ramp said was safe. The **42.7 ms latency win** (V3/W0) still applies to buffer geometry, but **only helps when total DSP stays under the Surge deadline** — here it does not.

**Contrast — accidental first V8-b run (invalid):** patch picker bug → 3 voices, ~39% DSP, **0 xruns** on both ×2 and ×3. Too light to test the question; superseded by Cloud Horn redo above.

---

## What this retires / confirms

| item | outcome |
|---|---|
| Single-patch V7 ceiling as universal | **Retired** — spread is **3–15+** across Quick Select |
| Gods as heavy anchor | **Retired** — ≥15 voices at 1024×3 |
| nperiods=2 as xrun fix at playable load | **Retired** — same overruns as ×3 at 7-voice hold |
| nperiods=2 latency win | **Confirmed** (W0/V3 geometry) — shipping candidate **if** voice policy keeps DSP under ceiling |
| Kernel / USB / buffer as binding | **Still dead** — all xruns graph-side, compute-bound |

---

## Could not measure

| item | why |
|---|---|
| True capacity > 15 | `midi-load-hold` + ramp cap at 15 MPE channels |
| Per-patch confirm windows | Time budget; 8 s probe only |
| Some fxp metadata | XML parse errors on a few patches (e.g. Cyber Pad); capacity still measured |

---

## Artifacts

- `/home/mitch/plan-v8-20260821-225953/v8a-survey.log`
- `/home/mitch/plan-v8-20260821-225953/v8b-1024x2-redo.log`
- `/home/mitch/plan-v8-20260821-225953/v8b-1024x3-redo.log`
- `/home/mitch/plan-v8-20260821-225953/plan-v8.log`

## Harness

```bash
sudo ./scripts/measure-plan-v8.sh
sudo ./scripts/measure-v8b-playable.sh --patch-name "Cloud Horn" --voices 7 \
  --artifact-dir /home/mitch/plan-v8-YYYYMMDD-HHMMSS
```

Branch: `docs/v8-patch-capacity` → merge to `dev` when ready.
