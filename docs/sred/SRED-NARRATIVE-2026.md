# SR&ED technical narrative — MPE Sound Module

**Claimant:** Ops Machine (Mitch Schwartz)
**Project:** Headless polyphonic MPE synthesis appliance on ARM single-board hardware
**Work period covered:** 2026-07-18 (first commit) to 2026-08-28
**Prepared:** 2026-08-28

> **This is a technical narrative, not an eligibility determination.** Whether this
> work qualifies, which costs are claimable, and how it maps to your fiscal year are
> your accountant's calls. What follows is the engineering record organised the way
> CRA asks for it, with every claim pointing at contemporaneous evidence in the repo.

---

## 0. Why this record is unusually strong

The claim rests on **59 measurement documents** written *during* the work, not
reconstructed afterward. Several record hypotheses that turned out to be **wrong**,
and say so explicitly. That matters more than the successes: a systematic
investigation is evidenced by its failed hypotheses, and those are the thing nobody
can credibly recreate after the fact.

Concrete examples, all in `docs/measurements/`:

| document | what it records |
|---|---|
| `PATCH-COST-what-makes-them-heavy.md` | A **retraction**. An earlier analysis named unison as the dominant CPU cost and "the only lever with an order of magnitude in it." Reading the patch files directly showed unison is 1–2 across every heavy patch, and the field being read was an *engine selector*, not a voice count. |
| `HANDOVER-census-unison-fix.md` | A measurement **instrument** found to be fabricating a value that was already being cited in decisions. |
| `classic-midi-router-hop-2026-08-28.md` | A latency result **discarded** for a confound (connection setup inside the measured arm), with the biased numbers preserved alongside the corrected ones. |
| `classic-midi-phase2-hardware-2026-08-28.md` | A **prediction that did not hold**, recorded as refuted rather than deleted. |
| `PI5-LOOPER-SEAM-WRAP.md` | Two premises **measured false**, closing out a line of work that had been built on them. |
| `pi5-predictions-2026-08-23.md` | Predictions **pre-registered** before the hardware booted, marked never to be edited retroactively. |
| `MEASUREMENT-DISCIPLINE.md` | A written analysis of the project's own recurring failure mode, with a cost table. |

---

## 1. Uncertainties addressed

CRA's first question is what could not be resolved by standard practice. Six threads.

### 1.1 Whether the target hardware could sustain the required polyphony at all

**Uncertainty.** Whether a single-board ARM computer could compute the required number
of simultaneous synthesis voices, for this specific patch library, inside a real-time
audio deadline — and if not, whether the limit was the processor, the software's
threading model, the audio buffer configuration, or patch construction. Published
benchmarks do not answer this: the workload is a specific synthesiser running specific
patches under a specific buffer regime.

**Work performed.** A staged elimination across buffer sizes, sample rates, CPU
governor settings, IRQ affinity, an overclock used *as a diagnostic instrument* rather
than as a fix, and a compiler-architecture rebuild (`-mcpu=cortex-a72`) measured
against a stock control binary. Each hypothesis was recorded before testing and
eliminated on evidence.

**Advancement.** A narrowly stated, defensible platform conclusion — deliberately
scoped to the hardware, patch library, and buffer regime measured, and explicitly
*not* generalised. `PI4-CLOSEOUT-2026-08-23.md` excludes corroborating data from the
newer board so the conclusion stands or falls on its own evidence.

**Evidence.** `PI4-CLOSEOUT-2026-08-23.md`, `CEILING-ANALYSIS-what-maxed-out-means.md`,
`P7-RESULT-2026-08-23.md`, `reference-suite-pi4-a3-a72-comparison-2026-08-22.md`,
`MULTITHREADING-ASSESSMENT.md`.

### 1.2 Whether the measurements themselves could be trusted

**Uncertainty.** This is the project's deepest recurring problem and is itself a
technological uncertainty: for several instruments it was unknown whether a reading
distinguished a working system from a broken one. A counter believed to mean "the
audio buffer emptied" did not. A patch-metadata parser emitted a voice count that was
not in the file. A MIDI port reported as open was subscribed to nothing, and the
startup banner read identically in both cases.

**Work performed.** Instruments were audited before their outputs were trusted;
positive controls were introduced so a null result could be distinguished from a dead
instrument; and the kernel's own subscription graph was consulted rather than the MIDI
library's return value. The recurring failure mode was analysed and written up.

**Advancement.** A reusable discipline — pre-registration, positive controls,
instrument audit before interpretation — plus specific corrected instruments. The
project now detects a class of fault it previously could not see.

**Evidence.** `MEASUREMENT-DISCIPLINE.md`, `HANDOVER-census-unison-fix.md`,
`X1-confirm-vs-soak` prompt and result, `scripts/sooperlooper/midi_subscription.py`
(module docstring records the seventeen-minute silent failure that motivated it).

### 1.3 Adaptive voice limiting under real-time load

**Uncertainty.** Whether polyphony could be constrained dynamically, from a live load
signal, without the limiter itself causing audible artefacts or becoming a load source.
The uncertainty was both control-theoretic (what signal, what headroom, what response
curve) and practical (whether measurement could be done inside the audio path at all).

**Work performed.** Instrumentation first, then a v2 "always-on" model driven by the
audio server's own load figure, calibrated by ear against measured headroom.

**Advancement.** A working adaptive governor with characterised behaviour under load.

**Evidence.** `poly-governor-instrumentation-2026-08-21.md`,
`poly-governor-v2-always-on-pi5-2026-08-23.md`, `G2-RESULT-2026-08-23.md`.

### 1.4 Seamless audio loop continuity

