# On-device looping — plan

*Last updated: 2026-08-10 20:24 (America/Toronto)*

**The question, in one line:** *What is the cheapest architecture that turns this box into a looper good enough to replace the RC-5 — and what do we build first?*

**Status:** Phase 0.2 **pass** — 10 min passthrough @ 512, 0 xruns (silent path). Still need Surge→Loopback, live signal, loop mix, latency A/B. PR #48.

**Related:** [`LATENCY-SPIKE.md`](LATENCY-SPIKE.md) · [`MIDI-CLOCK.md`](MIDI-CLOCK.md) · [`USB-SESSION-RECORD.md`](USB-SESSION-RECORD.md) · [`PATCH_NORMALIZATION.md`](PATCH_NORMALIZATION.md)

---

## Product framing

The device is ~**$600 of fixed hardware** where the software is the value. The pitch is not "cheaper than a pedal" — it is **one box replacing a stack**: MPE sound module + looper pedal + patch librarian + level management, driven off a $150 controller.

That makes looping **the thing that turns the box into a complete instrument** rather than a nice extra. It also demotes the RC-5 from "the sync master we design around" to "an optional peer for people who already own one."

**Hard requirement:** looping must work **standalone** — no laptop, no pedal, no network.

---

## Required behaviour (decided, not open)

These two constraints came from the intended workflow and they **determine the architecture**:

1. **A recorded clip is frozen at record time.** Record a phrase, then browse and change patches on screen to play something new on top. **The recorded loop must not change sound.** This is analog-pedal semantics: it records what came out, not what you pressed.

2. **No per-layer patch assignment in scope.** Assigning a patch per MIDI channel is an Ableton/clip-style workflow. It might be a mode someday; it is **not** what this instrument is for, and building for it now buys complexity nobody asked for.

Everything below follows from those.

---

## Evidence this is now feasible (2026-08-10)

The looper question was previously **closed** by [`LATENCY-SPIKE.md`](LATENCY-SPIKE.md): at a 1024-sample floor (~21 ms), loop buffers stacking on top were judged unusable. Tonight's session reopened it.

| Finding | Value | Consequence for looping |
|---|---|---|
| Buffer floor | **256 played acceptably** (~5.3 ms), 512 arguably *best feel* | The ~21 ms premise is gone; there is real headroom to spend |
| xruns | **None** at 1024 / 768 / 512 / 256 | Not underrun-bound today |
| Power / thermal (Arm A0) | `throttled=0x0` before and after jam, peak **60.3 °C** | Historic `0x50000` did not recur — clean baseline |
| Realtime | **Never enabled** — `SCHED_OTHER` prio 0, `ondemand` governor | Governor, RT priority, `threadirqs`, RT kernel are **all still unspent** |
| `usb-host` @ 256 | Comparable to standalone | Desk path holds too |
| **Attenbourg → drums** | Will not voice at **any** buffer | **CPU / voice ceiling**, not a buffer problem |

**Epistemic status: L1/L2, not L3.** Casual multi-minute jams, not the 10-minute worst-case soak `LATENCY-SPIKE.md` defines as passing. A.4b (scheduling latency under load) was **not run** — `rtla` is not installed. "256 works" is a promising hypothesis, not a validated production floor.

**The load-bearing conclusion:** the binding constraint on this box is **CPU / voice count**, not block latency. Attenbourg drums failing at every buffer is the cleanest evidence. **Any looper design that multiplies polyphony is fighting the actual constraint.**

---

## The architectural fork

### Decision: loop audio (Track A)

Record Surge's **audio output** into memory; mix recorded layers back into the output.

**Why, on both counts that matter:**

| Requirement | Audio looping | MIDI looping |
|---|---|---|
| **Clip frozen at record time** | **Native** — a loop is PCM. Changing patches afterward cannot alter it. | **Broken by construction.** Layers are re-synthesized by whatever patch is loaded, so browsing patches rewrites your recording. Fixing it requires per-layer patch pinning — i.e. the clip-style workflow explicitly out of scope. |
| **CPU cost of N layers** | **Nearly free.** Mixing 8 stereo layers at 256 frames is ~4 k float adds per period — microseconds, memory-bandwidth bound. | **Worst case for this box.** Each layer re-synthesizes its own MPE polyphony. Replaying layers multiplies voice count against the exact ceiling we just measured. |

