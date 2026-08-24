# Poly governor v2 — always-on jack model (Pi 5 ear tune)

*Last updated: 2026-08-23 20:20 (America/Toronto)*

**Platform:** Pi 5 · 128×2 @ 48 kHz · Cloud Horn / heavy chords  
**Uncertainty:** U10 (Pi 5 replication) + V7 (patch-aware capacity / audible degradation)  
**Spec:** [`Documents/specs/poly-governor-v2-progressive-spec.md`](../../Documents/specs/poly-governor-v2-progressive-spec.md)  
**Prior:** PR #106 (progressive v2) · G2 (Pi 4 threshold recalibration)

---

## Verdict (preliminary Gate B)

**Best tune (2026-08-23 ~20:20):** Mitch — *"best version yet."* Cloud Horn: no crackle at orange. Crystals @6 (heavy): tiny pop on first governor engage, **no crackle** under sustained load. Stack: always_on + jack + ramp apply + baseline **97** / headroom **3** / rise bias **7**.

| Criterion | Status |
|---|---|
| B1 Crackle vs proc-meter v2 | **PASS** @ Cloud Horn + Crystals (baseline 97, ramp apply) |
| B2 Gradual degradation | **PASS** (ramp apply; Crystals: tiny first-engage pop only) |
| B3 Clean play does not engage limit | **TBD** — holds ceiling − headroom (~61) by design |
| B4 Recovery does not pump | **TBD** |

**Tune history (ear):** 96 pretty good → ramp apply fixes pop → loosen to 99/headroom 2 → orange crackle on Cloud Horn/Crystals → rebalance **97/3/7** → best so far.

**Prior notes:** ~19:45 baseline 96 *"pretty damn good"* · ~20:15 ramp apply *"overall better"* but steal aggressive · over-loosen 98–99 caused crackle.

---

## Hypothesis pivot (ear-driven)

Initial v2 shipped **threshold progressive** mode with optional **proc fallback** when jack `dsp_percent` pegged at 100% while header CPU stayed green.

| Meter path | Crackle under load | Voice steal timing |
|---|---|---|
| **Jack deadline (`dsp_percent`)** | None heard | Too early (emergency / heavy limit while proc still low) |
| **Proc fallback** | Heard (~orange header) | Governor audible late |

**Conclusion:** Jack deadline model is the correct **anti-crackle actuator**. Proc CPU is a misleading control signal on Pi 5 when jack reads pegged. The fix is not to gate the governor behind a soft-start threshold — it is to run **always-on progressive limit** with **baseline loosening** so idle jack peg does not read as full stress.

---

## Design — `always_on` mode (dev @ `e66260e`)

| Mechanism | Behavior |
|---|---|
| **Mode** | `MPE_POLY_LIMIT_MODE=always_on` — continuous curve from boot; no SOFT_START gate |
| **Meter** | `MPE_POLY_GOVERNOR_METER=jack` — auto no longer swaps to proc on peg |
| **Baseline** | `MPE_POLY_JACK_BASELINE` — subtract from raw jack load (linear offset) |
| **Headroom** | `MPE_POLY_MIN_HEADROOM=3` — rest target = ceiling − headroom (~61 @ ceiling 64) |
| **Emergency** | `MPE_POLY_EMERGENCY_XRUN_ONLY=1` — no load≥90 cliff; xrun storm only |
| **Floor** | `MPE_POLY_FLOOR=4` — breaks 64/64 player parity; required for curve span |

**Baseline normalization (commit `e66260e`):** Span formula `(raw−baseline)/(100−baseline)` still reads **100% stress** whenever raw=100 at idle. Replaced with **linear offset** `stress = raw − baseline` so raising baseline loosens hold without changing anti-crackle jack path.

**Tuning dial:** raise `MPE_POLY_JACK_BASELINE` → more voices at idle; lower → tighter anti-crackle.

---

## Pi 5 deploy config (`/etc/mpe/mpe.env`)

```bash
MPE_POLY_GOVERNOR_V2=1
MPE_POLY_GOVERNOR_METER=jack
MPE_POLY_LIMIT_MODE=always_on
MPE_POLY_JACK_BASELINE=97
MPE_POLY_MIN_HEADROOM=3
MPE_POLY_RISE_BIAS_MAX=7
MPE_POLY_RAMP_APPLY=1
MPE_POLY_LIMIT_HARD=100
MPE_POLY_EMERGENCY_XRUN_ONLY=1
MPE_POLY_CEILING=64
MPE_POLY_FLOOR=4
MPE_POLY_RISE_FULL_RATE=65
MPE_POLY_RISE_BIAS_MAX=8
MPE_POLY_RISE_MIN_RATE=20
```

**Repo:** `dev` @ `e66260e` on Pi (`git reset --hard origin/dev`).

---

## Session chronology

| Step | Action | Outcome |
|---|---|---|
| 1 | v2 progressive + proc fallback (`9def36f`, `f1719c6`) | Crackle returned; early steal reduced |
| 2 | Always-on jack model (`801771b`) | Anti-crackle restored; baseline 75 still stepped down hard |
| 3 | Linear baseline fix (`e66260e`) | Pegged raw=100 mappable via baseline offset |
| 4 | Deploy baseline **92** | No immediate 64→4 cascade in journal |
| 5 | Ear rebalance + ramp apply tune | baseline 97/headroom 3/rise 7 — **best version yet** (Cloud Horn clean; Crystals tiny first pop, no crackle) |

---

## Repo anchors

| Commit | Topic |
|---|---|
| `801771b` | `always_on` mode, jack primary, min headroom, xrun-only emergency |
| `e66260e` | Linear jack baseline offset (pegged-meter fix) |
| PR #106 | Ramp-aware progressive v2 (superseded as default mode, retained for A/B) |

---

## Open

1. **Full Gate B** — B3/B4 under daily patches; recovery after sustained overload.
2. **Ramp apply (2026-08-23)** — `MPE_POLY_RAMP_APPLY=1`: while stress rising, OSC limit tracks curve without fade deferral (spread note-on steals). Ear retest Piano Fictions overload gesture.
3. **Promote to `player-env-parity.env`** — only after Gate B; includes floor=4 break.
3. **Auto-learn baseline** — `MPE_POLY_JACK_BASELINE=-1` (40-sample min) vs fixed 96 on Pi 5.
4. **Measurement arm** — structured soak with governor on @ 128×2 (blocked on cooler/PSU for Suite 1).

---

## SR&ED note

Eligible investigation: cross-meter disagreement on Pi 5 (jack peg vs proc green), controller redesign (always-on vs threshold), baseline calibration as loosening lever without sacrificing deadline-aligned actuation. Not routine deploy — ear test drove falsification of proc-fallback path.