**Uncertainty.** How to close a recorded audio loop so playback continues across the
join without a discontinuity, on a system where the recorder, the synthesiser, and the
control layer are separate processes with independent timing.

**Work performed.** An offline "seam weld" approach was built, instrumented, and
measured. Its premises were then tested directly and **both were found false** — the
operation believed to halt playback did not, and a parameter sweep believed to be
tuning the join was in fact measuring the landing error of a subsequent retrigger.

**Advancement.** The join was resolved by a different mechanism entirely, and the
false premises are recorded so the abandoned approach is not re-derived.

**Evidence.** `PI5-LOOPER-SEAM-WRAP.md` (close-out section), `multi-clip-slot-spike-2026-08-26.md`,
`multi-clip-p2-composition-failure-2026-08-27.md`.

> **See §4** — the majority of this thread was performed by a different agent/session
> and needs its own account.

### 1.5 Translating conventional MIDI instruments into an MPE-only signal path

**Uncertainty.** The appliance's synthesis engine runs in a mode where per-note
expression is carried on separate MIDI channels. A conventional keyboard sends
everything on one channel. It was unknown whether the two could coexist without
restarting the engine — which would be unacceptable in live use — and unknown whether
a device's expressive capability could be determined automatically at all.

**Work performed.**
- Established by reading the synthesiser's source that **no runtime control path exists**
  to change its expression mode or bend range. This eliminated the obvious approach and
  forced a translation layer.
- Measured the cost of an additional processing hop **before** building on it
  (0.053 ms median), and discarded a first result found to be confounded.
- Designed channel allocation with voice stealing, bend-range rescaling between
  differing conventions, and note retriggering.
- Investigated whether a control surface with an instrument mode could serve both roles
  at once. Three outcomes were enumerated in advance; capture determined the answer, and
  **an initial conclusion was published and then retracted** when a cleaner experiment
  showed the first capture had a mode change inside it.
- Designed a behavioural classifier for devices that do not announce their capability,
  with an explicit constraint that a false positive is worse than a miss.

**Advancement.** Conventional MIDI instruments now play the appliance from a cold boot
with no user configuration, while the existing expressive path is preserved. Real
hardware behaviour was characterised that is not documented by the manufacturer,
including a device announcing its mode over a vendor-specific message, and a pad
controller emitting duplicate note events (~4% of messages) that the translation layer
must absorb.

**Evidence.** `docs/CLASSIC-MIDI-PLAN.md`, `classic-midi-router-hop-2026-08-28.md`,
`classic-midi-phase2-hardware-2026-08-28.md`, `classic-midi-phase4-coldboot-2026-08-28.md`,
`tests/fixtures/apc-mini-mk2-notes-2026-08-28.jsonl` (captured hardware output retained
as a test fixture).

### 1.6 Platform configuration for real-time determinism

**Uncertainty.** Which of interrupt affinity, CPU governor pinning, core allocation,
power delivery, and thermal behaviour actually affect audio deadline misses on this
hardware — as opposed to being folklore carried over from desktop audio tuning.

**Work performed.** A census-based investigation with loaded and unloaded captures,
recording throttle flags, temperature, and clock alongside audio performance, with
power-supply conditions stated as part of the measurement conditions.

**Advancement.** An evidence-based configuration baseline, with several widely repeated
tuning assumptions tested rather than adopted.

**Evidence.** `PI5-IRQ-INVESTIGATION-PLAN.md`, `pi5-irq-phase1-2026-08-23.md`,
`PI5-HYGIENE-AND-CONFIG-PLAN.md`.

---

## 2. What is deliberately excluded

Listing this protects the claim. The following were performed in the same period and
are **routine engineering**, not experimental development:

- Deployment scripting, service unit configuration, git and release mechanics.
- Straightforward defect fixes with no uncertainty — e.g. a logging call that warned on
  a normally-absent file, and a startup gate that checked for the wrong device.
- Writing tests for behaviour already understood.
- Documentation, refactoring for readability, and user-interface layout work.

Where a debugging episode *did* involve genuine uncertainty, it appears in §1 under the
thread it belongs to, not as a separate item.

---

## 3. Suggested T661 wording

**Line 242 — technological uncertainties.** Six threads, above. If a single framing is
needed: *whether a general-purpose ARM single-board computer could host a
polyphonic, per-note-expressive synthesis instrument with live looping at usable
polyphony inside real-time audio deadlines — and, at each stage, whether the
instruments used to answer that question were themselves sound.*

**Line 244 — work performed.** Staged hypothesis elimination across hardware, compiler,
buffer, scheduling and patch-construction variables; instrument audit and correction;
adaptive load control; and a translation layer between two incompatible MIDI
expression conventions. Evidence is contemporaneous and includes recorded failures.

**Line 246 — advancements achieved.** A characterised polyphony ceiling stated narrowly
to its conditions; a corrected measurement methodology with a documented failure mode;
an adaptive voice governor; a resolved loop-continuity mechanism with two disproved
premises recorded; and automatic conventional-MIDI interoperability with an MPE-only
engine, including hardware behaviour not present in vendor documentation.

---

## 4. Gaps to close before filing

1. **The looper thread (§1.4) is under-documented here.** Most of that investigation was
   carried out in a separate session. See `docs/sred/PROMPT-looper-agent.md` for the
   request to send to whoever holds that context.
2. **Time and cost records.** This narrative covers the *what*, not the *how much*. No
   hours are recorded in the repo.
3. **Fiscal year boundary.** The work period here is 2026-07-18 to 2026-08-28. Which
   portion falls in the claim year is your accountant's determination.
4. **Prior-period work.** If work on this project predates the first commit
   (design, research, hardware evaluation), it is not represented in the repo at all.
