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

---

## Where the appliance stands

| | |
|---|---|
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

---

## Queue — do these in this order

| # | Task | Prompt | Time | Gate |
|---|---|---|---|---|
| **1** | **V11 — 512x2 / 256x3 at confirmed counts** | *needs writing* | ~15 min | none |
| 2 | 8 h soak at whatever V11 leaves as best config | — | overnight | none |
| 3 | P7 overclock diagnostic | `PROMPT-P7-overclock-diagnostic.md` | ~13 min | needs Mitch present (reboot) |
| 4 | P8 `-mcpu=cortex-a72` | `PROMPT-P8-mcpu-cortex-a72.md` | build 40 min + 20 min measure | **conditional on P7** |
| 5 | Governor fade, then re-enable | — | offline | Mitch ear-test |
| — | Census parser `unison_voices` fix | `HANDOVER-census-unison-fix.md` | offline | none |

**Why V11 is first:** fixed cost is 0.6% of the deadline at 1024 and 1.2% at 512, so the
voice ceiling is close to buffer-independent — Crystals is clean at 3 on *both* 1024 and
512. **512x2 = 21.3 ms, half of what 1024x2 gives**, for a config change. P7 buys ~11% of
compute; this buys ~50% of the latency. The reason 512 was abandoned ("crackle") was the
poly governor stealing voices, which is refuted and has never been re-tested. Full argument
and caveats: **`docs/measurements/REVIEW-line-of-thought-2026-08-22.md`**.

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
6. **One variable.** Overclock and rebuild do not overlap. Neither overlaps a soak.

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

### STRANDED — merge before relying on these

`docs/v9-review` is **not merged into dev**. Three analysis documents live only there:

- `CEILING-ANALYSIS-what-maxed-out-means.md` — assumption stack A1-A6, levers 1-7
- `PATCH-COST-what-makes-them-heavy.md` — unison retraction, real cost centres
- `MULTITHREADING-ASSESSMENT.md` — ~3x prize, multi-week cost, recommendation not to start

**Merge `docs/v9-review` into dev.** They are cited by the roadmap and are currently invisible
to anyone on the main line.

---

## Archive candidates

Move to `docs/measurements/archive/`. All are superseded or refuted — keep for provenance,
they are not decision inputs.

**Refuted / retracted lines**
`find-600us-2026-08-21.md`, `PROMPT-find-the-600us.md`, `cushion-model-2026-08-21.md`,
`t13-runway-refuted-2026-08-21.md`, `t12-alignment-confounded-2026-08-21.md`,
`t12-stream-sample-2026-08-21.md`, `scarlett-verdict-2026-08-21.md`,
`scarlett-step0-gate-2026-08-21.md`, `scarlett-step1-256x3-condA-2026-08-21.md`,
`PLAN-V-fixed-callback-cost.md`, `PROMPT-V-fixed-callback-cost.md`, `v1-fixed-cost-2026-08-21.md`

**Superseded IRQ / core / hygiene work** (conclusions now in the state table above)
`i3-config-diff-2026-08-20.md`, `i3-e1-revert-verify-2026-08-20.md`,
`i3-n15-e1-revert-2026-08-20.md`, `e1-three-cores-T1-2026-08-20.md`,
`cpu-census-2026-08-21.md`, `step0-restore-jack-2026-08-21.md`,
`step1-unknowns-2026-08-21.md`, `step2-hygiene-applied-2026-08-21.md`,
`step3-rebaseline-2026-08-21.md`, `low-latency-step0-step1-2026-08-19.md`,
`stream-start-variance-2026-08-21.md`, `shutdown-timeout-fix-2026-08-21.md`

**Superseded ladders / sweeps**
`t2-bug-class-sweep-2026-08-20.md`, `t4a-512-loop-curve-2026-08-20.md`,
`t4c-1024-loop-curve-finish-2026-08-20.md`, `t5-pre-jack-lsp-removal-2026-08-20.md`,
`t5-soak-2026-08-21.md`, `t6-harness-sweep-2026-08-20.md`, `t9-loops8-d-2026-08-21.md`,
`t11-condA-ladder-2026-08-21.md`, `postjournal-512-C-2026-08-20.md`,
`per-patch-headroom-open-2026-08-19.md`

**Looper-era** (still valid, different subsystem — not part of the instrument-latency thread)
`looper-midi-osc-latency-2026-08-19.md`, `looper-p0-latency-calibration.md`,
`looper-session-crash-and-cpu-2026-08-19.md`, `looper-session-phase3m-2026-08-18.md`,
`looper-stack-cost-2026-08-19.md`, `sooperlooper-eval-2026-08-14.md`,
`TEMPLATE-sooperlooper-eval.md`, `seam-weld-spike-2026-08-18.md`,
`peak-meter-dsp-cost-2026-08-18.md`, `systemd-liveness-cost-2026-08-19.md`

**Completed prompts / plans**
`PROMPT-W1-instrumented-window.md`, `PROMPT-V7-capacity-curve.md`,
`PROMPT-V8-patch-capacity-survey.md`, `PROMPT-V9-probe-duration-and-clean-x2.md`,
`PROMPT-YOLO-poly-governor.md`, `V7-capacity-curve-plan.md`,
`PLAN-2026-08-21-evening.md`, `session-handoff-2026-08-20.md`,
`crackle-root-cause-2026-08-18.md`

**Keep at top level:** everything in *Live documents* above, plus the three queued prompts
(`PROMPT-P7`, `PROMPT-P8`, `HANDOVER-census-unison-fix`), `README.md`,
`xrun-counter-audit-2026-08-21.md`, `v7-capacity-curve-2026-08-21.md`,
`v9-probe-duration-2026-08-22.md`.

**Do not archive by deleting.** `git mv` into `archive/`, and leave `README.md` pointing at it.
