# Grumpy Dev review — SooperLooper / Looper (Composer subagent)

> **Provenance:** Cursor Composer subagent (`composer-2.5-fast`), session 2026-08-15 (America/Toronto).
> **Agent ID:** `bed22ad6-a5e8-4a72-a7d0-4b1ebb61ac60`
> **Skill:** `grumpy-dev-code-review`
> **Dispatched by:** Workflow Manager session (looper fragility / whack-a-mole investigation)
> **Not the same as:** [`grumpy-review-looper-2026-08-15.md`](grumpy-review-looper-2026-08-15.md) — parallel fresh-context pass on `dev` (broader scope, ran tests, stronger rewrite prescription). Compare both; do not merge blindly.

**Date:** 2026-08-15  
**Scope:** Looper subsystem per user request  
**Prior art:** [`grumpy-review-looper-2026-08-14.md`](grumpy-review-looper-2026-08-14.md) + audit correction at file end (read and incorporated)

### What I read vs sampled

| Read fully | Sampled | Not read |
|---|---|---|
| `apc_footswitch.py`, `sl_grid_sync.py`, `sl_grid_state.py`, `sl_bench_listener.py`, `sl_hud_monitor.py`, `sooperlooper-apc-bench.py`, `looper_hud.py`, `looper_clock_monitor.py`, `sl_hud_state.py`, `midi_clock.py` (looper helpers), `wire-jack-graph.sh`, `tests/test_apc_footswitch.py`, `tests/test_sl_grid_sync.py`, `tests/test_sl_bench_listener.py`, `tests/test_looper_hud.py`, `tests/test_sl_hud_state.py`, `Documents/specs/looper-transport-clock-spec.md` (§K + §J), `Documents/DECISIONS.md` (looper rows), `docs/measurements/sooperlooper-eval-2026-08-14.md` (partial), prior grumpy review + audit | `apc_transport.py`, `apc_grid.py`, `sl-health.py`, `sl_loop_states.py`, touch_browser looper hooks, `engine-guard.sh`, `audio_engine.py` (guard), `scripts/sooperlooper/README.md`, `spike-internal-sync-phase.py`, `tests/conftest.py`, remaining `scripts/sooperlooper/*.sh` | Full superseded `looper-jack-client-spec.md`, SooperLooper C++ source, systemd units, Pi runtime. Unit suite not executed (python blocked in shell). |

---

## 1. First Impressions (The Gut Check)

This no longer looks like a hackathon dump. It looks like a team that got burned on the bench, wrote down what happened, and kept fixing forward.

The good signs are real: module split is sane, docstrings cite engine facts (`engine.cpp`, SL OSC docs), the eval log is unusually honest, `sl-health.py` exists because someone lost an evening to a wedged engine, and the Aug-14 whack-a-mole items (`_master_loop_established_`, deferred trigger threads, loop-0 desubscription) are **gone**. Tests are thoughtful — they encode product behavior (double-tap one cycle, quantize timeout, grid two-state model) rather than chasing coverage.

And yet Mitch's PoC thesis is **half right**. The plumbing layer (JACK wiring, OSC transport, HUD math, eval methodology) reads like engineering. The **control layer** still reads like a prototype that grew one symptom-fix at a time: a 350-line footswitch class with parallel `self.state` and `sl_state`, special-case branches for armed/stop-queued/launch-queued/transition-blink, and grid bookkeeping that only partially connects to engine truth. That's exactly the shape that produces "fixed loop 2, broke loop 0, fixed pad latch, broke grid drop."

Professionals work here — in the docs, measurements, and grid-sync module. The bench still behaves like nobody owns a single state diagram.

---

## 2. Architecture & Structure

**What's improved since the Aug-14 review**

- Loop 0 is no longer "bench-authoritative." `SlBenchStateListener` registers `range(num_loops)` and `sync_from_sl` handles all loops including 0.
- Grid clock model is settled in canon (`DECISIONS.md` 2026-08-15, spec §K): internal sync, first take defines tempo, two grid states (`set_grid_active`), no clip-as-clock.
- Deferred `_ensure_master_playing` / `_schedule_grid_sync` / post-record `trigger` spam — deleted.
- JACK timebase dead code — deleted from tree (DECISIONS says delete; confirmed absent).
- `wire-jack-graph.sh` now counts failures and exits non-zero instead of `|| true` everywhere.

**What's still wrong structurally**

1. **Two state machines, one engine (softened but not fixed).** SooperLooper publishes authoritative state over OSC. The bench still maintains `STATE_IDLE/RECORDING/PLAYING/STOPPED` and routes `_tap()` primarily from `self.state`, while LEDs partially follow `sl_state`. Reconciliation improved; **authority did not move**.

2. **Four-process coordination via files and UDP.** Bench (9951/9953), HUD monitor (9952), touch UI reader, SooperLooper engine — no supervisor, no startup ordering contract beyond shell scripts. Workable for eval; fragile for product.

3. **Import identity split.** `sooperlooper-apc-bench.py` does `sys.path.insert(..., "sooperlooper")` and imports `apc_footswitch`. Tests import `scripts.sooperlooper.apc_footswitch`. Same files, two module paths — fine today, landmine tomorrow.

4. **Grid establishment is split across three places without a closed contract.** `GridState` (Python bookkeeping), `on_grid_established` in bench (OSC tempo + `set_grid_active`), and `sl_grid_sync.apply_grid_sync` (startup defaults). **`eighth_per_cycle` and phase anchor are not wired through establishment** — see §5.

