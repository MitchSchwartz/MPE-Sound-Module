# PROGRESS — canonical thread

**Updated 2026-08-27 10:38 (America/Toronto).** This is the top-level index.

---

## The goal

**Reduce instrument-only playing to the lowest possible latency.** Synth only, live
instrument. Polyphony matters because a patch that drops voices is unplayable — but
**latency is the objective and buffer size is the direct lever.** Compute wins (overclock,
compiler flags, multithreading) buy polyphony *headroom*, which becomes latency only if it
is then spent on a smaller buffer.

**A Pi 5 is on order.** That does not change the goal — it changes what the Pi 4 work is *for*.
The Pi 4 is now a **control**: its job is to produce a frozen reference suite, a known latency
floor, and a clock-scaling forecast, so the Pi 5's gain is measurable rather than assumed.
Governing rule for the transition: **replication before optimisation.**
See [`docs/PI5-TRANSITION-PLAN.md`](docs/PI5-TRANSITION-PLAN.md).

---

## Where the appliance stands

| | |
|---|---|
| **Platform** | **Pi 4B / BCM2711 / Cortex-A72**, Pi OS Lite 64-bit (trixie). Pi 5 on order — see Track C |
| **Control binary** | **stock** (a72 null result 2026-08-22; reverted). Backup `~/surge-xt-cli.pre-a72` |
| Shipping | 1024x3 = **64.0 ms** |
| Measured free at clean load | 1024x2 = **42.7 ms** — **but "free" is wrong.** The 8 h soak measured **991 xruns = 2.06/min** at this config. It read clean only because windows were 25-45 s and the process is bursty (Fano 4.32). **No config is "clean"; the open question is audibility (B3).** |
| Governor | **ON** — `HIGH=78.0` `LOW=68.0` (G2 closed 2026-08-23). Fade actuation still open (B3 audibility) |
| Clock | 1800 MHz, `arm_boost=1`, `performance` |
| Cores | `irqaffinity=0,1`; jackd/surge/looper `CPUAffinity=2 3` |
| Confirmed floors | Crystals 3, Cloud Horn 5, Duduk 3, Brave New World 3 (all 1024) |
| **V11 (2026-08-22)** | Confirm windows only (75 s) — **screening, not certification** post-X1. Cells read 0/0/0 where bursts missed the window; **not evidence of "clean."** Cloud Horn @5 failed at 512×2 and 256×3 in-window. **V12 re-measures rate.** Artifacts `~/plan-v11-20260822-144259/` |
| **A2 pass 1 (2026-08-22)** | **DONE** — stock binary. JSON `~/reference-suite-pi4-20260822-204559/reference-suite-pi4-pass1.json` (`110977a`). Re-validated offline at `a1e80e3`: 12/12 loaded cells PASS. |
| **A3 (2026-08-22)** | **DONE — NULL (pre-reg &lt;3%).** a72 suite `~/reference-suite-pi4-a72-20260822-231637/`. Same Surge `253f8d86`; no win on any cell. **Stock kept as control.** Doc: `reference-suite-pi4-a3-a72-comparison-2026-08-22.md` |
| **A4 (2026-08-23)** | **DONE — noise floor.** Pass 2 `~/reference-suite-pi4-20260823-000348/`. Re-validated 12/12 at `e51856e`. **Max run-to-run spread 1.70%** (median 0.47%). Duduk a72 retro: **noise.** Doc: `reference-suite-pi4-a4-spread-2026-08-23.md` |
| **B2 soak (2026-08-23)** | **PASS** — attempt #3 @ 1024×2 Cloud Horn @5. **991 xruns**, 2.06/min, `invalid_windows=0`. Pi `9060236`. Log archived. Doc: `b2-soak-gate1-2026-08-23.md`. **B3 ear test** still open. |

**Settled and not to be relitigated:** every xrun on this appliance is a **JACK graph
overrun**, not an ALSA underrun — the ring has never drained (`W1-VERDICT`). Fixed
per-callback cost is **a = 0.13 ms** (`V1-VERDICT`). Retired: the 600 us gap, cushion/drain
model, URB depth/rate, frame alignment, `threadirqs`, `isolcpus`, PREEMPT_RT, the
single-client refactor, the unison cost theory (twice), and **`-mcpu=cortex-a72`** (A3 null).
**Run-to-run spread floor: ~1.7% max** (A4) — use as Pi 5 significance threshold.

**Scope caveat:** every number above is a **Pi 4 fact**. Absolute costs, core allocation, and
the whole IRQ census are void on a Pi 5 (RP1 moves USB behind PCIe). Retired lines were retired
on a platform where *compute* bound first — if the Pi 5 clears the compute wall, the jitter and
transport work **un-retires**. Check, do not assume.

---

