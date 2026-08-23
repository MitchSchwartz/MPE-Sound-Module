# Pi 4 close-out — the platform conclusion and what transfers to Pi 5

*Drafted: 2026-08-23 (America/Toronto). **Status: draft — three cells still open, see §7.***

**Purpose.** Close the Pi 4 instrument-latency arc as a systematic investigation with a
stated conclusion, an explicit list of eliminated hypotheses, and an honest account of the
confounds found along the way. Everything after §5 is forward-looking: what the Pi 4 work
licenses us to assume on Pi 5, and — more importantly — what it does not.

**Not a claim of Pi 5 superiority.** No Pi 5 measurement appears in this document. The Pi 5
running heavy patches at 512×2 is corroboration, not evidence, and is deliberately excluded
so the Pi 4 conclusion stands or falls on Pi 4 data.

---

## 1. The conclusion, stated narrowly

> At 48 kHz with the Quick Select patch library, on Raspberry Pi 4B / BCM2711 / Cortex-A72
> @ 1800 MHz, the appliance could not be certified at 512×2 (21.3 ms), and the binding
> constraint is **per-callback compute**, not I/O, driver, scheduling-priority, or
> compiler code generation. Each of those was tested and eliminated separately.

Three qualifiers that are part of the claim, not caveats to it:

1. **"Could not be certified"** — not "could not run." 512×2 produced audio. It never
   produced a clean measurement window long enough to support a shipping claim, and X1
   downgraded the earlier "512 is clean" statement to unsupported on method grounds.
2. **"Compute"** means DSP work inside Surge's callback, established by elimination (§2)
   and by the fixed-cost fit (§2, V1) — not by watching a CPU meter.
3. **The library is part of the claim.** A different patch set moves the ceiling. See §5.

---

## 2. Eliminated hypotheses

Each row is a lever that was tested and closed. This table, not the conclusion, is the
substance of the investigation.

| # | Hypothesis | Test | Outcome |
|---|---|---|---|
| H1 | Dropouts are ALSA ring underruns (I/O starvation) | W1 | **Refuted.** Every event is a JACK graph overrun. The ring never drained. |
| H2 | Cost is dominated by fixed per-callback overhead, so bigger buffers amortise it away | V1 | **Refuted.** Fixed cost `a` = **0.13 ms** — 0.61% of deadline at 1024, 2.44% at 256. Too small to explain the gap. |
| H3 | Compiler code generation is leaving performance on the table | A3 | **Null, pre-registered.** `-mcpu=cortex-a72` — all nine loaded cells ≤1.19% against a >5% win threshold. |
| H4 | Real-time scheduling / IRQ placement is the constraint | E1 + IRQ census (archived) | **Closed.** xhci is pinned to CPU0 and not movable on this SoC; core allocation is not a lever here. |
| H5 | The looper stack is the dominant cost | Looper stack cost, 2026-08-19 | **Refuted, and an earlier claim retracted.** See §3. |
| H6 | Clock is the binding constraint (overclock converts to headroom) | P7 | **Inconclusive 2026-08-23** — baseline half ~5–8% DSP (unloaded); OC ~34–53% (loaded). Comparison invalid; re-run required. See `P7-RESULT-2026-08-23.md`. |

### 2a. H6 / P7 — ran, and failed on the instrument

P7 executed 2026-08-23. **All 9 overclock cells: 0 xruns at 2000 MHz, `get_throttled` 0x0.**
Thermals and PSU hold at overclock — a real, if separate, result. Config reverted to stock.

**The comparison is void.** The @1800 baseline arm reported ~5–8% DSP against the OC arm's
~34–53% for the same patches. A higher clock cannot show more load. Cloud Horn @5 read
**7.6% baseline → 52.8% OC**; the reference suite has that cell at **56.9%** (1024×2) at
stock 1800 MHz, governor off. The OC arm is plausible; **the baseline arm was unloaded** —
the voice hold did not apply.

