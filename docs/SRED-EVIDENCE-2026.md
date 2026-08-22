# SR&ED evidence record — MPE synth appliance, low-latency instrument work

**Compiled 2026-08-22 from the repository record.** Read-only reconstruction: nothing in
here is new work, it is the existing docs, scripts, and git history organised into the shape
a claim is assessed in.

> **Not tax advice.** This is engineering evidence assembled for whoever prepares the claim.
> Eligibility determinations, expenditure allocation, and filing are theirs.

---

## 0. Why this document exists

Under the SR&ED definition, experimental development qualifies where work is undertaken to
achieve **technological advancement** in the face of **technological uncertainty**, through
a **systematic investigation** — hypothesis, experiment, analysis, conclusion.

The third criterion is the one this project is unusually strong on and the one most claims
are weakest on: **the record includes the failures.** Retractions, refutations, and
withdrawn conclusions are not embarrassments in this context. They are the primary evidence
that the work was genuine investigation rather than routine engineering with a known answer.

The criterion this project must be careful about is the second. A reviewer's first question
against any measurement is *"why couldn't you just look that up?"* — see §5.

---

## 1. Project identification

| | |
|---|---|
| **Project** | Headless MPE synth appliance — Raspberry Pi 4 (BCM2711, Cortex-A72), JACK2 + Surge XT + SooperLooper |
| **Repository** | `MPE-Module` |
| **Period covered** | 2026-07-18 (initial release commit) → 2026-08-22 (ongoing) |
| **Intensive investigation phase** | 2026-08-10 → 2026-08-22 |
| **Objective** | Reduce end-to-end latency for live instrument-only playing to the lowest value the hardware sustains without audio dropout, at usable polyphony across a real patch library |
| **Commits in period** | ~770 across all branches |
| **Measurement documents** | 74 in `docs/measurements/` (21 live, 53 archived) |
| **Measurement harness scripts** | 34 of 140 in `scripts/` |

---

## 2. Technological objective

Total output latency on this architecture is `period x nperiods` at the JACK layer. The
project objective was to move the shipping configuration down that ladder:

| config | total latency |
|---|---|
| 1024 x 3 | 64.0 ms *(shipping at period start)* |
| 1024 x 2 | 42.7 ms *(confirmed free, 2026-08-22)* |
| 512 x 3 | 32.0 ms |
| 512 x 2 | 21.3 ms *(target; untested at confirmed loads)* |
| 256 x 3 | 16.0 ms |

The constraint is that reducing the buffer shortens the compute deadline. The advancement
sought was **knowledge of what actually binds the deadline on this platform**, because
without it every buffer reduction is guess-and-listen.

---

## 3. Technological uncertainties

Each of these was a genuine unknown at the time work began, each was resolved by
experiment, and each resolution changed subsequent engineering decisions.

### U1 — What does an xrun on this appliance actually represent?
**Uncertainty.** The system reported xruns, but the counter's semantics under JACK2/ALSA on
this platform were not established. Two mechanisms produce the same count: the output ring
buffer draining (an *underrun*, a delivery problem) versus the processing graph missing its
deadline (an *overrun*, a compute problem). They call for opposite remedies.

**Resolved.** `W1-VERDICT-compute-bound-2026-08-21.md`. Journal instrumentation during W1-c
found `JackEngine::XRun: Surge XT was not finished` and **zero** `ALSA: xrun` lines at every
buffer size. Buffer fill sat flat at ~83% throughout. **Every xrun this project has ever
measured is a graph overrun; the ring buffer has never drained.**

**Consequence.** Retired an entire class of work — the ~600 µs jitter hunt, the cushion
depletion model, the audio-interface swap — all of which targeted a term that does not exist
in this system. Archived: `find-600us`, `cushion-model`, `scarlett-*`.

### U2 — Is there a fixed per-callback cost, and how large?
**Uncertainty.** If per-callback overhead is a large constant, small buffers are structurally
unaffordable and the whole latency objective is dead. If it is small, the voice ceiling is
approximately buffer-independent and low-latency configs are reachable.

**Resolved, after one wrong answer.** W1 fitted **a = 1.10 ms** across three cells. V1's
direct silence test refuted it: **a = 0.13 ms**, roughly 8x smaller.
(`v1-fixed-cost-2026-08-21.md`, `V1-VERDICT-no-fixed-cost-2026-08-21.md`.)

**Consequence.** 0.13 ms is 0.6% of the deadline at 1024, 1.2% at 512, 2.5% at 256. This is
the single number the current roadmap rests on. It also killed a planned single-client
refactor (measured benefit: 35 µs).

### U3 — Is the measurement instrument itself trustworthy?
**Uncertainty.** Recurring, and the most expensive class encountered. An instrument that
reads *clean* when it is *blind* is indistinguishable from a passing test.

**Four separate occurrences**, each found and documented: V8-b patch auto-pick,
`mpe-peak-meter` shutdown path, the V10-b ramp probe (`xruns_delta=0` where the confirm
harness read 275 on the identical load), and a fabricated `unison_voices` field in the patch
census (`HANDOVER-census-unison-fix.md`).

