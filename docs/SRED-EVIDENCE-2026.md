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

## 2a. State of knowledge — 2026-08-22

Recorded so the claim shows what was *established*, not only what was attempted.

### Established

| finding | evidence | status |
|---|---|---|
| All xruns here are JACK graph overruns; the ring has never drained | W1 journal instrumentation | settled |
| Fixed per-callback cost `a = 0.13 ms` (0.6% of deadline at 1024, 1.2% at 512) | V1 silence test; **cross-validated** by A2 loaded cells (+2.1–2.5 pp `dsp_med` rise 1024×2→256×3 vs ~1.83 pp predicted) | settled; **retracted W1's own 1.10 ms by 8x** |
| `1024x2` = 42.7 ms is free at clean load | V9-d, four patches, 3 x 45 s | settled |
| **`512x2` = 21.3 ms is clean for Crystals @3 and Duduk @3** | V11 xrun column, 0/0/0 x3 | **settled — a 3x reduction on the 64.0 ms shipping config, not yet shipped** |
| The latency floor is **patch-dependent** | V11: Cloud Horn @5 marginal (0/0/8) at the same config | settled (U9) |
| Filter choice predicts cost better than oscillator count | 53-patch census + capacity survey | settled (U4) |
| Live DSP at 256 under load ~80% | C0 live gate, 2026-08-22 | settled; refutes V11's 0.9-1.6% by ~50-80x |
| Instrument failure has **one** root cause across ten instances | Rule -1 analysis | settled (U3) |
| `-mcpu=cortex-a72` does not help | A3 null: all cells &lt;3% by pre-registration; same revision `253f8d86` | settled — **U7 `-mcpu` branch closed** |

### Not established — open

| question | why it is open |
|---|---|
| Does DSP scale inversely with clock? | P7 not run — and it is the cheapest available Pi 5 forecast |
| Is Cloud Horn genuinely unable to hold `512x2`? | 0/0/8 on three runs is a variance signal, not a verdict |
| The entire DSP picture at 512/256 | V11's column void; re-run pending |
| Does `1024x2` survive 8 h? | **Gate 1 soak never ran** (occurrence ten) |
| Do these findings hold on another ARM microarchitecture? | U10 — **Pi 5 instrument live; suite blocked on hardware** |

### Honest cost accounting

2026-08-22 consumed ~13 h hands-on and shipped **no latency improvement**. Nearly all of it went
into the measurement system. The justification is not that the instrument work was pleasant but
that the alternative was carrying an unaudited apparatus onto new hardware where no baseline
exists to catch an impossible reading. Nine prior conclusions rested on uncertified instruments
and several were retracted, including a fixed-cost model wrong by 8x that shaped roughly a week
of work before V1 caught it.

**One structural observation, recorded because it distinguishes convergence from thrash.** The
defects found across 2026-08-22 shrank monotonically in scope: an entire metric column (V11) ->
one 8-hour run lost silently (soak) -> one cell halted at 4 of 15 (C0 threshold) -> one field
dropped by a `printf` specifier count. Each is more peripheral than the last. That is the
signature of a converging investigation, not a widening one.

---

### U10 — Do the findings hold across ARM microarchitectures?
**Stated before the work, per gap G4.** Every result above is entangled with one SoC. `a = 0.13
ms`, the filter-over-oscillator cost finding, and the cushion result are each *either* a property
of the Surge/JACK stack *or* of a Cortex-A72 at 1.8 GHz, and one board cannot separate them.

Not resolvable by reference: Pi 5 specifications are **published and must be cited, not
measured**; how this specific audio graph behaves on it is not. Method: a frozen 15-cell
reference suite (A2, pass 1 established and revalidated), a predictions table committed **before
the board boots**, and a like-for-like pass before any optimisation.

