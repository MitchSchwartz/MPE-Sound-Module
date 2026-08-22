# V9 — probe duration + clean 1024×2

*Run: 2026-08-22 (America/Toronto)*  
*Harness: `docs/v8-patch-capacity` @ `091d63b`*  
*Artifacts: Pi `/home/mitch/plan-v9a-20260822-030357`, `/home/mitch/plan-v9b-20260822-031604`*

## TL;DR

- **V9-a falsifier failed** — Closed Hat @ ramp ceiling (15) **overruns on 60 s**; 8 s ramp alone is not enough for cap-hit patches.
- **Crystals @ 3** and **Cloud Horn @ 5**: ramp sustained-clean **matches 60 s confirm** (0 xruns).
- **Cloud Horn knee is ~5–6 voices**, not 7: V9 ramp saw **2 xruns @ 7** on 8 s; V8-b @ 7×45 s was above the knee, not just “duration stretched the same count.”
- **V9-b:** **1024×2 shippable at playable load: yes** — Cloud Horn @ 5 voices, 0 xruns on all 3×45 s runs for both ×2 and ×3 (~57% DSP). **42.7 ms total latency is free** at verified-clean load.

---

## V9-a — duration sensitivity

Config: 1024×3, strict mode, governor OFF, 8 s ramp (step +2), 60 s confirm @ `ramp_sustained_clean`, optional −2 step if confirm overruns.

| patch | ramp clean | 60 s @ ramp | xruns | DSP median | confirm clean | notes |
|---|---:|---|---:|---:|---|---|
| **Crystals** | 3 | 3 | **0** | 38.4% | yes | matches V8-a |
| **Cloud Horn** | 5 | 5 | **0** | 57.0% | yes | V8-a said 7; this run: overrun @ 7 on 8 s probe |
| **Closed Hat** | 15 (cap) | 15 → 13 | **2101 → 1523** | 76–81% | no | censored ramp; true ceiling unknown &lt; 13 |

**Cloud Horn ramp detail:**

```
PROBE voices=5 sec=8 xruns_delta=0
PROBE voices=7 sec=8 xruns_delta=2
sustained_clean=5
```

**Prediction check:** “Cloud Horn and Crystals drop ≥ 2 voices at 60 s vs 8 s ramp” — **not supported as stated.** At the **ramp-derived** count, both hold clean for 60 s. The V8 contradiction is explained by **testing @ 7 when knee is ~5–6**, plus 8 s false negatives (V8-a reported 7 clean when a re-run sees xruns @ 7 on the same 8 s window).

**Closed Hat** is the duration-sensitivity signal: **15 on 8 s probes ≠ playable @ 15 for 60 s.** Survey ceilings at the 15-voice cap remain **upper estimates** until confirmed.

Zero ALSA underruns on all confirms.

---

## V9-b — 1024×2 at verified-clean load

**Pick:** Cloud Horn @ **5 voices** (V9-a: ×3 clean 60 s, 0 xruns).  
**Method:** n=3 × 45 s, condition A, `midi-load-hold.py`.

| config | xruns (3 runs) | DSP median (approx) |
|---|---|---|
| **1024×2** | **0 / 0 / 0** | ~57% |
| **1024×3** | **0 / 0 / 0** | ~57% |

**Verdict:** **1024×2 shippable at playable load: yes** — voice count **5**, patch **Cloud Horn**.

First cell where ×2 and ×3 were compared **below** the overrun knee. V3 and V8-b were both overload arms.

---

## Implications

### Governor / ceilings

- Do **not** apply V8-a **15-voice censored rows** as policy without 60 s confirm.
- **Bounded knee patches** (Crystals, Cloud Horn, …): treat 8 s ramp as **screening**; policy floor should use **60 s confirm** or ramp−margin.
- Crystals @ 3 @ 60 s is **stable** this session — reasonable anchor for heavy patch floor until disproven.

### 1024×2 shipping

At loads where ×3 is verified clean, **×2 does not increase xruns** and does not materially change DSP. Shippable default for latency budget: **1024×2** when confirm passes at that buffer (subject to product choice on cushion vs 21 ms deadline — unchanged).

### Next if needed

- Closed Hat (or any cap-hit patch): binary search / ramp above 15 or longer confirm ladder to find true ceiling.
- Optional: V9-a **confirm @ V8-a voice count** (Cloud Horn @ 7) as explicit regression — expect overrun, documents V8-b without conflating knee vs duration.

---

## Harness

All logs carry `PROVENANCE patch=… hold_voices=…`. V9-a: `scripts/measure-plan-v9a.sh`. V9-b: `scripts/measure-v8b-playable.sh`.