**Consequence.** `MEASUREMENT-DISCIPLINE.md` (rules 0–7), the `measurement-design` skill, and
an instrument self-test doctrine in `AGENTS.md`. `measure-capacity-ramp.sh` demoted to
screening-only; all comparative claims restricted to the confirm harness.

### U4 — What determines per-voice compute cost across a real patch library?
**Uncertainty.** Whether patch cost was predictable from visible structure, which would let
a poly governor allocate voices per patch rather than to a single worst case.

**Resolved, against the intuitive hypothesis.** A 53-patch census
(`scripts/parse-fxp-metadata.py`, `run-quick-select-census.sh`) plus per-patch capacity
measurement found **oscillator count does not predict cost on this library — filter choice
does more of the work.** `filter1 >= 10` on 12/53 patches (23%) versus any Twist/Plaits on
2/53 (4%). Duduk is floor-3 class on a *single* unmuted oscillator.
(`PATCH-COST-what-makes-them-heavy.md`, `v8-patch-capacity-2026-08-21.md`.)

**Consequence.** A unison-stacking cost theory was raised, tested, and **retracted**. The
governor's threshold model needs recalibration against filter cost, not oscillator count.

### U5 — Does probe duration bias measured capacity?
**Uncertainty.** V8-a's ramp reported Cloud Horn sustained-clean at 7 voices; V8-b held the
same patch at 7 voices for 45 s and it overran. Either short probes are optimistic, or the
two harnesses differ.

**Resolved, and the first hypothesis was wrong.** V9 showed 275 xruns at 8 s on the confirm
harness — duration was *not* the split. The cause was a missing strict-mode enable per probe
in the ramp harness. (`v9-probe-duration-2026-08-22.md`, `V9-REVIEW`, `V10-b-ramp-probe-fix`.)

### U6 — Is the cushion (`nperiods`) load-bearing below the overrun knee?
**Uncertainty.** Prior comparisons of `x2` vs `x3` were all taken at overload, where the
deadline binds identically regardless of cushion — so they could not answer the question.

**Resolved.** V9-d, four patches at confirmed-clean voice counts, 3 x 45 s, condition A:
`1024 x 2` produced **0 xruns** across all cells. **42.7 ms was available and unclaimed.**

### U7 — Is the platform compute-bound, and which levers remain?
**Open in part.** `CEILING-ANALYSIS` audited the "the Pi is maxed out" claim and found it
false as stated: one core of four, 83% of maximum clock. `MULTITHREADING-ASSESSMENT` scoped
the parallelism lever (~3x polyphony, multi-week upstream C++). P7 (clock scaling diagnostic)
and P8 (`-mcpu=cortex-a72`) remain unrun.

---

## 4. Systematic investigation — chronology

Commit density by day is in git; the shape of the investigation is below. Every phase
follows hypothesis → experiment → analysis → revised hypothesis.

| dates | phase | what was tested | outcome |
|---|---|---|---|
| 07-18 → 08-09 | Build-out | Appliance assembly, Surge ARM64 build, SooperLooper integration | Baseline platform |
| 08-14 → 08-18 | First fault isolation | Audible crackle at 512 x 3 | **Root cause: the monitoring probe.** `jack_lsp` every 10 s re-registered a JACK client, forcing graph rebuild mid-audio. First instance of U3. |
| 08-18 → 08-19 | Looper cost characterisation | Looper stack cost, MIDI/OSC latency, systemd liveness cost, peak-meter DSP cost | Per-component cost model; several probes found to be self-inflicted load |
| 08-19 → 08-21 | Jitter hunt (**refuted line**) | Steps 0–4: storage/swap/journald audit, cyclictest floor and under load, RT throttle, IRQ census, frame alignment, audio interface swap (Scarlett 4i4 vs onboard) | Worst measured stall 429 µs against a 42.7 ms cushion — factor of 100. Alignment closure **withdrawn** (n=3). Scarlett verdict: **the bottleneck is the Pi.** |
| 08-20 | Core allocation | E1: `irqaffinity=0` + `CPUAffinity=1 2 3` | **Refuted, and the design flaw recorded explicitly** — two variables changed at once. Reverted. |
| 08-21 | W1 instrumented window | Journal-level xrun classification, fill-level poller | **U1 resolved.** Retires the entire jitter line above. |
| 08-21 | V0/V1/V2 | Silence test for fixed cost; client-count DSP | **U2 resolved**, W1's own model retracted 8x |
| 08-21 → 08-22 | V7/V8 patch capacity | Capacity curve; 53-patch survey; census parser | **U4 resolved** against the intuitive hypothesis |
| 08-22 | V8 review → V9 | Probe duration; `x2` vs `x3` below the knee | **U5 and U6 resolved.** `1024 x 2` shipped-eligible |
| 08-22 | V10-b | Ramp probe counting defect | Instrument fixed; ramp demoted to screening |
| 08-22 | Ceiling / multithreading analysis | Audit of the "maxed out" premise; parallelism scoping | **U7 partially resolved**; P7/P8 queued |