**Rule −1, occurrence eleven.** The load silently failed to apply, the harness reported a
plausible-shaped number, and the run exited clean through nine cells. Note that 7.6% is
*at or below the project's own plausibility floor for 1024* (1024=7.6, 512=12.5,
256=15.2). **Those floors existed as a concept and were never wired into this harness as an
assertion.** A pre-flight check rejecting any loaded cell below its buffer's floor would
have failed this run at minute one. Mechanism over vigilance — same shape as the
`MPE_EXPECT_SAMPLES` fail-open.

**Not rescued by cross-comparison, deliberately.** The OC arm could be compared against the
reference suite's validated 1800 MHz figures instead of its own broken baseline: 52.8% vs
56.9% is −7.2%, against −10% if DSP were perfectly clock-bound at +11.1% clock. That would
read as substantial-but-incomplete clock scaling. **It is not cited as a result** — different
day, different harness invocation, buffer configuration of the P7 cells not established.
Cross-instrument comparison is this project's recurring error class, and doing it to rescue
a run rather than to answer a question is how it happens. Recorded as a hypothesis with a
cheap test: **re-run the baseline arm alone with the floor assertion in place; if it lands
near 56.9%, H6 closes on one ~15-minute cell.**

**H6 therefore remains open.** Clock is neither established nor eliminated as a lever.

**Independent cross-validation of V1.** Stock `dsp_median` rises +2.34 / +2.11 / +2.48 pp
across 1024→512→256 for Crystals / Duduk / Cloud Horn. The V1 fit predicts ~1.83 pp from
`a` alone. Three unrelated patches agree with a model derived by a different method. This
is the single strongest internal consistency check in the arc.

---

## 3. The SooperLooper result — two cost terms, not one

This began as a defect hunt and ended as the most transferable model in the arc.

**Retracted first.** The 2026-08-18 numbers blamed the looper for ~30 points of DSP. They
were void: taken while `surge-watchdog`'s `jack_lsp` probe was itself generating 35
xruns/min, and the comparison stopped the looper *and both watchdogs* together, attributing
three components' cost to one. The corrected figure is **+5.50 points of DSP at 1024×3, all
of it the engine** — the session merge (+0.15) and the watchdog (+0.30) are free.

**Then the actual finding.** Cost decomposes into two independent terms:

| term | mechanism | 512×3 | 1024×3 |
|---|---|---|---|
| **Structural** | one extra process hop in JACK's serial callback chain | **~3 xruns/min regardless of loop count** (0 loops 3.00, 4 loops 1.33, 8 loops 2.67, 16 loops 3.40 — non-monotonic, flat inside noise) | **0.00** at 0/4/8 loops |
| **Load** | actual loop DSP work | masked by the structural term | **0.00 at 8 loops**, +0.80/min at 16 — and only with the full stack present |

Two consequences, both stated in `t9-loops8-d`:

- **The "fixed +0.80/min B→D cost" reading was wrong**, and the 8-loop condition-D cell is
  what broke it. The stack costs *nothing* at 8 loops and is expensive at 16. It is an
  interaction, not an additive cost.
- **"SooperLooper is 71% of the cost" is a 512-specific observation** and must not be
  carried to 1024 conclusions.

**Why this is the transferable part.** The structural term is not a *compute* cost — the
client's workload is irrelevant to it. It is the cost of an additional scheduling handoff
inside a deadline. That predicts it scales with *deadline slack*, not with clock speed:
invisible when the period is long, fatal when it is short. This is the one Pi 4 finding
that generates a falsifiable Pi 5 prediction (§6).

**Shipping position it supports:** 1024×3, condition D (full stack), 8 loops is clean at
n=15 (15/15). 16 loops is 13/15 clean, mean 0.13. That claim is measured on the
configuration that actually runs, not on a reduced condition.

---

## 4. The confound we found in our own instrument — and why it had to be fixed first

**G2, closed 2026-08-23.** The poly governor shipped with `MPE_POLY_CPU_HIGH=50.0` /
`LOW=40.0`. Cloud Horn @5 runs **clean** at 56.9–59.4% DSP. The high threshold sat *below*
measured clean load and the low threshold sat below every clean point in the reference
suite — meaning the governor would engage during normal playing and, once engaged, could
not release.

