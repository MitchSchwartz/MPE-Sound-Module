# PROGRESS — canonical thread

**Updated 2026-08-23 00:54 (America/Toronto).** This is the top-level index.

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
| Measured free at clean load | 1024x2 = **42.7 ms** (Cloud Horn, Duduk, Brave New World, Crystals) |
| Governor | **OFF** (left off from V9; re-enable blocked on fade) |
| Clock | 1800 MHz, `arm_boost=1`, `performance` |
| Cores | `irqaffinity=0,1`; jackd/surge/looper `CPUAffinity=2 3` |
| Confirmed floors | Crystals 3, Cloud Horn 5, Duduk 3, Brave New World 3 (all 1024) |
| **V11 (2026-08-22)** | **512x2 clean for Crystals @3 and Duduk @3** (0/0/0 x3). Cloud Horn @5 marginal. 256x3: Duduk clean, Crystals marginal, Cloud Horn overloaded. **Xrun column stands.** Post-C0 DSP certified. Artifacts `~/plan-v11-20260822-144259/` |
| **A2 pass 1 (2026-08-22)** | **DONE** — stock binary. JSON `~/reference-suite-pi4-20260822-204559/reference-suite-pi4-pass1.json` (`110977a`). Re-validated offline at `a1e80e3`: 12/12 loaded cells PASS. |
| **A3 (2026-08-22)** | **DONE — NULL (pre-reg &lt;3%).** a72 suite `~/reference-suite-pi4-a72-20260822-231637/`. Same Surge `253f8d86`; no win on any cell. **Stock kept as control.** Doc: `reference-suite-pi4-a3-a72-comparison-2026-08-22.md` |
| **A4 (2026-08-23)** | **DONE — noise floor.** Pass 2 `~/reference-suite-pi4-20260823-000348/`. Re-validated 12/12 at `e51856e`. **Max run-to-run spread 1.70%** (median 0.47%). Duduk a72 retro: **noise.** Doc: `reference-suite-pi4-a4-spread-2026-08-23.md` |
| **B2 soak (2026-08-23)** | **IN FLIGHT** — attempt #3 @ 1024×2 Cloud Horn @5. Pi `9060236`. Started 00:51, expected finish ~08:51. Log `~/instrument-soak-1024x2.log`. Pilot PASS (`~/instrument-soak-pilot-2026-08-23.log`). Prior attempt FAIL: `b2-soak-gate1-2026-08-23.md`. |

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

## Queue — two tracks

**Track A runs without Mitch.** Track B needs him reachable and is batched into one window.
Full plan: [`docs/measurements/PROMPT-PI4-CLOSEOUT.md`](docs/measurements/PROMPT-PI4-CLOSEOUT.md).

