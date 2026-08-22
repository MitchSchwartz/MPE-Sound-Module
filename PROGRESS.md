# PROGRESS — canonical thread

**Updated 2026-08-22.** This is the top-level index. An agent picking up work starts here,
then opens the prompt file for its task. Everything else in `docs/measurements/` is either a
result or history.

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
| Shipping | 1024x3 = **64.0 ms** |
| Measured free at clean load | 1024x2 = **42.7 ms** (Cloud Horn, Duduk, Brave New World, Crystals) |
| Governor | **OFF** (left off from V9; re-enable blocked on fade) |
| Clock | 1800 MHz, `arm_boost=1`, `performance` |
| Cores | `irqaffinity=0,1`; jackd/surge/looper `CPUAffinity=2 3` |
| Confirmed floors | Crystals 3, Cloud Horn 5, Duduk 3, Brave New World 3 (all 1024) |

**Settled and not to be relitigated:** every xrun on this appliance is a **JACK graph
overrun**, not an ALSA underrun — the ring has never drained (`W1-VERDICT`). Fixed
per-callback cost is **a = 0.13 ms** (`V1-VERDICT`). Retired: the 600 us gap, cushion/drain
model, URB depth/rate, frame alignment, `threadirqs`, `isolcpus`, PREEMPT_RT, the
single-client refactor, and the unison cost theory (twice).

**Scope caveat:** every number above is a **Pi 4 fact**. Absolute costs, core allocation, and
the whole IRQ census are void on a Pi 5 (RP1 moves USB behind PCIe). Retired lines were retired
on a platform where *compute* bound first — if the Pi 5 clears the compute wall, the jitter and
transport work **un-retires**. Check, do not assume.

---

## Queue — two tracks

**Track A runs without Mitch.** Track B needs him reachable and is batched into one window.
Full plan: [`docs/measurements/PROMPT-PI4-CLOSEOUT.md`](docs/measurements/PROMPT-PI4-CLOSEOUT.md).

### Track A — autonomous (no reboot, no gate)

| # | Task | Prompt | Time |
|---|---|---|---|
| **A0** | **Instrument pre-flight** — prove the counter moves before trusting it | closeout §A0 | ~5 min |
| **A1** | **V11 — 512x2 / 256x3 at confirmed counts** | `PROMPT-V11-512-256-confirm.md` | ~15 min |
| **A2** | **Settle a72 — measure the built binary, keep or revert. Freezes the control.** | closeout §A2 | ~20 min |
| A3 | Freeze `measure-reference-suite.sh` — must run unmodified on a Pi 5 | closeout §A3 | ~30 min |
| A4 | Run reference suite **twice** on the frozen binary (noise floor) | closeout §A4 | 2 × 30 min |
| A5 | Full appliance state capture — the control condition | closeout §A5 | ~10 min |
| A6 | Archive raw logs off the SD card | `PROMPT-G3-archive-raw-logs.md` | ~30 min |
| A7 | `build-surge.sh --arch {a72\|a76\|generic}` as reusable infrastructure | closeout §A7 | ~30 min |
| A8 | Platform-stamp the live docs | closeout §A8 | ~20 min |
| A9 | **Predictions table — commit before the Pi 5 boots** | transition plan §5 | ~20 min |

**A2 must precede A4.** If a72 is installed after the reference passes, the two passes ran on
different binaries and the noise floor is worthless. And the Pi 5 will run `-mcpu=cortex-a76` —
if the Pi 4 control is untuned, the platform comparison measures *hardware plus a compiler flag*
and reports it as hardware.

### Track B — needs Mitch (one window, ~45 min + soak)

| # | Task | Prompt | Time |
|---|---|---|---|
| B1 | **P7 clock-scaling diagnostic** — now a Pi 5 *forecast*, not a lever | `PROMPT-P7-overclock-diagnostic.md` | ~13 min |
| B2 | 8 h soak at the V11 winner | — | overnight |
| B3 | Ear test before shipping the new default | — | ~10 min |

### Track C — Pi 5, on arrival

[`docs/PI5-BRINGUP-RUNBOOK.md`](docs/PI5-BRINGUP-RUNBOOK.md). Suite 0 (instruments, hard gate) →
Suite 1 (like-for-like reference, scores the predictions) → Suite 2 (latency ladder — **the
objective**) → Suite 3 (NVMe delta) → Suite 4 (thermal). Designed to run overnight and wake
Mitch only on a defined fork.

**Why V11 is still first:** fixed cost is 0.6% of the deadline at 1024 and 1.2% at 512, so the
voice ceiling is close to buffer-independent — Crystals is clean at 3 on *both* 1024 and
512. **512x2 = 21.3 ms, half of what 1024x2 gives.** It also defines the floor the Pi 5 gets
compared against; measuring the Pi 5 against 1024x2 would overstate the gain. Full argument:
**`docs/measurements/REVIEW-line-of-thought-2026-08-22.md`**.