**This is not a footnote. It is the reason this document could not have been written a week
ago.** Until G2, "the Pi 4 cannot do 512×2" was partly "our voice governor would not let
it." Any close-out written before G2 would have been confounded by our own configuration.

Recalibrated to `HIGH=78.0` / `LOW=68.0` and verified with both arms:

| arm | condition | result |
|---|---|---|
| Negative | Cloud Horn @5, 30 min | 0 governor engagements · 8 xruns · `dsp_max` 78.3 |
| Positive | Crystals @6, 3 min (deliberate overload) | 1 emergency engagement |

**Open question on the negative arm — closed 2026-08-23 (O4):** `dsp_max` 78.3 with
`HIGH` 78.0 and zero engagements looked like a near-miss. It was not. The governor reads
**process/OSC CPU** via `SurgeCpuMonitor` at ~6.7 Hz (`poll_interval=0.15 s`,
`raw_percent` — `surge_poly_governor._cpu_sample`). The soak's `dsp_max` is
**`jack_cpu_load`** at ~1 Hz — different instrument, different denominator (callback time
vs period deadline, not `/proc` jiffies), different sampling rate. **78.0 and 78.3 were
never the same quantity**, so the negative arm did not demonstrate a 0.3 pp margin — it
demonstrated **non-engagement**, which is weaker and sufficient. G2's `HIGH=78.0` /
`LOW=68.0` are calibrated against the meter the governor actually reads; that is what
counts for shipping. Do not audit or tune those thresholds from soak `dsp_max`. The
positive arm confirms the governor is not blind.

---

## 5. Is the Pi 4 a serviceable instrument? — the precise version

Short answer: **yes at 1024×2, with a bounded polyphony ceiling, and the limitation is
capacity rather than fidelity.** The precise statements:

**5a. What is measured.** At 1024×2 (42.7 ms), governor off, condition A, verified-clean
load, three weight classes hold 0 xruns over 3×45 s cells: Cloud Horn @5 (~57% DSP), Duduk
@3 and Brave New World @3 (~38%). Dropping 1024×3 → 1024×2 cost nothing measurable —
64.0 ms → 42.7 ms for free.

**5b. The tension that must be stated with it.** The B2 8-hour soak measured **991 xruns =
2.06/min at 1024×2** — the config V9 called free. Both readings are correct: short cells at
verified-clean load are clean; an 8-hour window at playing load is not. Given X1's Fano
factor of **4.32**, short windows systematically under-detect. **"1024×2 is free" is true of
the cells V9 ran and is not a shipping claim about an evening of playing.** G2's negative
arm (8 xruns / 30 min = 0.27/min) is a better-behaved figure, but its buffer configuration
must be confirmed before it is compared to B2.

**5c. The real limitation is polyphony, not audio quality.** From the 53-patch survey at
1024×3, governor off:

| tier | sustained-clean voices | count | share |
|---|---|---|---|
| polyphonic | ≥8 | 38 | 72% |
| limited | 3–7 | **15** | **28%** |
| pad-only | 1–2 | 0 | — |
| unplayable | fails at 1 voice | **0** | — |

Seven patches — including **Crystals** and **Duduk** — sustain **3 voices** and first
overrun at 5. Crystals @3 and Cloud Horn @5 were re-confirmed clean over 60 s, so the low
end is not a probe artefact.

**Read musically: on 28% of the library the Pi 4 cannot hold a four-note chord, and on
seven patches a triad is the ceiling.** That is the honest form of "lower audio quality" —
it is a *capacity* limit that constrains what can be played, not a *fidelity* limit. Nothing
in this arc measured distortion, aliasing, noise floor, or bit depth, and no claim about
those is supported.

**5d. Two caveats on the ceiling table, both load-bearing.**
- **38 of 53 patches are censored** at the 15-voice probe cap. Their true ceilings are
  **≥15**, not 15. The "3 → 15 spread" is a lower bound on bounded patches only and **must
  not be quoted as a measured range.**