> **C0 done on Pi 2026-08-22** — full gate green (`~/conformance-full-green.log`, #96–#101).
> **A2 + A3 + A4 done** — stock control calibrated with noise floor.
> **B2 IN FLIGHT** — 8 h soak started 00:51 after pilot PASS on `9060236`.
> **A5–A9 blocked** until B2 PASS (`SENTINEL soak-complete`).

### Track A — autonomous (no reboot, no gate)

| # | Task | Prompt | Time |
|---|---|---|---|
| ~~**C0**~~ | ~~**INSTRUMENT CONFORMANCE**~~ — **DONE.** Pi full gate green 2026-08-22 | `PROMPT-C0-instrument-conformance.md` | ≤15 min |
| A0 | Per-session conformance pass (same gate as C0) | `PROMPT-C0-instrument-conformance.md` | ≤15 min |
| ~~**A1**~~ | ~~**V11**~~ — **DONE (xrun column).** 3/6 cells pass | `PROMPT-V11-512-256-confirm.md` | ~25 min |
| ~~**A2**~~ | ~~**Reference suite pass 1**~~ — **DONE** 2026-08-22 (stock binary) | closeout §A2 | ~35 min |
| ~~**A3**~~ | ~~**Settle a72**~~ — **DONE NULL** 2026-08-22; stock control kept | closeout §A3 | ~60 min |
| ~~**A4**~~ | ~~**Reference pass 2**~~ — **DONE** 2026-08-23; max spread **1.70%** | closeout §A4 | ~30 min |
| **A5** | Full appliance state capture — **blocked on B2 PASS** | closeout §A5 | ~10 min |
| A6 | Archive raw logs off the SD card — **blocked on B2 PASS** | `PROMPT-G3-archive-raw-logs.md` | ~30 min |
| A7 | `build-surge.sh --arch {a72\|a76\|generic}` — **blocked on B2 PASS** | closeout §A7 | ~30 min |
| A8 | Platform-stamp the live docs — **blocked on B2 PASS** | closeout §A8 | ~20 min |
| A9 | Predictions table — **blocked on B2 PASS** | transition plan §5 | ~20 min |

### Track B — needs Mitch (one window, ~45 min + soak)

| # | Task | Prompt | Time |
|---|---|---|---|
| B1 | **P7 clock-scaling diagnostic** — now a Pi 5 *forecast*, not a lever | `PROMPT-P7-overclock-diagnostic.md` | ~13 min |
| **B2** | **IN FLIGHT** — 8 h soak @ 1024×2 (started 00:51) | — | overnight |
| B3 | Ear test before shipping any new binary default | — | ~10 min |

### Track C — Pi 5, on arrival

[`docs/PI5-BRINGUP-RUNBOOK.md`](docs/PI5-BRINGUP-RUNBOOK.md). Suite 0 (instruments, hard gate) →
Suite 1 (like-for-like reference, scores the predictions) → Suite 2 (latency ladder — **the
objective**) → Suite 3 (NVMe delta) → Suite 4 (thermal). Designed to run overnight and wake
Mitch only on a defined fork.

**V11 xrun result (done):** 512x2 is clean for Crystals and Duduk at confirm counts; Cloud Horn
@5 fails at both 512x2 and 256x3. 256x3 only fully clean for Duduk. No blanket floor promotion.
Full argument: **`docs/measurements/REVIEW-line-of-thought-2026-08-22.md`**.

**Why P7 moved up rather than being dropped:** as a performance lever ~11% is marginal against a
new board. As an **instrument** it forecasts whether the Pi 5's 2.4 GHz will convert at all — and
if DSP does *not* scale with clock, the Pi 5's memory advantage is the operative one and the
optimisation order changes. 13 minutes either way.

**Deferred:** multithreading (re-score after the Pi 5 baseline), governor fade and threshold
recalibration (Pi 4-absolute thresholds will be wrong on the Pi 5 — do it once, there),
percussive rate metric.

### Open gates (Mitch only)

- **Gate 1** — ship 1024x2 as instrument profile default, after a clean soak. Looper stack stays
  1024x3/D. **IN FLIGHT** — B2 attempt #3 running (00:51→~08:51). Prior abort documented in
  `b2-soak-gate1-2026-08-23.md`. Gate opens only on `SENTINEL soak-complete` + acceptable xrun total.
- **Gate 2** — governor re-enable: **blocked** until the fade lands *and*
  `CPU_HIGH_THRESHOLD=50.0` is recalibrated (it sits *below* the ~58.9% baseline DSP).
- **Gate 3** — percussive metric: deferred. Reframed as a **rate** question (does a fast roll
  drop notes), not a voice-count question.
- **B3 ear test** — required before any binary or buffer default ships to production.

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
6. **Ask the shortest useful version of a test** before running it. Doctrine:
   `docs/measurements/MEASUREMENT-DISCIPLINE.md`, skill: `.claude/skills/measurement-design/`.
7. **One variable.** Overclock and rebuild do not overlap. Neither overlaps a soak. This
   applies hardest during the Pi 5 transition, where a dozen things change at once.
8. **Name the platform in every measurement doc.** Two boards, one repo: an unstamped number is
   ambiguous the moment the Pi 5 boots. Standard conditions table carries the current default.

---

## Live documents

| File | What it is |
|---|---|
| `docs/measurements/b2-soak-gate1-2026-08-23.md` | **B2 FAIL** — subshell abort, fix, re-run checklist |
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
| `docs/SRED-EVIDENCE-2026.md` | Uncertainties, chronology, prior-art position, gaps G1–G5 |

**History:** superseded and refuted runs live in [`docs/measurements/archive/`](docs/measurements/archive/)
(53 files, 2026-08-22 compaction). Keep for provenance — not decision inputs.
