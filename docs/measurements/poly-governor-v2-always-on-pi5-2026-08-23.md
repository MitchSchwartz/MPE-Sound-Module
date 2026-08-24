# Poly governor v2 — always-on jack model (Pi 5 ear tune)

*Last updated: 2026-08-23 20:23 (America/Toronto)*

**Status:** **PAUSED** — Gate B not closed. Further tune / measurement deferred until Pi 5 **active cooler + 27 W PSU** (thermal/voltage baseline will shift the playing field).

**Platform:** Pi 5 · 128×2 @ 48 kHz · Cloud Horn / Crystals / Piano Fictions  
**Uncertainty:** U10 (Pi 5 replication) + V7 (patch-aware capacity / audible degradation)  
**Spec:** [`Documents/specs/poly-governor-v2-progressive-spec.md`](../../Documents/specs/poly-governor-v2-progressive-spec.md)  
**Code:** `dev` @ `db64943` (always_on + ramp apply `07cb32d`)

---

## Verdict (preliminary Gate B — not closed)

**Best tune so far (2026-08-23 ~20:20):** Mitch — *"best version yet."* Cloud Horn @5: clean at orange. Crystals @6: tiny first-engage pop, no crackle under **progressive** overload. Stack: always_on + jack + ramp apply + baseline **97** / headroom **3** / rise bias **7**.

**Later same session (~20:23):** Crackle **still reproducible** with a **step-attack** gesture — ride near top of green, let load subside, then hammer into orange. Progressive continuous ramp → pop not crackle; step attack → crackle. **Gate B B1 not fully PASS.**

| Criterion | Status |
|---|---|
| B1 No crackle under overload | **PARTIAL** — progressive ramp OK at 97/3/7; step attack fails |
| B2 Gradual degradation | **PARTIAL** — ramp apply helps; Crystals tiny first pop |
| B3 Clean play / idle headroom | **TBD** |
| B4 Recovery does not pump | **TBD** |

---

## Gesture findings (ear — drives next test design)

| Gesture | Load shape | Audible result @ 97/3/7 |
|---|---|---|
| **Progressive ramp** | Continuous hard playing, load rises smoothly | Best tune: tiny pop possible (Crystals); **no crackle** |
| **Step attack** | Green peak → subsidence → sudden hammer to orange | **Slight crackle** — governor / rise bias cannot lead enough |
| **Over-loosen tune** (baseline 99, headroom 2) | Sustained overload | Crackle at orange (Cloud Horn, Crystals) |

**Working hypothesis:** Anti-crackle needs **lead time** on the limit curve. Step attacks outrun `dLoad/dt` + rise bias + rate-limited OSC steps even with ramp apply. Progressive ramps use the 1–2s window; step attacks do not.

**Pop vs crackle (actuation vs deadline):**
- **Pop** — first Surge `uber_release` steal when OSC ceiling first bites sounding notes (Task C).
- **Crackle** — JACK deadline miss before limit reduction buys enough DSP headroom.

---

## Hypothesis pivot (ear-driven)

| Meter path | Crackle under load | Voice steal timing |
|---|---|---|
| **Jack deadline (`dsp_percent`)** | Controlled with tight enough tune | Baseline + curve tune steal vs crackle tradeoff |
| **Proc fallback** | Heard when jack abandoned | Governor late |

**Shipped controller:** always_on + jack + baseline offset + **ramp apply** (OSC tracks curve while `dLoad/dt` rising; no fade deferral during rise).

---

## Pi 5 canonical tune (`/etc/mpe/mpe.env`) — frozen until hardware gate

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
MPE_POLY_RISE_MIN_RATE=20
```

**Do not promote to `player-env-parity.env` until Gate B closed on thermally stable hardware.**

---

## Paused — why

1. **PSU / thermal** — Suite 1 and reference replication still blocked (5 V / 3 A PSU, no active cooler). Governor thresholds measured under constrained power may not transfer.
2. **Gesture coverage** — single-patch ear passes insufficient; step-attack crackle found late in session.
3. **Next work** — automated gesture matrix (below) before more manual baseline nudging.

---

## Next: automated governor gesture matrix (not built)

**Goal:** Repeatable load **profiles** (not just patches) so tune regressions are falsifiable without Mitch ear for every commit.

| ID | Profile | Intent |
|---|---|---|
| P1 | Progressive ramp | 2–4 s MIDI density ramp into hold; measure xruns + first limit transition |
| P2 | Step attack | Green plateau → 200 ms quiet → step to max density (reproduces hammer-to-orange) |
| P3 | Sustained ceiling | Hold at target voice count 30–60 s; governor engagements + xrun rate |
| P4 | Recovery | Overload → release → verify limit recovery without pump |

**Instrumentation:** `meter.state` (`dsp_percent`, xruns), governor journal / trace, optional `poly-governor.trace`. **Driver:** extend `scripts/midi-load.py` or soak harness with timed note-density envelopes per profile. **Pass/fail:** pre-register xrun delta + whether limit stepped before xrun burst (per gesture class).

**Blocked same as Suite 1:** run on Pi 5 after cooler + 27 W PSU so results are comparable to U10 replication arm.

---

## Session chronology

| Step | Action | Outcome |
|---|---|---|
| 1 | v2 progressive + proc fallback | Crackle on proc path |
| 2 | Always-on jack + baseline (`801771b`, `e66260e`) | Anti-crackle; early steal |
| 3 | Ramp apply (`07cb32d`) | Piano pop fixed; progressive ramp smooth |
| 4 | Ear tune 96→99→97, headroom, rise bias | Tradeoff surface mapped |
| 5 | Best @ 97/3/7 | Cloud Horn clean; Crystals tiny pop |
| 6 | Step-attack gesture | Crackle returns — **pause** |

---

## Repo anchors

| Commit | Topic |
|---|---|
| `801771b` | always_on mode |
| `e66260e` | Linear jack baseline |
| `07cb32d` | Ramp apply |
| `db64943` | Pause + SR&ED + gesture plan |

PR #106 · [`poly-governor-instrumentation-2026-08-21.md`](poly-governor-instrumentation-2026-08-21.md) Task C (steal on note-on)

---

## SR&ED note

Eligible: cross-meter falsification (jack vs proc); always_on + baseline as loosening lever; ramp apply as actuation-timing fix; **gesture-class dependence** (progressive vs step attack) as new falsifier for “governor tuned” claims. Pause is hardware-gated replication discipline, not abandonment.