- **The ramp is noisy at the knee, not biased.** V8-REVIEW predicted ceilings were
  systematically optimistic; V9 fired that pre-registered falsifier — the counts held under
  longer confirms. The defect is variance: the same 8 s probe on the same patch returned 7
  and then 5. A single probe near the knee is close to a coin flip, because the event rate
  there is low by definition. **Repeat at the candidate count (n≥3) and take the worst.**

**5e. What the governor changes about the failure mode — and the limit of what we know.**
With the governor correctly calibrated, exceeding the polyphony ceiling should surface as
**voice stealing** rather than as **xruns**: notes are dropped instead of the audio stream
breaking up. That is a strictly better failure mode for an instrument.

**Whether stolen voices are audible, and how objectionable, is UNMEASURED.** B3 (steal
audibility) has not been run, and fade actuation (V7 Fix 2) is not merged — so steals are
currently expected to be abrupt rather than faded. **No claim about the subjective quality
of governed playing is supported by any measurement in this arc.** It is the single largest
gap between what is measured and what a player would care about.

---

## 6. What transfers to Pi 5, and what does not

The failure mode to avoid is porting Pi 4 *constants* rather than Pi 4 *method*.

### Transfers as method
- **Fano 4.32 → window sizing.** Effective sample size ≈ n / Fano. Event-count windows need
  ~4× longer than Poisson math suggests. Applies to any counting process on any board.
- **The osc-vs-filter split.** Crystals / Cloud Horn (oscillator-dominated) vs Duduk
  (filter-dominated, and floor-3 class on a *single* oscillator). Oscillator count does not
  predict cost on this library; expensive filters are ~5× more common than exotic
  oscillators (12/53 vs 2/53).
- **Pre-registration with a stated falsifier.** V9 is the proof it works: the falsifier was
  written in advance and it fired against its author's hypothesis.
- **Rule −1** — an instrument must never be able to fail silently. Ten occurrences on Pi 4.

### Must be re-derived on Pi 5 — do not port
| quantity | Pi 4 value | why it cannot carry |
|---|---|---|
| Fixed per-callback cost `a` | 0.13 ms | Sets what small buffers cost and predicts whether 256 is reachable. Re-fit it. |
| Governor thresholds | HIGH 78 / LOW 68 | Derived from Pi 4 clean load. Porting them **reproduces the exact bug G2 just fixed.** |
| xrun mechanism | graph overrun, never underrun (W1) | Measured with USB on CPU0. RP1 puts USB behind PCIe — different path, re-verify before interpreting any Pi 5 xrun figure. |
| IRQ topology | full census | **Void.** RP1 southbridge invalidates it entirely. |
| Polyphony ceilings | 3 → ≥15 | Whole point of the platform change. |

### Falsifiable predictions this arc generates
1. **The structural looper term should shrink or vanish at a given buffer.** It is a
   deadline-slack phenomenon (§3): a faster core leaves more slack per period, so the extra
   scheduling handoff that was fatal at 512 on Pi 4 should be affordable. **If the
   structural term persists at 512 on Pi 5, it is not compute-bound and the §3 model is
   wrong** — a clean falsifier worth pre-registering.
2. **`a` should fall**, and by less than the DSP work falls, since it is dominated by
   scheduling and graph traversal rather than arithmetic.
3. **The osc/filter cost ratio may invert.** A76 changes cache and memory behaviour, and
   filter inner loops and oscillator inner loops are differently sensitive to it.

### The asymmetry that must appear in any cross-platform result
**`linux-image-rpi-2712-rt` does not exist** (confirmed by `apt-cache` on the Pi 5,
2026-08-23). `linux-image-rpi-v8-rt` is the Pi 4 kernel and will not boot a 2712.
**PREEMPT_RT is a Pi 4-only lever.** Any Pi 4 → Pi 5 comparison must state whether the Pi 4
arm used an RT kernel; if it did, the comparison is not clean on that axis and the document
must say so rather than reporting a single ratio.

---

## 7. Open cells — what this draft is still missing

