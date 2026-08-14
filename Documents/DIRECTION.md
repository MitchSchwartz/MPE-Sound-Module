# Phase 2 direction — read before looper work

*Last updated: 2026-08-14 (America/Toronto)*

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
(**Session A ~1.5 h, Session B ~3-4 h**; if the source build fights past Session
A's budget, that is the maintenance-cost answer).

**Must prove explicitly (do not infer from docs):**

1. **B1 — `dry=0` removes passthrough** — fail-open story rests on this
2. **B7 (retarget to 16 loops, not 8) — CPU/xruns beside Surge** — not clean
   in isolation. 16 simultaneous is the real design target (§UX below), not
   the vetting doc's original 8.
3. **B10 — play both models back to back** — free-form pedal vs
   grid-imitation (fixed `sync_source`/`quantize`) on the same hardware, same
   session. Since `yolo/looper-phase0` is no longer safe to boot, this is the
   only way to A/B the two feels — see DECISIONS.md 2026-08-14 UX entry for
   why pad-per-loop is the target either way.
4. **NEW — recorded-but-idle memory, separate from playing count.** Preallocate
   loops at varying `-t` (loop-memory seconds) and measure `VmRSS` up to 64
   loops recorded but silent. This is the cheap axis; §Table below.
5. **NEW — per-pad clear.** Confirm `undo_all` on a single loop is a clean,
   audible, in-time erase with the others playing — this is the primary
   corrective gesture in Mitch's play style (no undo stack; clip-clear is the
   "fix a mistake" tool).
6. **NEW — multiply.** `/sl/#/hit multiply` from an idle state and mid-loop;
   confirm it extends cleanly to 2×/4×/8× without an audible seam.
7. **NEW — `pitch_shift` / rubberband.** If Session A's build drops rubberband
   to get past the trixie packaging gap, confirm whether `pitch_shift` still
   works or silently no-ops. Affects whether rubberband becomes a hard build
   requirement.
8. **NEW — gain staging headroom (measure, do not build).** Under B7's 16-loop
   load plus live playing, capture the peak at `system:playback` and how far
   it exceeds 0 dBFS, with and without the per-loop `wet` law. **Do not build
   a limiter during this session** — the loop bus does not exist until the
   looper is adopted, and the limiter gets specced against these numbers
   rather than a guessed ceiling. See §Gain staging below.

**Audio input in/out:** during the same session, plug mic/guitar into the
interface for ~10 min — decide looping-pedal vs output-only by playing, not
whiteboard-first.

**Dropped:** checking out `yolo/looper-phase0` on the Pi — predates JACK; would
downgrade to the ALSA stack #50 removed. UX signal comes from the B10 A/B above.

**Memory table to fill in (recorded-but-idle, item 4):**

| Loops recorded (idle) | `-t` (s) | Predicted `VmRSS` | Measured |
|---|---|---|---|
| 16 | 40 | ~246 MB | |
| 64 | 40 | ~983 MB | |
| 64 | 20 | ~492 MB | |

Predictions are arithmetic from documented `-t` semantics (stereo float32 ×
seconds × 48 kHz), not measured. Note: `-t` sets loop memory only — it does
not bound undo depth the way it would under SooperLooper's native EDP model,
because Mitch's play style doesn't use the undo stack (clip-clear replaces
it). Lower `-t` freely if memory is tight.

**Do not start:** NumPy mixer Tasks 1–4, whole PR #48 merge, Python callback Tasks 7–11.

---

## UX target, if SooperLooper is adopted

**Pad-per-loop, not Zynthian's row-per-loop.** Each APC pad is one
SooperLooper loop, played as an Ableton-style clip grid — up to 16
simultaneous, more recordable-but-idle across the grid. Commands (multiply,
reverse, oneshot, feedback, clear) live on a held-pad shift layer rather than
fixed columns, because our 8×8 grid has room Zynthian's 5×8 Key 25 didn't.
Full reasoning: DECISIONS.md 2026-08-14 "Loop UX."

## Gain staging, if SooperLooper is adopted

**One** limiter JACK client, on the loop bus, **sidechained from live** —
and **nothing in the live path**. The sidechain is the mechanism, not a
refinement: it lets the limiter *see* the live signal without *carrying* it,
so it can hold `live + loops` under 0 dBFS while Surge still runs straight to
`system:playback` through nothing.

No limiter on the live path — if it died, the instrument would go silent,
which is the exact failure PR #50's parallel topology exists to prevent.
Live stays bounded by per-patch normalization to −3 dBFS.

**Build after the verdict, measure first:** Session B measures how far the
16-loop sum actually overshoots; the limiter is then built to spec against
real numbers. Full reasoning, the superseded two-limiter version, and the
deferred watchdog-bypass alternative: DECISIONS.md 2026-08-14 "Master gain."

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

**Kill if:** won't build inside Session A (~1.5 h), can't fail open, adds xruns beside Surge, or
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
