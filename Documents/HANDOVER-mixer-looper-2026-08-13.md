# Handover — mixer & looper work (post Phase 1 soak)

> **⚠ SUPERSEDED 2026-08-13** — TL;DR tracks (Tasks 1–4, whole #48 merge) are
> **wrong**. Read [`DECISIONS.md`](DECISIONS.md) and [`DIRECTION.md`](DIRECTION.md)
> instead. Below: useful **Phase 1 landing notes** (#56–#59, Pi smoke) only.

*Last updated: 2026-08-13 (America/Toronto)*

**Audience:** next agent session or Mitch picking up Phase 2 / mixer product work.  
**Pi integration branch:** `dev` @ `d01d9c3`+ (deployed and smoke-tested 2026-08-13; doc handover commit follows).  
**Release line:** `main` is **not** promoted yet — soak `dev` first.

---

## TL;DR — what to do next

| Track | Start here | Blocked on |
|-------|------------|------------|
| **Looper Phase 2** | Laptop **Tasks 1–4** in [`looper-jack-client-spec.md`](specs/looper-jack-client-spec.md) (mixer fixtures + NumPy parity) | Nothing — can start on `dev` tonight |
| **Looper Phase 0 merge** | **Task 0:** rebase [`yolo/looper-phase0`](https://github.com/MitchSchwartz/MPE-Sound-Module/pull/48) onto current `dev`, wire D5 guard in `mpe-looper.py` `main()` | Agent rebase; **Mitch:** §D.4 guard boot test on Pi → merge PR #48 |
| **Product “mixer”** | **Mitch decision first** — see §Two mixers below | No spec in repo; not a code blocker for looper Tasks 1–4 |

**Do not** start JACK callback client work (Tasks 7–11) until PR #48 is merged and Task 5 rewires the looper engine off `bytes`/`audioop`.

---

## What just landed on `dev` (2026-08-13)

Phase 1 JACK graph is **done and Pi-validated** for operator settings changes:

| PR | What |
|----|------|
| [#56](https://github.com/MitchSchwartz/MPE-Sound-Module/pull/56) | Planned promote path (`mpe_promote_surge_planned`), touch overlay/toasts, 30s cooldown |
| [#57](https://github.com/MitchSchwartz/MPE-Sound-Module/pull/57) | shellcheck CI gate |
| [#58](https://github.com/MitchSchwartz/MPE-Sound-Module/pull/58) | Watchdog defers supervisor restart when `/run/mpe/planned-promote` is set |
| [#59](https://github.com/MitchSchwartz/MPE-Sound-Module/pull/59) | `jack_lsp` as graph owner under `sudo` (fixes 30s false `no-server` timeout) |

**Pi smoke (sample-rate toggle):** ~**5s** each way, `state=ok`, Surge on graph, **146/146** `mpe test pi audio`.

**Known follow-up (not blocking):** second audible dropout during **post–settings OSC patch reload** (“Patch loaded” toast) — [`jack-audio-engine-spec.md`](specs/jack-audio-engine-spec.md) backlog + [`docs/TOUCH_PATCH_BROWSER.md`](../docs/TOUCH_PATCH_BROWSER.md) Known gaps.

---

## Two different “mixers” (do not conflate)

### 1. Touch UI per-patch mixer — **shipped**

Vol / Tail / Touch / Norm faders on the patch detail pane. Code: `patch_browser/mixer_controls.py`, `touch_browser_mixer.py`, `mixer.py` (layout model only).

This is **not** Phase 2 looper work. It controls Surge via OSC/sidecars, not the JACK graph.

### 2. Looper audio mixer — **Phase 2 engineering**

`mix_live_and_loops` on branch **`yolo/looper-phase0`** (`looper_engine.py`) — `audioop` today, must become **swappable NumPy backend** with zero allocation in the JACK callback.

Spec: [`Documents/specs/looper-jack-client-spec.md`](specs/looper-jack-client-spec.md) §A–§C, Tasks **1–4** (laptop), **5+** (needs PR #48 merged).

### 3. Product “sound-module mixer” — **no spec yet**

Reviews flagged naming collision with per-patch faders vs a future **multi-channel / analog-style** surface. [`Documents/reviews/synthesis-2026-08-13.md`](reviews/synthesis-2026-08-13.md) §Product decision: decide whether that rides on the JACK insert (Phase 2 dependency) or is a separate UI — **before** building more engine surface.

**Recommendation:** finish looper **Tasks 1–4** (provably neutral mixer swap) while Mitch decides product mixer scope in parallel.

---

## Looper — current state

| Item | Status |
|------|--------|
| Governing Phase 1 spec | [`jack-audio-engine-spec.md`](specs/jack-audio-engine-spec.md) — Gate C soak **PASS** on amendment branch; merged path now on `dev` |
| Phase 2 design spec | [`looper-jack-client-spec.md`](specs/looper-jack-client-spec.md) — **Draft**; task table §Task Breakdown |
| Phase 0 code | Open PR [#48](https://github.com/MitchSchwartz/MPE-Sound-Module/pull/48) → `yolo/looper-phase0` @ `811b6cc` — **76 commits ahead of old `dev` base; must rebase onto current `dev`** |
| D5 guard policy | On `dev`: `engine-guard.sh` + `patch_browser/audio_engine.py` — looper refused when `MPE_LOOPER_ENABLED=1`; HUD `looper=guarded` |
| Authoritative guard chokepoint | **`mpe-looper.py` `main()`** — **not on `dev` yet**; Task 0 adds it on rebased phase0 branch |
| Runtime today | With default `engine=jack`, enabling looper does **not** run ALSA pipeline — guard exits cleanly |

### Merge order (locked in spec §D.4)

1. Phase 1 already on `dev` ✓  
2. **Task 0:** rebase `yolo/looper-phase0` → current `dev`, resolve conflicts in legacy paths Phase 1 rewrote (`start-surge-cli.sh`, udev, etc.), add `main()` guard  
3. **Mitch Pi test** (§D.4 — ~20 min): `MPE_LOOPER_ENABLED=1`, reboot, expect `looper=guarded`, **one** refusal, **no** `mpe-looper` restart loop → merge **#48**  
4. Strike ALSA-pipeline soak items from #48 checklist (Phase 2 deletes that path)

### Phase 2 task sequence (after #48 merged)

**Laptop now (Tasks 1–4, 6):**

| Task | Deliverable |
|------|-------------|
| 1 | `tests/fixtures/mixer_cases.py` + golden `.npz` |
| 2 | `patch_browser/looper_mix.py` — NumPy int16 bit-exact vs `audioop` |
| 3 | Float32 backend + `MixWorkspace` + tracemalloc zero-allocation proof |
| 4 | `LoopRing` with `read_into` / `write_from` |
| 6 | `docs/measurements/` scaffold + §C.3 template |

**After #48 (Task 5):** rewire `ClipMatrix` / `LooperSession` — delete `bytes` mixer path.

**Pi hardware (Tasks 7–11):** `python3-jack-client`, JACK callback insert topology (§B.3), fail-open criterion 19, GC discipline, **§C.3 measurement run** → verdict ship Python vs compiled kernel.

**Task 12:** remove D5 guard only if Task 11 says ship.

**Human gates (Mitch only):** Task 0 merge + guard boot, Task 7 `apt install`, Task 11 measurement verdict, Task 12 guard removal.

---

## Mixer work — if you mean Phase 2 audio mixer

Start with **Task 1** (fixtures). Cheapest falsification: if NumPy cannot match `audioop` bit-exact, criterion 8 must be re-scoped before any Pi work.

Tests target: `mpe test local looper` (register suites in `mpe-cli` when added).

Existing touch mixer tests: `tests/test_mixer_controls.py` — unrelated to looper mix parity.

---

## Mixer work — if you mean product multi-channel UI

**Blocked on product spec.** Open questions:

- Is it a JACK-graph insert UI (same process as looper)?
- Separate surface from per-patch Vol/Tail/Touch?
- Does it require Phase 2 topology (Surge → looper → DAC)?

Write a short spec in `Documents/specs/` after Mitch decides — synthesis doc item 17.

---

## Pi & repo ops

```bash
# Deploy latest dev
mpe looper deploy dev          # git pull on Pi
mpe restart watchdog           # after watchdog/audio-engine script changes
mpe engine sync-units            # after unit file changes
mpe engine status
mpe test pi audio              # 146 tests @ d01d9c3
```

**Branch policy:** [`docs/GIT-WORKFLOW.md`](../docs/GIT-WORKFLOW.md) — feature → `dev` → Pi soak → **`main`**.

**Open PRs:** only [#48](https://github.com/MitchSchwartz/MPE-Sound-Module/pull/48) (looper phase0).

**Agent rules:** use **`mpe` CLI** for Pi ops; no raw SSH unless blocked. Staging: Pi checks out branch, `./scripts/configure-pi-paths.sh --local --force`.

---

## Spec & review index

| Doc | Purpose |
|-----|---------|
| [`jack-audio-engine-spec.md`](specs/jack-audio-engine-spec.md) | Phase 1 canon + Gate C results + backlog |
| [`looper-jack-client-spec.md`](specs/looper-jack-client-spec.md) | Phase 2 design + task breakdown |
| [`synthesis-2026-08-13.md`](reviews/synthesis-2026-08-13.md) | Review merge + prioritized backlog |
| [`docs/TOUCH_PATCH_BROWSER.md`](../docs/TOUCH_PATCH_BROWSER.md) | Touch UI + audio settings UX |
| [`docs/GIT-WORKFLOW.md`](../docs/GIT-WORKFLOW.md) | Branches + Pi testing |

---

## Suggested first session plan

1. **Agent:** rebase `yolo/looper-phase0` onto `dev` (Task 0) — do **not** merge until Mitch runs §D.4 boot test.  
2. **Agent (parallel):** Task 1 mixer fixtures on `yolo/looper-mix-fixtures` from `dev`.  
3. **Mitch:** §D.4 guard boot → approve #48 merge.  
4. **Mitch (optional):** product mixer one-pager decision.  
5. **Later:** promote `dev` → `main` after extended Pi gig soak (not required to start Tasks 1–4).

---

## Out of scope for this handover

- **`main` promotion** — wait for Mitch confidence on `dev`  
- **usb-host profile** looper — spec Non-Goal (`standalone` only)  
- **Compiled mix kernel** — only if Task 11 measurement fails Python deadline  
- **Touch UI toast/patch-reload gap fix** — backlog only