**Documented retractions** (evidence of genuine iteration, not post-hoc narrative):
W1's `a = 1.10 ms`; the E1 core-allocation config; the unison cost theory; the probe-duration
hypothesis; the Scarlett alignment closure; the cushion depletion model; the `ondemand`
governor pop theory; the single-client refactor.

---

## 5. Prior-art position — the reviewer's first question

Honest self-assessment. Not everything measured needed to be.

**Was publicly available and should have been read, not measured** — weak ground for a claim,
and the correct disposition is to attribute little or no effort to these:

- JACK buffer arithmetic (`period x nperiods`, deadline vs cushion) — JACK documentation
- Pi 4 clock and thermal parameters (`arm_boost`, `arm_freq` ceiling, `over_voltage` steps,
  80/85 °C throttle points) — published Raspberry Pi specifications
- Cortex-A72 microarchitecture; NEON mandatory on aarch64 — ARM documentation
- Surge's `processBlock` being single-threaded — readable from upstream source
- IRQ assignments (30 = xhci, 41 = mmc) — `/proc/interrupts`

**Was not publicly available and could not be** — the substance of the claim. In each case
the answer depends on the interaction of this specific hardware, this JACK/Surge/looper
graph, this configuration, and this patch library:

- U1: that all xruns here are graph overruns, and that
  `jack_get_xrun_delayed_usecs` returns 0 on this JACK2/ALSA path so magnitude data never
  exists — the *behaviour* is documented; that it makes the appliance's primary metric
  blind to severity is a system-specific finding
- U2: `a = 0.13 ms` on this graph
- U4: filter cost dominating oscillator count across these 53 patches
- U6: cushion non-binding below the knee at these confirmed loads
- U3: every instrument defect — these are defects in code written for this project

**Recommended process change, forward-looking.** Each experiment prompt should carry a
*"prior art checked"* paragraph: what was looked up, what it did and did not answer, why
measurement remained necessary. Cheap to write at design time, expensive to reconstruct at
claim time. This aligns exactly with Rule 0 of `MEASUREMENT-DISCIPLINE.md` (cheap-first),
so it costs nothing the discipline does not already demand. **Start with V11.**

---

## 6. Evidence inventory

| kind | location | notes |
|---|---|---|
| Hypothesis / result documents | `docs/measurements/` (21 live) | One file per experiment, conditions recorded |
| Superseded and refuted lines | `docs/measurements/archive/` (53) | **Deliberately retained** — provenance for retractions |
| Methodology | `docs/measurements/MEASUREMENT-DISCIPLINE.md`, `AGENTS.md`, `measurement-design` skill | Rules 0–7, pre-registration, instrument audit |
| Current thread and queue | `PROGRESS.md` | Goal, state, ordered queue, standing rules |
| Measurement harnesses | 34 scripts in `scripts/` | Reproducible; conditions parameterised |
| Raw run logs | On appliance (`~/*.log`), referenced by path in each doc | **See gap G3** |
| Chronology | Git history, ~770 commits, dated | Commit messages state the finding, not just the change |
| Peer review | PRs #86, #88, #90–#95 | Review comments are contemporaneous critique |

---

## 7. Gaps to close before filing

| # | gap | why it matters | effort |
|---|---|---|---|
| **G1** | **Person-hours are not recorded.** Git timestamps show *when* commits landed, not effort expended. Claims are computed from labour. | Highest-value gap | [`PROMPT-G1-effort-reconstruction.md`](measurements/PROMPT-G1-effort-reconstruction.md) — interactive walkthrough with Mitch, phase by phase |
| **G2** | **Prior-art searches are undocumented.** §5 is reconstructed after the fact, not contemporaneous. | Directly answers the reviewer's first question | Adopt the prompt paragraph going forward; §5 covers the past |
| **G3** | **Raw logs live on the appliance**, referenced by path but not archived in-repo. An SD card failure destroys the underlying data behind every number. | Evidence durability, and this is an audio appliance whose SD card shares IRQ 41 with WiFi | [`PROMPT-G3-archive-raw-logs.md`](measurements/PROMPT-G3-archive-raw-logs.md) — **only when the Pi is idle** |
| **G4** | **Uncertainties were never stated as such at the time.** They are inferred here from what the experiments tested. | Reviewers look for uncertainty stated up front | This document establishes them; carry the framing forward into new prompts |
| **G5** | **No separation of eligible from routine work.** Packaging, docs, CLI ergonomics, and deploy tooling are interleaved in the same history. | Overclaiming weakens the whole submission | Tag or list the routine commits |

---

## 8. Explicitly outside the claim

Recorded so the boundary is drawn by us, not by a reviewer: appliance packaging and BOM,
README/onboarding documentation, CLI ergonomics, deploy and install scripting, and routine
dependency and build maintenance. These are engineering, not investigation.

---

## 9. Standing note

The archive is not dead weight. A refuted line with its refutation recorded is worth more
to this claim than a clean result with no history — it is the difference between showing an
answer and showing an investigation. **Do not delete `docs/measurements/archive/`.**