| # | Item | Why it blocks the close-out | Cost |
|---|---|---|---|
| O1 | ~~**P7**~~ | **Ran 2026-08-23; comparison invalid** — OC half clean @2000; baseline half unloaded (~7% vs ~52% Cloud Horn @5). H6 inconclusive; re-run baseline before citing. `P7-RESULT-2026-08-23.md`. | re-run baseline |
| O1b | ~~**Wire plausibility floors into the load harness**~~ | **Done 2026-08-23.** `measure-latency-run.sh` + soak **per-minute** window via `mpe_result_assert_loaded_dsp` (extended after V12; minute-1-only missed 1024 decay). **Preflight gate (2026-08-23):** `measure-soak-preflight.sh` / `--preflight-only` — poly JSON + 45s DSP ≥28% @512 (was 50%; lowered post P5 A/B). | done |
| O2 | **A4** — reference pass 2 | The only thing that puts an error bar on any Pi4→Pi5 ratio. Was a curiosity about a closed lever; now load-bearing. | one suite pass |
| O3 | **V12** — buffer compare | **Reconciled 2026-08-23 18:08** — A/B on Pi: canonical **~33%** @512 (cells A/D); **no reload → ~7.6%** (cell C); **hw:2 not testable** (jackd failed). Historical **~58%** not reproduced; reference band stale for this appliance. Preflight floor **50% → 28%** (2026-08-23). **Unblocked pending Pi preflight pass** at new floor — see `V12-PARITY-2026-08-23.md` §A/B + preflight floor update. | unblocked pending Pi preflight |
| O4 | ~~**G2 threshold statistic** (§4)~~ | **Closed 2026-08-23.** Two instruments — governor proc/OSC @ 6.7 Hz vs soak `jack_cpu_load`. Negative arm: non-engagement, not margin. See §4, `G2-RESULT-2026-08-23.md` §O4. | done |
| O5 | **B3** — steal audibility | The gap in §5e between measured capacity and player-relevant quality. | ear test |
| O6 | ~~Pi hot-patch not in repo~~ | **Done 2026-08-23.** Pi @ `1c165b9`; soak script matches repo (hot-patch removed). | done |

**O4, O6 and O1b are closed.** V12 canonical load **~33%** reconciled; reference **~58%** not restored. Preflight floor lowered to **28%** — **unblocked pending Pi `--preflight-only` pass**. Next: Pi pull + preflight, then V12 short or A4 pass 2; optional Mitch gate to unplug USB and retry **hw:2** cell. O1 (P7 baseline) and O5 unchanged.

---

## 8. Retractions on the record

Kept deliberately. A close-out that lists only successes is not evidence of systematic
investigation.

| retracted | superseded by | lesson |
|---|---|---|
| Looper costs ~30 DSP points | Looper stack cost 2026-08-19 | An experiment that changes three variables at once attributes their sum to one. |
| Fixed +0.80/min B→D looper cost | t9-loops8-d | Two cells agreeing does not establish a constant; the third cell broke it. |
| "512 is clean" | X1 | Window sized by convention, not by expected event rate. |
| Ceilings systematically optimistic | V9 | Pre-registered falsifier fired **against its author**. The defect was variance, not bias. |
| `unison_voices` scalar | `parse-fxp-metadata.py` | Engine selectors were being summed as voice counts. Fabricated metric, cited in decisions before it was caught. |
| Governor "correctly quiet" at 50/40 | G2 | Thresholds set below measured clean load — our own configuration confounding our own platform conclusion. |
| P7 clock comparison (@1800 baseline) | §2a | Load silently failed to apply; harness reported a plausible number below its own plausibility floor and exited clean. |

---

*Companion documents: [`MEASUREMENT-DISCIPLINE.md`](MEASUREMENT-DISCIPLINE.md) (Rules −1, 0.5, 0–7) ·
[`../SRED-EVIDENCE-2026.md`](../SRED-EVIDENCE-2026.md) (U1–U10) ·
[`pi5-predictions-2026-08-23.md`](pi5-predictions-2026-08-23.md) (pre-registered, do not edit retroactively).*