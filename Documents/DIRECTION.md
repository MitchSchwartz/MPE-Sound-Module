# Phase 2 direction — read before looper work

*Last updated: 2026-08-13 (America/Toronto)*

**Locked decisions:** [`DECISIONS.md`](DECISIONS.md)  
**Deep canon (branch audit, open questions):** OM-Repo [`GROUNDING.md`](https://github.com/opsMachine/OM-Repo/blob/main/internal/projects/mpe-synth-launch/GROUNDING.md)

---

## Python and audio

| Layer | Python? |
|---|---|
| Touch browser, OSC to Surge, pedal bridge, calibration, systemd scripts | **Yes** |
| JACK realtime callback (mixing, looping, DSP) | **No — ever** |

Phase 0 (`arecord`/`aplay`) and the drafted Python JACK client spec are **retired
paths**, not the plan.

---

## Next step — SooperLooper Pi test only

Run on **current `dev`** — the only Phase 2 experiment that does not disturb the
appliance. Plan: [`looper-vetting.md` §7](https://github.com/opsMachine/OM-Repo/blob/main/internal/projects/mpe-synth-launch/research/looper-vetting.md)
(~4 h **timebox**; if source build fights past ~4 h, that is the maintenance-cost
answer).

**Must prove explicitly (do not infer from docs):**

1. **B1 — `dry=0` removes passthrough** — fail-open story rests on this
2. **B7 — CPU/xruns beside Surge** — not clean in isolation
3. **B10 — 20-min play** — EDP model vs what you want from the instrument

**Audio input in/out:** during the same session, plug mic/guitar into the
interface for ~10 min — decide looping-pedal vs output-only by playing, not
whiteboard-first.

**Dropped:** checking out `yolo/looper-phase0` on the Pi — predates JACK; would
downgrade to the ALSA stack #50 removed. UX signal is B10 above.

**Do not start:** NumPy mixer Tasks 1–4, whole PR #48 merge, Python callback Tasks 7–11.

---

## Phase 2 options (simple comparison)

### A. Adopt SooperLooper — **try first**

Existing C++ JACK looper. Engine runs headless; our UI talks **OSC** (same pattern
as Surge today). Zynthian ships this on Pi + JACK + Python UI.

| Pros | Cons |
|---|---|
| Overdub, undo, multiply, persistence — free | Not in Debian trixie — source build + maybe patch |
| Free-form **or** tempo-synced loops | EDP model ≠ approved APC clip-grid UX |
| Fail-open wiring possible (Surge → DAC direct) | Last upstream release 2023; 28 open issues |
| Full OSC command set for record/overdub/undo/clear | **Unverified on this Pi** — must run test plan |

**Kill if:** won't build in ~4 h, can't fail open, adds xruns beside Surge, or
B10 play test feels wrong.

### B. Build our own (compiled JACK client) — **fallback**

C or Rust process in the JACK graph; Python only sends commands.

| Pros | Cons |
|---|---|
| Smallest footprint; full UX control | Largest build — realtime C on stage |
| `APC-LOOPER-UX.md` clip grid survives as-is | You own every bug |
| Fail-open designed in from day one | Months, not weeks |

### C. mod-host + Loopor (LV2) — **distant second adoption**

Plugin host + MIT-licensed looper plugin. MOD Devices uses this pattern.

| Pros | Cons |
|---|---|
| Loopor is MIT | Two unpackaged components |
| Proven on ARM pedals | Socket protocol, not OSC — new plumbing |
| | Single loop per plugin instance; weak multi-loop story |

### Ruled out

| Candidate | Why |
|---|---|
| FreeWheeling | Dead upstream, no OSC, GUI-coupled |
| Luppp | GUI is the app, not in Debian, forces tempo grid |
| Giada | Desktop GUI app, not trixie |
| Ardour | DAW, not a live looper; no distributed headless binary |
| Python JACK client (this repo's draft spec) | **Rejected** — see DECISIONS.md |
| **Play looper-phase0 on Pi** | **Dropped** — ALSA-era branch; unsafe after #50 |

---

## What our Phase 0 code actually does (PR #48, unmerged)

Not the Phase 2 plan — a spike on a path being deleted:

- **Clip grid** (Ableton-style): 8 fixed-length clips, no overdub, no undo
- **Audio:** five-process `arecord` → Python → `aplay` pipeline (~40 ms)
- **Worth keeping if split:** APC control surface, clip matrix *UI model*, touch
  HUD, 96 kHz work — **not** `looper_engine.py` / ALSA I/O