**Why P7 moved up rather than being dropped:** as a performance lever ~11% is marginal against a
new board. As an **instrument** it forecasts whether the Pi 5's 2.4 GHz will convert at all — and
if DSP does *not* scale with clock, the Pi 5's memory advantage is the operative one and the
optimisation order changes. 13 minutes either way.

**Deferred:** multithreading (re-score after the Pi 5 baseline), governor fade and threshold
recalibration (Pi 4-absolute thresholds will be wrong on the Pi 5 — do it once, there),
percussive rate metric.

### Open gates (Mitch only)

- **Gate 1** — ship 1024x2 (or better, if V11 lands) as instrument profile default, after a
  clean soak. Looper stack stays 1024x3/D.
- **Gate 2** — governor re-enable: **blocked** until the fade lands *and*
  `CPU_HIGH_THRESHOLD=50.0` is recalibrated (it sits *below* the ~58.9% baseline DSP).
- **Gate 3** — percussive metric: deferred. Reframed as a **rate** question (does a fast roll
  drop notes), not a voice-count question.

---

## Standing rules

1. **Never run any command against `raspberrypi2` while a measurement is in flight** —
   including read-only ones.
2. **Confirm harness only** (`measure-confirm-at-voices.sh` / `measure-latency-run.sh`) for
   any before/after. **Never `measure-capacity-ramp.sh`** — ramp ceilings are screening-grade.
3. **Read `dsp_med` for compute questions**, `dsp_p99`/`dsp_max` for tail questions. Do not
   use a tail statistic to answer a central-tendency question.
4. **Check the instrument before trusting the reading.** The recurring failure on this
   appliance is an instrument that reads clean when it is blind — V8-b auto-pick, peak-meter
   shutdown, V10-b ramp probe, census `unison_voices`. **Four occurrences.**
5. **Ask the shortest useful version of a test** before running it. Doctrine:
   `docs/measurements/MEASUREMENT-DISCIPLINE.md`, skill: `.claude/skills/measurement-design/`.
6. **One variable.** Overclock and rebuild do not overlap. Neither overlaps a soak. This
   applies hardest during the Pi 5 transition, where a dozen things change at once.
7. **Name the platform in every measurement doc.** Two boards, one repo: an unstamped number is
   ambiguous the moment the Pi 5 boots. Standard conditions table carries the current default.

---

## Live documents

| File | What it is |
|---|---|
| `docs/measurements/REVIEW-line-of-thought-2026-08-22.md` | **Current roadmap argument.** Read this second. |
| `docs/measurements/MEASUREMENT-DISCIPLINE.md` | Doctrine. Rules 0-7. |
| `docs/measurements/session-handoff-2026-08-22.md` | Last session state |
| `docs/measurements/V9-REVIEW-2026-08-22.md` | V9 a/b/c/d results |
| `docs/measurements/V10-b-ramp-probe-fix-2026-08-22.md` | Why ramp ceilings are screening-only |
| `docs/measurements/V1-VERDICT-no-fixed-cost-2026-08-21.md` | a = 0.13 ms |
| `docs/measurements/W1-VERDICT-compute-bound-2026-08-21.md` | Graph overrun, not underrun. **Note: its a = 1.10 ms is retracted by V1-VERDICT.** |
| `docs/measurements/v8-patch-capacity-2026-08-21.md` + `V8-REVIEW` | 53-patch survey (ceilings screening-grade) |
| `docs/measurements/CEILING-ANALYSIS-what-maxed-out-means.md` | Assumption stack A1–A6, levers 1–7 |
| `docs/measurements/PATCH-COST-what-makes-them-heavy.md` | Unison retraction; real cost centres |
| `docs/measurements/MULTITHREADING-ASSESSMENT.md` | ~3× prize, multi-week cost; do not start yet |
| `docs/PI5-TRANSITION-PLAN.md` | **Why the transition is structured this way.** What survives, what is void |
| `docs/PI5-BRINGUP-RUNBOOK.md` | Pi 5 setup + overnight suites with gates |
| `docs/measurements/PROMPT-PI4-CLOSEOUT.md` | Ordered Pi 4 closeout, Track A/B |
| `docs/SRED-EVIDENCE-2026.md` | Uncertainties, chronology, prior-art position, gaps G1–G5 |

**History:** superseded and refuted runs live in [`docs/measurements/archive/`](docs/measurements/archive/)
(53 files, 2026-08-22 compaction). Keep for provenance — not decision inputs.