## Queue — four tracks

**Track A runs without Mitch.** Track B needs him reachable and is batched into one window.
**Track D (looper multi-clip)** is the live thread as of 2026-08-26 — two short hardware
checks are batched for the morning of **2026-08-27**; everything else in it is autonomous.
Full plan: [`docs/measurements/PROMPT-PI4-CLOSEOUT.md`](docs/measurements/PROMPT-PI4-CLOSEOUT.md).

> **C0 done on Pi 2026-08-22** — full gate green (`~/conformance-full-green.log`, #96–#101).
> **A2 + A3 + A4 done** — stock control calibrated with noise floor.
> **B2 PASS** — Gate 1 soak complete 08:54; A5–A9 unblocked (closeout in progress).
> **Gate 1 ship stack:** **V12 → B3** (~70 min Pi time, Mitch approval for V12). G2 closed; governor on at 78/68.

### Track A — autonomous (no reboot, no gate)

| # | Task | Prompt | Time |
|---|---|---|---|
| ~~**C0**~~ | ~~**INSTRUMENT CONFORMANCE**~~ — **DONE.** Pi full gate green 2026-08-22 | `PROMPT-C0-instrument-conformance.md` | ≤15 min |
| A0 | Per-session conformance pass (same gate as C0) | `PROMPT-C0-instrument-conformance.md` | ≤15 min |
| ~~**A1**~~ | ~~**V11**~~ — **DONE (xrun column).** 3/6 cells pass | `PROMPT-V11-512-256-confirm.md` | ~25 min |
| ~~**A2**~~ | ~~**Reference suite pass 1**~~ — **DONE** 2026-08-22 (stock binary) | closeout §A2 | ~35 min |
| ~~**A3**~~ | ~~**Settle a72**~~ — **DONE NULL** 2026-08-22; stock control kept | closeout §A3 | ~60 min |
| ~~**A4**~~ | ~~**Reference pass 2**~~ — **DONE** 2026-08-23; max spread **1.70%** | closeout §A4 | ~30 min |
| ~~**A5**~~ | ~~**State capture**~~ — **DONE** 2026-08-23 (calibration + `pi4-control-2026-08-23/`) | closeout §A5 | ~10 min |
| ~~**A6**~~ | ~~**Log archive refresh**~~ — **DONE** soak log re-pulled (512 lines) | OM-Repo `sred/PROMPT-G3-archive-raw-logs.md` | ~30 min |
| ~~**A7**~~ | ~~**`build-surge.sh --arch`**~~ — **DONE** (`scripts/build-surge.sh`) | closeout §A7 | ~30 min |
| ~~**A8**~~ | ~~**Platform stamps**~~ — **DONE** on B2 + predictions docs | closeout §A8 | ~20 min |
| ~~**A9**~~ | ~~**Predictions table**~~ — **DONE** `pi5-predictions-2026-08-23.md` | transition plan §5 | ~20 min |

### Track B — needs Mitch (one window, ~45 min + soak)

| # | Task | Prompt | Time |
|---|---|---|---|
| B1 | **P7 clock-scaling diagnostic** — now a Pi 5 *forecast*, not a lever | `PROMPT-P7-overclock-diagnostic.md` | ~13 min |
| ~~**B2**~~ | ~~**8 h soak @ 1024×2**~~ — **PASS** (991 xruns, 2.06/min) | — | overnight |
| ~~**G2**~~ | ~~**Governor recalibration + re-enable**~~ — **CLOSED** 2026-08-23. Doc: `G2-RESULT-2026-08-23.md` | `PROMPT-G2-governor-recalibration.md` | ~33 min |
| **V12** | **512×2 vs 1024×2 rate comparison** — needs Mitch approval | `PROMPT-V12-certify-buffer.md` · `measure-v12-buffer-compare.sh` | ~70 min |
| B3 | Ear test (audibility) — **Gate 1 ship** | — | ~10 min |

### Track D — multi-clip slot matrix (looper), 2026-08-26

Spec: [`Documents/specs/multi-clip-per-track-spec.md`](Documents/specs/multi-clip-per-track-spec.md) (rev 3).
Spike results: [`docs/measurements/multi-clip-slot-spike-2026-08-26.md`](docs/measurements/multi-clip-slot-spike-2026-08-26.md).

**Done 2026-08-26:** the seam-weld pipeline is deleted — takes close into a native
one-pass SooperLooper overdub (`117f4cc`, `1a90d51`, `a99cf63`). 16 contiguous
tracks. P0 restored (`c509ed9`) after `2500782` had silently dropped it.
SP1/SP2/SP4 pass with 2–3 orders of magnitude of margin; SP6 measured.

#### Needs Mitch — batched for the morning of 2026-08-27 (~5 min total)

| # | Task | Exact gesture | Why it matters |
|---|---|---|---|
| ~~**SP8**~~ | **Done 2026-08-27 — REFUTED.** Shift alone emitted note `0x62` and nothing else, four clean press/release pairs. With the SP6 capture that is two independent observations and zero ghost notes. The 80 ms filter was eating the genuine Shift+Scene chord; `MK1_GHOST_SHIFT_S` now defaults to 0 (`07bff9e`), mechanism kept behind `MPE_APC_MK1_GHOST_S`. **P3 unblocked.** |
| ~~**SP3b**~~ | **Done 2026-08-27 — both PASS by ear.** Queued stop re-tapped before the bar keeps playing; queued launch re-tapped before the bar stays silent. P0 and the `pause_on` cancel are confirmed on hardware. Had to be by ear: SL sets the target state the instant a trigger is queued, which is how two automated runs PASSed vacuously. |

#### Open decision — not a test

| # | Question | Evidence |
|---|---|---|
| ~~**D1**~~ | **Done 2026-08-27 — write to the card.** Mitch: *"let's write to sd actually and do what the feature promises."* Every WAV fsynced before the manifest names it, manifest fsynced before its rename, directory fsynced after (`07bff9e`). `MPE_LOOPER_FSYNC=0` opts out. **Still unmeasured on the Pi** — the added latency per save gesture needs a number. |

#### Autonomous — no Mitch needed

| # | Task |
|---|---|
| ~~**P1**~~ | **Done** — `slot_matrix.py` (`0c039e7`, 31 tests) + manifest v2 (`83cade9`, 26 tests). Pushed to `dev`; **not deployed to the Pi** — v2 changes what `save_song` writes to disk, and that is not a change to make on Mitch's instrument overnight without him. Suite: 1186 passed. |
| **P2** | **I0+I1 on `dev` — Pi gate open.** Dual-controller fix + close-take lifecycle. See integration plan I0–I2. |
| **P3** | **Blocked on I0–I4** — scene row code exists; scene LED/gesture wrong until unified column controller lands. |

### Track C — Pi 5, on arrival

[`docs/PI5-BRINGUP-RUNBOOK.md`](docs/PI5-BRINGUP-RUNBOOK.md). Suite 0 (instruments, hard gate) →
Suite 1 (like-for-like reference, scores the predictions) → Suite 2 (latency ladder — **the
objective**) → Suite 3 (NVMe delta) → Suite 4 (thermal). Designed to run overnight and wake
Mitch only on a defined fork.

**V11 xrun result (2026-08-22, superseded by X1 for certification):** confirm windows only —
post-X1, short-window 0/0/0 is screening, not proof of clean. **V12 re-measures `512×2` vs
`1024×2` at 30 min/arm.** Full argument: **`docs/measurements/REVIEW-line-of-thought-2026-08-22.md`**,
**`X1-RESULT-burstiness-2026-08-23.md`**.

**Why P7 moved up rather than being dropped:** as a performance lever ~11% is marginal against a
new board. As an **instrument** it forecasts whether the Pi 5's 2.4 GHz will convert at all — and
if DSP does *not* scale with clock, the Pi 5's memory advantage is the operative one and the
optimisation order changes. 13 minutes either way.

**Deferred:** multithreading (re-score after the Pi 5 baseline), percussive rate metric.
Governor thresholds are Pi 4-absolute and will need a Pi 5 pass after G2 closes here.

### Open gates (Mitch only)

- **Gate 1** — ship instrument profile default (1024×2 or 512×2 per V12). **Soak PASS** at
  1024×2 (991 xruns, 2.06/min) — see `b2-soak-gate1-2026-08-23.md`. **Ship blocked on V12 → B3.**
- ~~**Gate 2**~~ — **CLOSED 2026-08-23.** Thresholds 78/68 verified; governor on. See
  `G2-RESULT-2026-08-23.md`. Fade actuation still open — audibility is B3, not a G2 blocker for thresholds.
- **Gate 3** — percussive metric: deferred. Reframed as a **rate** question (does a fast roll
  drop notes), not a voice-count question.
- **V12** — long-window buffer comparison, governor on. Prompt:
  `PROMPT-V12-certify-buffer.md`. Requires Mitch approval (~70 min).
- **B3 ear test** — audibility acceptance; **invalid until V12 closes.**

---

## G2 result pointer

Full log paths and harness notes: [`docs/measurements/G2-RESULT-2026-08-23.md`](docs/measurements/G2-RESULT-2026-08-23.md).

---

## Standing rules

1. **Never run any command against `raspberrypi2` while a measurement is in flight** —
   including read-only ones.
2. **Confirm harness only** (`measure-confirm-at-voices.sh` / `measure-latency-run.sh`) for
   any before/after. **Never `measure-capacity-ramp.sh`** — ramp ceilings are screening-grade.
3. **Read `dsp_med` for compute questions**, `dsp_p99`/`dsp_max` for tail questions. Do not
   use a tail statistic to answer a central-tendency question.
4. **An instrument must never be able to fail silently.** **Eleven occurrences** — the most
   expensive pattern here, one root cause: value and failure share a channel, so blindness
   arrives as a result. Required everywhere: no in-band failures (`|| x=0`, `unknown`,
   continue-on-error), a positive control, a negative control, and physics assertions that
   reject impossible readings in-harness, and **a terminal sentinel on every exit path** for
   anything long-running. **No suite runs without a conformance pass in the same session.**
   Doctrine: `MEASUREMENT-DISCIPLINE.md` **Rule -1**.
5. **Pilot before running at length.** One cell, minimum window, **read every field** — exit 0
   is not the check. Required whenever anything is new or changed, including after a fix and on
   a new platform. V11 spent 24.5 min to learn what a 2-min pilot would have shown.
6. **Ask the shortest useful version of a test** before running it. **Any window over 30 minutes
   needs Mitch's explicit approval and a written justification** — expected event rate, events
   needed, why shorter will not do. Doctrine:
   `docs/measurements/MEASUREMENT-DISCIPLINE.md`, skill: OM-Repo `.claude/skills/measurement-design/`.
7. **One variable.** Overclock and rebuild do not overlap. Neither overlaps a soak. This
   applies hardest during the Pi 5 transition, where a dozen things change at once.
8. **Name the platform in every measurement doc.** Two boards, one repo: an unstamped number is
   ambiguous the moment the Pi 5 boots. Standard conditions table carries the current default.

---

## Live documents

| File | What it is |
|---|---|
| `docs/measurements/b2-soak-gate1-2026-08-23.md` | **B2 PASS** — Gate 1 soak (991 xruns, 2.06/min) |
| `docs/measurements/PROMPT-G2-governor-recalibration.md` | **G2** — 78/68 proposal, empirical verify |
| `docs/measurements/PROMPT-V12-certify-buffer.md` | **V12** — 512×2 vs 1024×2 rate; no PASS/FAIL |
| `docs/measurements/X1-RESULT-burstiness-2026-08-23.md` | **X1 closed** — Fano 4.32; confirm = screening |
| `docs/measurements/pi5-predictions-2026-08-23.md` | **A9** — pre-registered Pi 5 predictions |
| `docs/measurements/reference-suite-pi4-a4-spread-2026-08-23.md` | **A4 noise floor** — spread table, Duduk retro, Pi 5 threshold |
| `docs/measurements/reference-suite-pi4-pass1-revalidation-2026-08-22.md` | A2 pass 1 offline re-validation at a1e80e3 |
| `docs/measurements/reference-suite-pi4-a3-a72-comparison-2026-08-22.md` | A3 stock vs a72 — null result |
| `docs/measurements/REVIEW-line-of-thought-2026-08-22.md` | **Current roadmap argument.** Read this second. |
| `docs/measurements/REVIEW-C0-conformance-2026-08-22.md` | C0 review; blocking findings through #97 |
| `Documents/reviews/review-loop-index-c0-conformance-live-2026-08-22.md` | C0 review loop (#96 + #97) |
| `docs/measurements/MEASUREMENT-DISCIPLINE.md` | Doctrine. Rules 0-7. |
| `docs/measurements/session-handoff-2026-08-22.md` | Last session state |
| `docs/measurements/V9-REVIEW-2026-08-22.md` | V9 a/b/c/d results |
| `docs/measurements/V1-VERDICT-no-fixed-cost-2026-08-21.md` | a = 0.13 ms |
| `docs/measurements/W1-VERDICT-compute-bound-2026-08-21.md` | Graph overrun, not underrun. **Note: its a = 1.10 ms is retracted by V1-VERDICT.** |
| `docs/PI5-TRANSITION-PLAN.md` | **Why the transition is structured this way.** What survives, what is void |
| `docs/PI5-BRINGUP-RUNBOOK.md` | Pi 5 setup + overnight suites with gates |
| `docs/measurements/PROMPT-PI4-CLOSEOUT.md` | Ordered Pi 4 closeout, Track A/B |
| OM-Repo [`sred/SRED-EVIDENCE-2026.md`](../OM-Repo/internal/projects/mpe-synth-launch/sred/SRED-EVIDENCE-2026.md) | Uncertainties, chronology, prior-art position, gaps G1–G5 |

**History:** superseded and refuted runs live in [`docs/measurements/archive/`](docs/measurements/archive/)
(53 files, 2026-08-22 compaction). Keep for provenance — not decision inputs.
