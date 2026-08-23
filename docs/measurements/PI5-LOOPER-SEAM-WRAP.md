# Pi 5 looper — seam wrap finish line

*Branch:* `yolo/pi5-looper-seam-wrap` · *Opened:* 2026-08-23 (America/Toronto)

**Goal:** Ship **stop-then-weld** (scratch loop 15 + offline merge) on the Pi 5 player —
continuous wrap at N→0 with release tail at the seam, not at sample 0.

**Canon:** [`Documents/specs/looper-loop-seam-spec.md`](../../Documents/specs/looper-loop-seam-spec.md) ·
[`DECISIONS.md`](../../Documents/DECISIONS.md) §2026-08-19 ·
[`archive/seam-weld-spike-2026-08-18.md`](archive/seam-weld-spike-2026-08-18.md) (archaeology)

**Player context:** Pi 5 audio path is live at 128×2 ([`PI5-SESSION-CLOSEOUT-2026-08-23.md`](PI5-SESSION-CLOSEOUT-2026-08-23.md)).
Looper stack is **not** part of daily player services yet — this branch adds/evaluates it.

---

## Product model (locked)

1. **Pad-down → stop** — length fixes immediately (defining take) or at quantised bar (grid).
2. **Tail pass** — parallel record on **scratch loop 15** while main loop plays at fixed N.
3. **Weld once** — `save_loop` both buffers → Python crossfade at seam → `load_loop` → resume.

**Rejected forever:** Tier 2 extend-until-quiet, Option E seam overdub ([`DECISIONS.md`](../../Documents/DECISIONS.md)).

**Defaults:** `MPE_SL_TAIL_CAPTURE=1`, `MPE_SL_SEAM_WELD=1`.

---

## Code map (already on branch)

| File | Role |
|------|------|
| `scripts/sooperlooper/sl_seam_weld.py` | Background save → merge → load worker |
| `scripts/sooperlooper/seam_merge.py` | Crossfade tail onto `[N−M,N)` ↔ `[0,M)` |
| `scripts/sooperlooper/apc_footswitch.py` | `SEAM_WELD` tail capture + hooks |
| `scripts/sooperlooper-apc-bench.py` | Wires `SeamWeldWorker` |
| `scripts/sooperlooper/sl_hud_monitor.py` | Scratch loop excluded from phrase bar |
| `tests/test_seam_merge.py` | Offline merge geometry |
| `tests/test_apc_footswitch.py` | Tail poll, merge hooks, abort |
| `scripts/sooperlooper/spike-seam-weld.sh` | Manual Tier 3 spike procedure |

---

## Acceptance still open (spec S5–S7)

| ID | Criterion | Status |
|----|-----------|--------|
| S5 | Grid clip length = one cycle after weld; tail at seam not head-only | **Ear + OSC** — not closed on Pi 5 |
| S6 | Fast pad switch during weld → no overdub bleed on other loop | **Manual** — Mitch scenario |
| S7 | Grid BPM unchanged by tail capture on clips 1+ | **`derive_tempo` unchanged** |

Tier 1 (latency) was calibrated on Pi 4 (`MPE_SL_INPUT_LATENCY=3072`) — **re-tune on Pi 5**
after looper is up ([`archive/looper-p0-latency-calibration.md`](archive/looper-p0-latency-calibration.md)).

---

## Known risks from Pi 4 ear spike (re-verify on Pi 5)

| Symptom | Likely cause | Branch task |
|---------|--------------|-------------|
| Release tail missing | Peak meter / merge skip / scratch mis-align | Tune `MPE_SL_TAIL_*`; log peak during weld |
| Jump to loop head after weld | `load_loop` + `trigger` playhead reset | Confirm `pause_off` + `trigger` sequence; ear |
| `save_loop` timeout | Wedged engine (B8 history) | **`mpe looper sl-health`** before every merge test |
| HUD bar count wrong during tail | Scratch loop in phrase ref | Fixed in `sl_hud_monitor` — regression test |
| Seam weld busy drop | Overlapping closes | Log + guard in bench |

---

## Pi 5 bring-up sequence (before ear tests)

1. **SooperLooper on Pi 5** — build eval tree per [`AGENTS.md`](../../AGENTS.md) scoped exception
   (`~/src/sooperlooper-*`, not under repo). Mitch gate: `sudo apt` if deps missing.
2. **Deploy branch** — `mpe looper deploy yolo/pi5-looper-seam-wrap` (or laptop pull + `configure-pi-paths.sh`).
3. **Engine health** — `mpe looper sl-health` → `save_loop` smoke before seam tests.
4. **Bench** — `mpe looper sl-bench restart`; watch log for `seam-weld: done loop N`.
5. **Spike script** — `scripts/sooperlooper/spike-seam-weld.sh` on Pi.

### Status 2026-08-23 (evening)

| Step | State |
|------|--------|
| Binary | Copied Pi 4 `sooperlooper` → `~/src/sooperlooper-1.7.9/src/` |
| Runtime deps | `liblo7`, `liblo-tools`, `libsigc++-2.0-0v5`, `librubberband2` (+ friends) |
| Branch on Pi | `yolo/pi5-looper-seam-wrap` @ `3a5697a` |
| Env | 16 loops, `MPE_SL_SEAM_WELD=1`, `TAIL_MODE=extend` removed |
| sl-health | **PASS** |
| APC bench | **Running** — log shows `stop-then-weld on (scratch loop 15)` |

**Git note:** first `mpe looper deploy` may fail if Pi has untracked census artifacts — run
`git clean -fd appliance-state/pi5-irq-census-2026-08-23` once, or use updated
`scripts/looper-deploy.sh` after checkout lands.

**Next:** Mitch ear — defining take close with release ringing; bench log should show
`seam-weld: saving…` → `seam-weld: done loop N`.

Env on Pi 5 (`/etc/mpe/mpe.env`):

```bash
MPE_SL_TAIL_CAPTURE=1
MPE_SL_SEAM_WELD=1
MPE_SL_SCRATCH_LOOP=15
# After P0 re-tune:
# MPE_SL_AUTOSET_LATENCY=0
# MPE_SL_INPUT_LATENCY=<samples>
```

---

## Work queue (this branch)

| # | Task | Gate |
|---|------|------|
| 1 | SooperLooper binary + `mpe-sooperlooper.service` on Pi 5 | Mitch apt/build if needed |
| 2 | `sl-health` + single-loop `save_loop` smoke | Blocks merge path |
| 3 | Defining take ear pass — release through wrap, no pop | Mitch ear (S2/S5 partial) |
| 4 | Grid clip weld — length + BPM unchanged | S5, S7 |
| 5 | Fast pad-switch during weld | S6 — Mitch |
| 6 | Document Pi 5 result in `docs/measurements/`; promote spec status | Gate B |

**Out of scope here:** poly governor, Suite 1 latency ladder, Pi 5 IRQ experiments.

---

## SR&ED note

Extends Phase 2 looper investigation onto **second platform** (U10) — seam geometry is
stack-specific; Pi 4 ear failures may or may not reproduce on RP1 USB + 128×2 JACK graph.
Log labour via [`sred-daily-capture`](../../.claude/skills/sred-daily-capture/SKILL.md) after bench sessions.

---

*Last updated: 2026-08-23 (America/Toronto)*