**Status 2026-08-23:** Pi 5 player appliance operational at 128×2; IRQ/hygiene baseline captured;
predictions table committed ([`pi5-predictions-2026-08-23.md`](measurements/pi5-predictions-2026-08-23.md)).
**Frozen reference suite not yet run** — blocked on active cooler + 27 W PSU. Early platform
finding: RP1 USB/touch IRQ topology differs materially from Pi 4; Pi 4 affinity map only
partially transfers ([`pi5-irq-phase1-2026-08-23.md`](measurements/pi5-irq-phase1-2026-08-23.md)).

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

**Independent corroboration (2026-08-22, A2 pass 1 loaded cells).** On stock binary, `dsp_med`
rises **+2.34 / +2.11 / +2.48 pp** (Crystals / Duduk / Cloud Horn) from 1024×2 to 256×3 —
against **~1.83 pp** predicted from the fixed-cost fraction alone (`reference-suite-pi4-a3-a72-comparison-2026-08-22.md` §V1). Loaded-cell evidence, not the silence test.

### U3 — Is the measurement instrument itself trustworthy?
**Uncertainty.** Recurring, and by a wide margin the most expensive class encountered. An
instrument that reads *clean* while *blind* is indistinguishable from a passing test, so the
failure arrives as a **result** rather than an error and is believed, published, and built on.

**Resolved 2026-08-22 — and the resolution is a single root cause, not a list of bugs.**

> **Every instrument on this appliance returned its value and its failure through the same
> channel.** At the reading site there was no way to distinguish *"here is a measurement"* from
> *"I could not measure."* That is one missing convention, replicated everywhere because
> nothing enforced it.

**Ten documented occurrences**, spanning five days and four subsystems:

| date | instrument | returned | should have returned |
|---|---|---|---|
| 08-19 | `xrun-corr.sh` | exit 0, empty file, 12 runs | write failure |
| 08-19 | `set-surge-audio.sh` | continued without `sudo` — a run labelled 512 ran at 1024 | hard stop |
| 08-19 | latency tap v1 | `n=0` after 267 presses (wrong code path) | no-events error |
| 08-19 | latency tap v2 | `n=0` after 115 presses | no-events error |
| 08-21 | V8-b auto-pick | a plausible patch name — the wrong one | selection failure |
| 08-21 | `mpe-peak-meter` shutdown | looked stopped; was not | shutdown failure |
| 08-22 | V10-b ramp probe | `0` xruns via `\|\| start=0` swallowing a blind meter | blind-meter error |
| 08-22 | census `unison_voices` | plausible integer, summed engine selectors | unsupported-field error |
| 08-22 | V11 `dsp_med` | `unknown`, plus idle readings presented as measurements | field + alignment error |
| 08-22 | Gate 1 soak log | 253 bytes, header only, 4 h in — setup died under `set -e`, every failure path wrote to stderr or nowhere | aborted sentinel |

**The V11 case is the clearest and is independently corroborated.** `dsp_med` read 0.9–1.6% at
256x3 across three unrelated patches, including a cell with 23 xruns — a cell missing its
deadline is at ~100% by definition, so the reading was **arithmetically impossible**, not merely
wrong. The live conformance gate later measured **80.2% at 256 under load**: the original
readings were off by a factor of roughly 50–80. That is a measured refutation, not an inference.

**Consequence — the advancement.** The general principle was derived from the instances and
implemented as a mechanism, not a habit:

- **`MEASUREMENT-DISCIPLINE.md` Rule -1**, five required mechanisms: no in-band failures
  (`|| x=0`, `unknown`, continue-on-error all halt); a **positive control** asserting a reading
  is *right*, not merely present; a **negative control** asserting the harness halts when the
  instrument is broken; **physics assertions** rejecting arithmetically impossible results
  in-harness; and a **terminal sentinel on every exit path** for long runs, so "no result yet"
  and "died" stop sharing a channel.
- **`Rule 0.5` — pilot before running at length.** One cell, minimum window, read every field.
  Exit code 0 is not the check; every silent failure above exited 0.