5. **Touch UI is a consumer, not a controller.** `LooperClockMonitor` reads JSON; settings "Sync & timing" opens MIDI sync modal — it does **not** control SooperLooper grid mode. That's fine, but docs/README imply tighter integration than exists.

---

## 3. Code Quality

**Naming:** Good. `GridState`, `set_grid_active`, `QUANTIZE_WAIT_TIMEOUT_S` — intent is readable.

**DRY:** Acceptable in scripts; footswitch LED logic is intentionally verbose for UX reasons.

**Dead / stale code**

- `anchor_phase` imported in `sooperlooper-apc-bench.py` but **never called** — dead import alongside missing behavior.
- `LooperClockMonitor` docstring still says *"Background reader for ~/.mpe_midi_clock_state.json"* while also reading SL HUD — stale.
- `scripts/sooperlooper/README.md` still claims grid syncs to **JACK transport** and HUD reads **JACK transport** — wrong per §K / current `sl_hud_monitor.py`.
- `spike-internal-sync-phase.py` still sets `playback_sync=1` on all loops — contradicts current `sl_grid_sync.py` defaults and post-mortem lessons.
- Duplicate entry point: `sl-hud-monitor.py` is a one-line shim to `sl_hud_monitor.py` — harmless, slightly sloppy.

**Error handling:** Improved. `SlBenchStateListener.start()` fatals on bind failure (good). Quantize wait times out with a logged message (good). `_clear_loop` and grid drop paths still lack consistent occupancy updates (see §5).

**Env knob sprawl:** ~15+ `MPE_*` vars across looper files with no registry; typos in `/etc/mpe/mpe.env` still die as raw `ValueError`.

**Two-phase construction:** `LoopFootswitch.bind()` still required after `__init__`; `_tap()` before bind → `AttributeError`. Tests always bind; production code always binds — low risk, still a footgun.

---

## 4. Code Smells (The Hall of Shame)

### 🔴 `_tap()` still drives transport from bench fiction, not SL state

LEDs increasingly read `sl_state`; **gestures still branch on `self.state`:**

```289:316:scripts/sooperlooper/apc_footswitch.py
        if self.state == STATE_IDLE:
            ...
            self._hit("record")
            self.state = STATE_RECORDING
        elif self.state == STATE_RECORDING:
            ...
            self._hit("record")
            ...
            if self.quantized and not defining:
                self._begin_quantize_wait()
            else:
                self.state = STATE_PLAYING
```

*Fix direction:* Make `_tap()` a thin mapper from `(sl_state, grid phase)` → next OSC command; delete bench `self.state` or reduce it to a cache invalidated on every `sync_from_sl`.

### 🔴 Grid unit not established — `eighth_per_cycle` never sized to the first take

On grid establish, bench sends tempo and activates quantize — **but not cycle length.** `derive_tempo()` can return `bars > 1`; **`bars` is ignored for engine config.**

*Fix direction:* On establish: set `eighth_per_cycle` from `bars`, call `anchor_phase(bpm)` (function exists, imported, unused).

### 🔴 `GridState._occupied` tracking is incomplete — "no clips, no grid" is unreliable

`_clear_loop()` clears via bench without updating occupancy. Test `test_grid_survives_deleting_the_clip_that_defined_it` conflicted with spec §K — **replaced in same session** (see [`review-audit-looper-composer-subagent-2026-08-15.md`](review-audit-looper-composer-subagent-2026-08-15.md)).

### 🟡 `ThreadingOSCUDPServer` — datagram order not guaranteed

### 🟡 `quantized` flag frozen at bench startup

### 🟡 Stale docs lie about the clock (`scripts/sooperlooper/README.md`)

### 🟢 `_osc_send` wrapper never used

---

## 5. Logic & Business Rules

Canon table and edge cases as in subagent output — see full transcript. Key gap: **unit (`eighth_per_cycle`) from first take** is ❌ in implementation.

---

## 6. Test Strategy & Execution

Strengths and gaps as in subagent output. Could not run unit suite in subagent shell (python blocked).

---

## 7. Security & Performance

Localhost OSC; HUD 0.5s writes; eval CPU headroom documented elsewhere.

---

## 8. Developer Experience

`--dump-midi`, `mpe looper sl-*`, eval doc strong; README stale; onboarding > 1 day for control-layer changes.

---

## Verdict

Mitch's instinct is **directionally correct but imprecise**. Aug-14 sins are fixed; what remains is prototype-stage control logic: bench **mirrors** SL state instead of **deriving** behavior, grid establishment sends half the §K contract, occupancy bookkeeping is unreliable. **Do not rewrite the JACK/OSC/eval layer.** **Do** finish the control-layer refactor §K describes: SL-authoritative taps, complete grid establish, consistent occupancy.

The Aug-14 audit was right to reject "rewrite everything" — fixes are hundreds of lines, not thousands — but wrong to imply the control layer is done. It's **better**, not **stable**.

---

## Priority backlog (🔴 only)

1. **Complete grid establishment OSC contract** — `eighth_per_cycle` from `bars`, `anchor_phase(bpm)`, test.
2. **Collapse tap authority to SL state** — refactor `_tap()`; demote bench `self.state`.
3. **Fix grid occupancy + align tests with "no clips, no grid"** — `_clear_loop` / `reset_all_loops`; tests (partially addressed post-audit in `tests/test_apc_footswitch.py`).
4. **Add fake SL engine integration tests**
5. **Scrub stale clock docs** — README, `LooperClockMonitor` docstring, spike banner