**On the "8 channels" instinct — correct, with a precision.** The cost driver isn't channel count, it's **simultaneous voices**. MPE assigns one channel per held note (the Roli uses most of 2–16), so a replayed layer re-triggers every note it recorded, and layers **sum**: 3 layers of 6-note chords is 18 voices Surge must render live, on top of whatever you're playing. Audio playback of those same 3 layers costs one add per sample. The instinct that MIDI replay "seems really difficult and not good" is right, and it's the same wall Attenbourg drums already hit.

**What audio looping also buys:** it can loop **anything**, including the Sound Blaster mic input — the guitar/vocal path already wired for `usb-host-session`. MIDI looping structurally cannot.

**What it costs:** a real audio pass. That is the honest tradeoff and the rest of this document is mostly about containing it.

### Deferred: MIDI clip mode

Not rejected on merit — deferred as **the wrong first product**. If per-layer patches, re-quantize, or post-hoc editing ever become the ask, MIDI capture slots cleanly into `mpe-pressure-remap.py` (already a MIDI middleman with quantize and offset via `patch_browser/midi_sync.py`). Revisit only on explicit demand.

### Rejected / deferred alternatives

| Option | Verdict |
|---|---|
| **Packaged OSS looper app** (SooperLooper, Mobius, Giada) | **Rejected for MVP.** All assume JACK/PipeWire plus a GUI or plugin host. Adopting JACK is an architecture change to a stack deliberately built on direct ALSA, and it lands its own latency and ops cost. Revisit as a *library*, not an app. |
| **`ecasound` / ALSA chain tool** | **Spike-only.** Useful to prove the chain and CPU behaviour in an afternoon; awkward to productize behind a touch transport. |
| **Custom ALSA plugin** (`extplug` / LADSPA in the plug chain) | **Deferred.** Theoretically the lowest-latency topology (loop mixing inside Surge's own callback, no extra stage) and the hardest to debug. Only if Phase 1 misses its latency target. |
| **Host/DAW-side looping** (`usb-host`, `usb-host-session`) | **Already exists, keep.** Requires a computer, so it is not the standalone story. |
| **RC-5 as loop engine** | **Demoted, not deleted.** `midi-clock-in` + quantize stays supported for owners; no longer the design center. |

---

## Track A architecture

### The latency problem, stated before designing around it

Audio looping needs Surge's output *and* the DAC. The naive chain puts the looper **in series**, which taxes **live playing**, not just playback:

```
Surge → snd-aloop → [looper mixes] → DAC        ← live play now pays every stage
```

| Per-stage buffer | Live-play total (≈3 stages) | Verdict |
|---|---|---|
| 1024 | ~64 ms | Unusable |
| 512 | ~32 ms | Poor |
| 256 | ~16 ms | Playable, no margin |

The alternative puts the tap **in parallel**: Surge keeps writing straight through to the DAC, and a copy of its output feeds the looper.

```
                    ┌→ DAC  (live — one buffer, unchanged)
Surge → multi/dmix ─┤
                    └→ snd-aloop → [looper] → back into the DAC mix (playback only)
```

Live latency stays at Surge's own buffer (~5–11 ms). Only **loop playback** is delayed, and that delay is a **fixed, measurable offset** — schedule playback that much earlier and the loop lands on the grid. Cost: an `.asoundrc` mixing chain (`dmix` plus a copy branch), more per-callback work, and format-conversion care.

**Plan:** prove the **series** version first because it is one process and no ALSA voodoo, measure the live-latency delta honestly, then move to the **parallel** topology if live feel regresses. Do not build parallel first on the assumption it is needed.

### Loop engine

| Concern | Decision |
|---|---|
| **Storage** | RAM ring buffer per layer, `float32` or `int16` stereo. 4 bars @ 120 BPM ≈ 8 s ≈ **1.5–3 MB per layer** — irrelevant on this Pi. |
| **Language** | Prototype the mix loop in **Python + numpy** (matches the repo). If it xruns at the target buffer — GIL and GC pauses are the realistic failure mode — port **only the mixer** to a small C binary. Decide on measurement, not taste. |
| **Mixing** | Sum layers plus live passthrough, then **one output gain stage**. Stacking layers clips; see §Gain staging. |
| **Clock** | Pi as clock master for looping (`midi-clock-out` exists, currently env-only). Loop length in **bars**. Follow external clock when `midi-clock-in` is running (RC-5 owners). |
| **Boundaries** | Loop wrap must be **sample-accurate and click-free** — align to the period grid, and crossfade a few ms at the seam. |

### Gain staging

The repo already solved per-patch loudness (`PATCH_NORMALIZATION.md`), and stacking four normalized layers will clip a 0 dBFS output. The looper needs its own headroom policy: record at unity, mix with per-layer gain, and keep an output ceiling. **This is a design item, not an afterthought** — the box has no output limiter today by deliberate choice.

### Integration points (concrete, in this repo)

| Touchpoint | Change needed |
|---|---|
| `scripts/lib/unload-snd-aloop.sh` | Surge start currently **unloads** `snd-aloop` to save overhead. Must become conditional when the looper is enabled. |
| `scripts/detect-audio-device.sh` | Tiering picks the Sound Blaster (Tier 1). With the looper enabled, Surge must target **Loopback** instead — a new tier or profile, not a hack. |
| `MPE_AUDIO_PROFILE` | Interaction with `usb-host` / `usb-host-session` must be defined. MVP: looper supported in **`standalone` only**; other profiles unchanged. |
| `patch_browser/calibration_loopback.py` | Existing, working `snd-aloop` capture patterns — reuse rather than reinvent. |
| Poly governor | Unchanged, but now shares CPU with the looper process. Watch it during Phase 1. |

### Transport UI

Touch-first, following existing modal/settings patterns (`touch_ui_enums.py` `Screen`, `touch_browser_*`):

| Control | Behaviour |
|---|---|
| **Record** | Arms; starts at the next bar (immediately if unsynced) |
| **Overdub** | Adds a layer on the existing grid |
| **Play / Stop** | Transport, quantized to bar |
| **Clear** | Current layer (tap) / whole loop (long-press) |
| **Layer list** | Mute, delete, per-layer gain |
| **HUD** | Reuse the existing looper badge — bar position, layer count, record state |

Foot pedal is the **second** input, not the first: `pedal-to-osc.py`'s `PEDAL_MAPPING` already supports arbitrary targets, so transport is additive. **APC mini** Scene Launch → looper transport via `patch_browser/control_surfaces/` (`ControlSurfaceMap` registry; mk1 + mk2 maps). Default **mk1** (`MPE_APC_VARIANT=mk1`): Scene Launch **82–89**; mk2: **112–119** + Session SysEx.

---

## Phases

Stop and re-evaluate at each gate.

### Phase 0 — Audio-path spike (~1 day, throwaway code)

The only question that matters: **can a second process sit in the audio path without wrecking feel or stability?**

| Step | Action | Answers |
|---|---|---|
| 0.1 | Surge → `snd-aloop`; a script reads loopback and writes to Sound Blaster (passthrough only, no looping) at **512** | Does series passthrough even run clean? |
| 0.2 | Measure **xruns** (log + `/proc/asound/*/status`) and CPU over 10 min | Stability floor of the new stage |
| 0.3 | Measure **live-play latency delta** vs Surge-direct (clap/loopback test or honest A/B by feel) | The real cost of series topology |
| 0.4 | Add naive record + playback of one bar, mixed with live | Does the concept feel right at all? |
| 0.5 | Repeat 0.1–0.3 at **256**, and in **Python vs a C mixer** if Python xruns | Buffer and language decisions, on evidence |

**Gate:** series passthrough holds 10 minutes xrun-free at ≤512 **and** the live-latency delta is acceptable → continue with series. If not → design the parallel `dmix` topology before writing product code.

### Phase 1 — Loop engine

| Deliverable | Notes |
|---|---|
| `patch_browser/looper_engine.py` (or C mixer + thin Python shell) | Ring buffers, layer mix, wrap/crossfade, gain staging |
| `tests/test_looper_engine.py` | Pure-logic tests on buffers/arrays — no hardware, runs in CI |
| Bar/tick alignment | Reuse `PPQN` and grid math from `midi_clock.py` / `midi_sync.py` |

**Gate:** tests green; a scripted record/overdub/play cycle runs on the Pi, in time, xrun-free.

### Phase 2 — Daemon + transport

| Deliverable | Notes |
|---|---|
| `scripts/mpe-looper.py` | Daemon (model: `surge-poly-governor.py`) |
| `config/mpe-looper.service` | **Off by default**; ordering after `surge-xt-cli.service` |
| State file | `~/.mpe_looper_state.json` for UI polling (model: `~/.mpe_midi_clock_state.json`) |
| Audio routing switch | Conditional `snd-aloop` + Surge device selection, driven by `MPE_LOOPER_ENABLED` |
| Pi-master clock | Promote `midi-clock-out` from env-only to a supported mode |

**Gate:** full record/overdub/play/clear over SSH on the Pi; enabling and disabling the looper leaves the gig path byte-identical.

### Phase 3 — Touch UI + persistence

| Deliverable | Notes |
|---|---|
| Transport screen + HUD | New `Screen` enum, draw/input handlers |
| Layer management | Mute / delete / per-layer gain |
| Loop save/load | WAV or raw per layer under `~/.mpe_loops/` |
| Foot pedal transport | Optional mapping in `pedal-to-osc.py` |
| Docs | This file → operator guide; `README.md` feature bullet |

**Gate:** Mitch builds a 3-layer loop live — hands on the Roli and the screen, **changing patches between layers** — no SSH.

### Phase 4 — External input (only when asked)

Loop the Sound Blaster **mic input** (guitar, voice) alongside Surge. The capture plumbing already exists for `usb-host-session` (`patch_browser/session_capture.py`), so this is mostly routing and gain, not new architecture. This is where audio looping pays off versus every MIDI design.

---

## Config surface (all default OFF)

| Variable | Default | Meaning |
|---|---|---|
| `MPE_LOOPER_ENABLED` | `0` | Master switch (also gates `snd-aloop` + Surge device routing) |
| `MPE_LOOPER_BPM` | `120` | Fallback tempo when nothing sets one |
| `MPE_LOOPER_BARS` | `4` | Default loop length |
| `MPE_LOOPER_MAX_LAYERS` | `8` | Guard on memory and mix headroom |
| `MPE_LOOPER_BUFFER_SIZE` | inherit | Looper period; defaults to `MPE_SURGE_BUFFER_SIZE` |
| `MPE_LOOPER_OUTPUT_GAIN_DB` | `0` | Output ceiling / headroom control |

Existing `MPE_MIDI_*` and `MPE_SURGE_*` semantics are unchanged. **Pulling this work must change nothing until `MPE_LOOPER_ENABLED=1`.**

---

## Testing strategy

```bash
python3 -m unittest discover -s tests -q          # all tests, before every PR to dev
```

| Level | What | Where |
|---|---|---|
| **Unit (CI)** | Ring buffer wrap, layer mix, gain/clip policy, bar-length math, crossfade seam | `tests/test_looper_*.py` |
| **Unit (CI)** | State file read/write, env parsing, routing decisions (`MPE_LOOPER_ENABLED` on/off) | same |
| **Manual (Pi)** | xruns, live-latency delta, timing feel, 10-min soak, patch-change-during-loop | Validation log below |

There is no ALSA or MIDI in GitHub Actions, so keep every device dependency behind an injectable seam (the `surge_audio` / `session_capture` pattern) and the logic stays CI-testable.

---

## Boundaries

**Always**

- Ship looper features **off by default**; the gig path must be unchanged when disabled
- Run the full suite before any PR to `dev`
- Keep buffer/mix logic pure and unit-tested; keep device I/O at the edges
- Measure xruns and live latency **before** claiming a topology works

**Ask first**

- Any change to `detect-audio-device.sh` tiering or the live ALSA output path
- Making `snd-aloop` load at boot
- New systemd units enabled by default
- Changing production `MPE_SURGE_BUFFER_SIZE` / `MPE_SURGE_SAMPLE_RATE` on the live Pi
- Dependencies heavier than `numpy` / `python-rtmidi` / `python-osc`, or introducing a compiled component

**Never**

- Merge to `main` before a Pi soak on `dev` (see [`GIT-WORKFLOW.md`](GIT-WORKFLOW.md))
- Put the looper in the live output path without a written latency measurement
- Adopt JACK/PipeWire as a side effect of a looper task
- `scp`/`rsync` experiment code to the Pi — deploy through git only

## Human gates (Mitch)

- **Topology choice** — series vs parallel `dmix`, after Phase 0 numbers; record in `DECISIONS.md`
- **Python vs compiled mixer**, if Phase 0.5 forces it
- **Promoting Pi-as-clock-master to default** (changes RC-5 owners' behaviour)
- **Production buffer/rate change** on the live Pi
- **Enabling `mpe-looper.service` by default** — only after a clean soak

---

## Risks

| Risk | Severity | Mitigation |
|---|---|---|
| **New audio stage xruns** — tonight's clean numbers were Surge *alone* | **High** | Phase 0 measures before any product code; C mixer fallback |
| **Live-play latency regression** from series topology | **High** — kills the thing that made this feasible | Measure in 0.3; parallel `dmix` topology as the answer |
| **Clipping** when stacking normalized layers | Medium (sounds amateur) | Explicit gain staging + output ceiling in Phase 1 |
| Python realtime jitter (GIL/GC) at 256 | Medium | Prototype at 512; port mixer to C on evidence |
| Clicks at the loop seam | Medium | Period-aligned wrap + short crossfade; test coverage |
| Routing change breaks the gig path when looper is **off** | Medium | `MPE_LOOPER_ENABLED=0` must be a no-op; test both branches |
| 256/512 not actually stable under real load | Medium | Structured 10-min soak before any default change; 1024 stays the fallback |
| Scope creep toward RC-5 parity | Medium | MVP = one loop, N layers, no undo history, no multi-slot |

---

## Open questions

1. **Loop length definition for MVP** — tap tempo, first-loop-sets-length, or fixed BPM?
2. **What replaces "undo"?** Per-layer delete may be enough; RC-5 users expect undo/redo.
3. **Is external-audio looping (Phase 4) actually needed** for the $600 pitch, or is "loop the synth" the honest scope?
4. **Does the looper need to work in `usb-host`**, or is `standalone`-only acceptable for v1?

---

## Validation log

| Date | Phase | Result | Notes |
|---|---|---|---|
| 2026-08-10 | 0.0 | Spike code landed | `looper_engine`, `looper_devices`, `looper_xruns`, `scripts/looper-phase0-spike.py` — CI + Pi unit tests green |
| 2026-08-10 | 0.1 | **L1 pass (partial)** | Pi: 30s passthrough @ 512, **0 xruns**, 2812 periods — loopback capture silent (Surge not on Loopback yet); fixed ALSA buffer=2×period |
| 2026-08-10 | 0.2 | **L1 pass** | Pi: **600s** passthrough @ 512, **0 xruns**, 56250 periods, 0 short_reads — silent loopback path |
| | 0.3 / 0.4 | | Surge→Loopback + live signal + loop mix + latency A/B |

## Deploys

Repo → Pi **through git only**: land on `dev`, soak on the Pi, then promote to `main` per [`GIT-WORKFLOW.md`](GIT-WORKFLOW.md). No direct copies to the appliance.