- **C0 conformance suite** (`scripts/instrument-conformance.sh`, offline + live), with
  data-derived monotone plausibility floors (1024=7.6, 512=12.5, 256=15.2, anchored to V9/W1)
  and a monotonicity assertion on the thresholds themselves.
- Propagated to the `measurement-design` skill, `AGENTS.md`, and both pre-registration blocks.

**Independently validated on first use, same day:** the C0 gate found real defects when first
run on the Pi (PR #99); it then **refused to publish** reference-suite cell P1 under a
sample-count threshold it could not defend, halting at cell 4 of 15 rather than emitting fifteen
plausible values. The pilot rule caught that threshold defect in ~2 minutes, against the 25
minutes the equivalent V11 failure had cost two days earlier.

**Residual finding worth recording:** fixing the undefendable threshold introduced a
*fail-open* — a guard that silently did not apply when its environment variable was unset.
Caught in review and closed (`a1e80e3`, fail-closed). **The failure mode reproduces inside its
own remedy**, which is the strongest available evidence that the mechanism — not vigilance — is
what does the work.

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
the parallelism lever (~3x polyphony, multi-week upstream C++).

**`-mcpu=cortex-a72` branch closed (2026-08-22, A3).** Same Surge revision (`253f8d86`), flag-only
delta. All nine comparison cells **&lt;3%** dsp_med delta — pre-registered **no-effect**. Stock
binary kept as control; a72 artifact retained uninstalled. A clean negative on a compiler lever is
as valuable as a win for scoping what remains.

**Still open under U7:** P7 (clock scaling diagnostic — Pi 5 forecast instrument).

---

### U8 — Can a measurement system be made structurally incapable of silent failure?
**Uncertainty.** Distinct from U3, which asked whether *these* instruments were trustworthy.
This asks whether a general mechanism exists that makes the failure class impossible rather
than merely watched-for. Vigilance had already failed ten times, including twice *after* the
pattern was named in writing (`MEASUREMENT-DISCIPLINE.md`, 2026-08-21 21:38).

**Partially resolved.** Five mechanisms implemented and independently exercised (see U3).
Evidence that mechanism beats vigilance: the gate refused two runs on its first day, and the
fail-open regression inside the fix itself was caught by a structural review rather than by
care. **Not fully resolved** — the class is still appearing in *new* code (a `printf` with 14
format specifiers for 15 arguments silently dropped a field, 2026-08-22), which is the argument
for retaining the mechanisms rather than declaring the doctrine finished.

**Why this is not routine engineering.** The question is not "write better tests." It is whether
a measurement apparatus for a real-time audio system can be given a structural property —
value and failure on separate channels, with physics-level rejection of impossible readings —
that eliminates a failure mode empirically shown to survive documentation, review, and
experience. That is experimental development on the measurement system itself.

### U9 — Is the latency floor a property of the appliance or of the patch?
**Uncertainty.** All prior work sought a single shipping buffer configuration, implicitly
assuming one floor for the instrument.

**Resolved against that assumption, 2026-08-22 (V11).** At `512x2` (21.3 ms) Crystals @3 and
Duduk @3 held **0 xruns across three runs each**, while Cloud Horn @5 produced 0/0/8. At
`256x3` (16.0 ms) Duduk held clean and Crystals was marginal (0/2/2). **The floor is
patch-dependent, not a single appliance-wide constant.**

**Consequence.** Reframes the shipping decision from one global buffer to a per-profile
configuration the appliance already supports, and reframes marginal cells (two clean runs then
one bad) as a variance question requiring more runs, not a verdict from three.

**Note on evidence integrity.** V11's **xrun** column is sound and its **DSP** column is
withheld — the derived plausibility floors show the 512 readings were implausible too, not only
the 256 ones. The xrun path survived precisely because a positive control had been run against
it that morning; the DSP path had none. **A single 5-minute control is the entire difference
between half a usable result and none.**

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
| 08-22 | V11 buffer confirmation | `512x2` / `256x3` at confirmed voice counts | **U9 resolved** — floor is patch-dependent. **21.3 ms available** for 2 of 3 patches, a 3x reduction on the 64.0 ms shipping config. DSP column void — occurrence nine |
| 08-22 | Gate 1 soak | 8 h certification at `1024x2` | **Never ran.** Died in setup, logged nothing — occurrence ten. Certification outstanding |
| 08-22 | Rule -1 / Rule 0.5 / C0 | Root-cause analysis of ten instrument failures; conformance suite built, reviewed (F1-F5), fixed, and run live on Pi | **U8 partially resolved.** Gate green on Pi 2026-08-22; live DSP 80.2% @ 256 corroborates the V11 refutation |
| 08-22 | A2 reference suite | Frozen 15-cell suite, pass 1 at 25 s windows | Control established; revalidated offline against the later parser (`494e8b4`) — **control stands** |
| 08-22 | A3 `-mcpu=cortex-a72` | Same revision (`253f8d86`), flag-only rebuild vs stock on reference suite | **NULL — no-effect** (&lt;3% all cells). **U7 `-mcpu` branch closed.** V1 model cross-validated on loaded cells (+2.1–2.5 pp rise vs ~1.83 pp predicted). Duduk filter path: possible regression, pending A4 noise floor |
| 08-23 | **Pi 5 player bringup (U10)** | Second platform: clone + hygiene + IRQ Phase 0/1 + RT verify; 128×2 player tuning; loaded census @ 24v | **Instrument live; replication suite not run.** RP1 IRQ map differs (usb1/i2c CPU0, not writable); Pi 4 movable-IRQ script no-op. PREEMPT_RT N/A on 2712. Suite 1 blocked: 3 A PSU, no cooler. See [`PI5-SESSION-CLOSEOUT-2026-08-23.md`](measurements/PI5-SESSION-CLOSEOUT-2026-08-23.md) |

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
| Raw run logs | [`docs/measurements/raw-logs/`](measurements/raw-logs/MANIFEST.md) (G3 archive) + appliance | Manifest + SHA256SUMS in-repo |
| **Daily labour (contemporaneous)** | [`docs/SRED-DAILY-LOG.md`](SRED-DAILY-LOG.md) | **From 2026-08-22 onward** — skill `sred-daily-capture` |
| Reconstructed labour (G1) | [`docs/SRED-EFFORT-LOG.md`](SRED-EFFORT-LOG.md) | **Closed 2026-08-22** — reconstruction baseline only |
| Chronology | Git history, ~770 commits, dated | Commit messages state the finding, not just the change |
| Peer review | PRs #86, #88, #90–#95 | Review comments are contemporaneous critique |

---

## 7. Gaps to close before filing

| # | gap | why it matters | effort |
|---|---|---|---|
| **G1** | **Person-hours are not recorded.** Git timestamps show *when* commits landed, not effort expended. Claims are computed from labour. | Highest-value gap | **Closed** — [`SRED-EFFORT-LOG.md`](SRED-EFFORT-LOG.md) (reconstruction). **Ongoing:** [`SRED-DAILY-LOG.md`](SRED-DAILY-LOG.md) + **`sred-daily-capture`** |
| **G2** | **Prior-art searches are undocumented.** §5 is reconstructed after the fact, not contemporaneous. | Directly answers the reviewer's first question | Adopt the prompt paragraph going forward; §5 covers the past |
| **G3** | **Raw logs live on the appliance**, referenced by path but not archived in-repo. An SD card failure destroys the underlying data behind every number. | Evidence durability, and this is an audio appliance whose SD card shares IRQ 41 with WiFi | **Archive done** — [`docs/measurements/raw-logs/`](measurements/raw-logs/MANIFEST.md) (627 files, MANIFEST + SHA256SUMS). Re-run [`PROMPT-G3`](measurements/PROMPT-G3-archive-raw-logs.md) after major measurement pushes when Pi idle. |
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
