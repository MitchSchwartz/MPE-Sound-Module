# Pi 5 transition — what to do before the board arrives, and how to spend it

**2026-08-22.** Written after the Pi 5 was ordered, before it shipped.

---

## 0. The reframe that makes this worth doing properly

The instinct is "the Pi 5 is an upgrade, swap it in and go faster." That throws away most of
the value.

**A second platform is a second data point, and two data points let you separate what is a
property of this SoC from what is a property of the software stack.** Every finding in
`docs/measurements/` is currently entangled with one board. `a = 0.13 ms`, "filters dominate
oscillator count", "cushion is non-binding below the knee" — each is either a fact about
Surge on ARM or a fact about a Cortex-A72 at 1.8 GHz, and **today we cannot tell which.**

Re-running the same suite on a different microarchitecture disentangles them. That is a real
technological advancement, it is cheap if the suite is frozen first, and it is a much
stronger SR&ED position than either platform alone: **a finding replicated across two
architectures, or contradicted by one, is evidence of a kind a single board cannot produce.**

**So the governing rule for this whole transition is: replication before optimisation.**
Resist tuning the Pi 5 until the frozen suite has run on it unchanged.

---

## 1. Do this on the Pi 4 while you still have it in a known state

The Pi 4 is currently a calibrated instrument: known config, known floors, a harness that
works, and a governor whose behaviour is characterised. **The moment you start swapping
boards, cables, and OS images, that is gone.** Everything in this section is cheap and
becomes impossible later.

### 1.1 — Freeze a reference suite (**do this first, it gates the rest**)

Define one **golden measurement set** that can be re-run verbatim on any platform, and pin
exactly what it holds constant. Roughly:

| cell | why it is in the set |
|---|---|
| Crystals @ 3 | oscillator-bound, the original worst case |
| Cloud Horn @ 5 | oscillator-bound, mid-weight |
| Duduk @ 3 | **filter-bound** — the finding that oscillator count does not predict cost |
| Brave New World @ 3 | multi-oscillator, simple construction |
| silence @ 0 voices | isolates the fixed per-callback cost `a` |
| buffer ladder: 1024×3, 1024×2, 512×3, 512×2, 256×3 | the latency ladder itself |

Confirm harness only. Condition A, strict mode, governor off, `performance` at stock clock.
Record `dsp_med` **and** `dsp_p99`, xruns, and the achieved clock.

**Write it as one script** (`scripts/measure-reference-suite.sh`) that takes a platform label
and emits a single structured result file. If it needs edits to run on the Pi 5, it was not
frozen. Budget ~45–60 min per full pass.

**Run it on the Pi 4 at least twice**, on separate days, before anything changes. Two passes
give you the run-to-run noise floor — without which a Pi 5 "improvement" cannot be
distinguished from spread, and §5's predictions cannot be scored.

### 1.2 — V11 becomes the anchor, not just a task

V11 (512×2 and 256×3 at confirmed counts) already sits at the top of the queue. It now does
double duty: it establishes **the Pi 4's true latency floor**, which is the number the Pi 5
gets compared against. Without it the comparison is against 1024×2, a config you already know
is not the floor, and the gain gets overstated.

### 1.3 — P7's value has inverted: run it

**Correcting an earlier call.** With a Pi 5 ordered I said P7 (2000 MHz overclock diagnostic)
was not worth its reboot risk for ~11%. That was right about P7 *as a performance lever* and
wrong about **P7 as an instrument.**

P7 answers: *does DSP cost scale inversely with clock on this workload?* That is the
cheapest available forecast of what a 2.4 GHz A76 will actually give you. It converts the
Pi 5 from a hope into a prediction you can pre-register and score.

- Clock scales cleanly → expect the Pi 5's clock advantage to convert, and the A76's IPC gain
  on top. Predict and check.
- Clock does not move DSP → **the workload is bound by something other than instruction
  throughput** (memory, cache, dependency stalls). Then the Pi 5's *memory* advantage is the
  operative one, not its clock, and that reframes every optimisation afterwards.

Either result is worth ~13 minutes. **Run P7 before the board arrives.** Use the shortest
useful form from `REVIEW-line-of-thought-2026-08-22.md`: `dsp_med` primary, 25 s windows,
3 runs, Crystals @3 + Duduk @3.

### 1.4 — Reframe P8 from tweak to infrastructure

`-mcpu=cortex-a72` as a 5–15% lever is now marginal. But the **build-variant question is
suddenly real and unavoidable**: the Pi 5 needs `-mcpu=cortex-a76`, and a binary tuned for
one is wrong for the other.

So P8's deliverable changes. It is no longer "measure a flag." It is
**`scripts/build-surge.sh --arch {a72|a76|generic}`** — one parameterised build path, with the
measurement as a side effect. Do that work now, on the Pi 4, where the compare is against a
known baseline. It is the same effort and it survives the transition.

### 1.5 — Capture the Pi 4's full state as a restorable artifact

`scripts/backup-appliance-state.sh` exists. Run it, commit the output, and add anything it
misses: `/boot/firmware/config.txt`, kernel cmdline, all `CPUAffinity` values, `irqaffinity`,
governor, the exact Surge revision and build flags, JACK version, kernel version, USB audio
device and its URB configuration.

**This is the control condition.** Without it, "the Pi 5 is faster" is unfalsifiable.

### 1.6 — Run G3 (raw log archival) before the SD card matters less to you

OM-Repo [`sred/PROMPT-G3-archive-raw-logs.md`](../../../OM-Repo/internal/projects/mpe-synth-launch/sred/PROMPT-G3-archive-raw-logs.md). Once attention moves to the Pi 5, that card is a
decreasingly-cared-for object holding the only copy of every number in 74 documents. Pull the
logs while it is still the machine you are using.

### 1.7 — Do not decommission the Pi 4

Keep it assembled and bootable. It is the control, the fallback if the Pi 5 has an audio
regression, and the only way to re-run a cell when a Pi 5 result looks wrong. **Do not harvest
its SD card, PSU, or audio interface for the new board** — buy duplicates. A control you
cannibalised is not a control.

---

## 2. What actually changes on the Pi 5 — and what it invalidates

Assume nothing carries over until measured. Concretely:

| area | Pi 4 (BCM2711) | Pi 5 (BCM2712) | status of existing work |
|---|---|---|---|
| CPU | Cortex-A72, 4 × 1.8 GHz (`arm_boost`) | Cortex-A76, 4 × 2.4 GHz, wider/deeper OoO, bigger caches | Clock and IPC both move. **Every absolute number is void.** Core *count* is unchanged, so the pinning *strategy* survives; the values do not. |
| I/O topology | USB/Ethernet on-die; IRQ 30 = xhci (unmovable, CPU0), IRQ 41 = mmc0/mmc1 shared | **RP1 southbridge over PCIe** — USB, Ethernet, GPIO all move behind it | **The entire IRQ census is dead.** `irqaffinity=0,1`, the xhci-pinned-to-CPU0 constraint, the SD/WiFi IRQ-41 sharing — all must be re-derived from scratch. |
| Storage | SD card (shares IRQ 41 with SDIO WiFi) | SD **or NVMe over PCIe** | NVMe removes the SD/WiFi interaction *and* the G3 durability risk *and* cuts build times. **Strongly consider it.** But it is a second variable — see §4. |
| Thermals | Passive often adequate; 80/85 °C throttle | Runs materially hotter; **active cooling effectively mandatory** | Thermal behaviour must be re-characterised. The official active cooler is not optional for sustained DSP. |
| Power | 5 V/3 A; project has undervoltage history (GPIO jumpers, resolved) | **5 V/5 A PSU required** for full behaviour | Buy the official 27 W supply. Undervoltage has bitten this project before and it presents as unexplained throttling — i.e. it looks exactly like a measurement result. |
| Audio out | 3.5 mm analog + USB | **No analog jack** — USB/HDMI only | Already on USB audio, so **no impact**. One of the few things that transfers unchanged. |
| OS / kernel | Current image | Requires a newer image; different kernel, likely different JACK and Surge versions | **The biggest confound risk.** See §4.1. |
| Build flags | `-mcpu=cortex-a72` | `-mcpu=cortex-a76` | Handled by §1.4 |

**What does transfer, unchanged and valuable:** the measurement discipline (rules 0–7), the
confirm-vs-ramp harness distinction, the instrument self-test doctrine, the buffer arithmetic,
the knowledge that xruns here are graph overruns not underruns (a fact about the *stack*, not
the *board* — but **verify it, do not assume it**), the patch census, and the CPU subprocess
doctrine.

---

## 3. Structuring so nothing already built gets destroyed

The failure mode is editing Pi 4 findings in place until the record no longer says which board
produced which number. Three mechanisms:

### 3.1 — Platform-stamp everything, retroactively and going forward

**Right now only 5 of 23 live measurement documents name the platform**, and the "Standard
conditions" table in `docs/measurements/README.md` has no platform row. That is harmless with
one board and actively corrupting with two.

- Add **Platform** to the standard-conditions table (`Raspberry Pi 4B / BCM2711 / Cortex-A72
  @ 1800 MHz`).
- Add a one-line platform header to every live measurement doc. Mechanical, ~20 minutes.
- Archived docs: add the stamp to `archive/README` collectively rather than editing 53 files.
- **Every new doc names its platform in the first two lines.** No exceptions.

### 3.2 — Parameterise config; do not fork the repo

Do **not** create a `pi5` branch that drifts. Platform-specific values become profiles:

```
config/platform/pi4.env     # ARCH=cortex-a72  IRQ_AFFINITY=0,1  AUDIO_CPUS="2 3" CLOCK=1800
config/platform/pi5.env     # ARCH=cortex-a76  IRQ_AFFINITY=?    AUDIO_CPUS="?"   CLOCK=2400
```

with services and harnesses reading from the active profile. The Pi 5 values start as `?` and
get filled in by measurement — **which is the point.** The unknowns become visible and
enumerable instead of silently inherited.

### 3.3 — Results carry their platform in the data, not the filename

The reference suite (§1.1) emits a platform field in the result file itself. Filenames get
renamed and copied; embedded fields do not. This is what makes an automatic Pi 4 vs Pi 5
comparison table possible later instead of a manual reconciliation.

---

## 4. Controlling the confounds — the part most likely to go wrong

A platform swap changes a dozen variables at once. This project's whole discipline is
one-variable-at-a-time, and the transition is where that gets abandoned under excitement.

### 4.1 — Software version is the confound that will bite

If the Pi 5 runs a newer Surge, newer JACK, and a newer kernel, then "the Pi 5 is 2.3× faster"
measures **hardware + four software changes together** and cannot be decomposed.

**Mitigation, in order of preference:**
1. Build **the same Surge revision** on the Pi 5 that the Pi 4 runs (only `-mcpu` differs).
   This is why §1.4 matters.
2. If the OS forces newer JACK or kernel, **record it explicitly as a known confound** and say
   so in every conclusion. A stated confound is a limitation; an unstated one is an error.
3. Optionally, close the loop: build the *newer* Surge on the **Pi 4** and measure it there.
   That isolates the software delta on known hardware — and it is the kind of control step
   that distinguishes systematic investigation from an upgrade.

### 4.2 — Change one thing at a time, even during setup

Tempting to do NVMe + active cooling + new OS + overclock + new build flags in one afternoon,
then measure. **Don't.** Bring the Pi 5 up in the configuration closest to the Pi 4's — SD
card, stock clock, same Surge revision, same USB audio device, same patch library — run the
reference suite, *then* add improvements one at a time with a measurement between each.

The first Pi 5 number you want is the **like-for-like** one. Everything else is optimisation
on top of it, and optimisation you cannot attribute is not knowledge.

### 4.3 — Re-verify the instruments on the new platform before trusting any reading

Four times on the Pi 4 an instrument read clean while blind. A new kernel, a new JACK, and a
new IRQ topology are exactly the conditions that break instruments silently.

**Before the first real measurement**, confirm on the Pi 5: the xrun counter actually counts
(force an overrun and see it), strict mode engages, `mpe_meter_assert_live` behaves, the DSP
sampler reads the right thread, and `jack_get_xrun_delayed_usecs` still returns 0 (or
doesn't — which would itself be a finding worth having).

---

## 5. Pre-register the predictions — free, and the strongest artifact available

**Before the board is powered on**, write down what the Pi 4 model predicts for each cell in
the reference suite, with reasoning. Then score it.

Suggested form:

| quantity | Pi 4 measured | Pi 5 predicted | basis | Pi 5 actual |
|---|---|---|---|---|
| fixed per-callback cost `a` | 0.13 ms | ? | mostly clock-bound → ~0.10 ms at 2.4 GHz | |
| Crystals clean voices @ 512×2 | (V11) | ? | clock ratio 1.33× × A76 IPC ~1.3–1.6× → ~1.8–2.1× | |
| Duduk clean voices @ 512×2 | (V11) | ? | filter-bound; may scale differently — **P7 informs this** | |
| lowest clean buffer config | (V11) | ? | | |
| xrun class | 100% graph overrun | ? | expected unchanged — it is a stack property | |

**Why this is worth the twenty minutes it costs:**

- If predictions land, the Pi 4 cost model is **validated across microarchitectures** — a much
  stronger claim than it can earn on one board.
- If they miss, **that is the interesting result**: something is bound by a term the model does
  not contain, and you have found it cheaply instead of discovering it three optimisation
  rounds later.
- It is textbook systematic investigation — hypothesis stated before experiment, outcome
  scored honestly either way. It directly closes gap **G4** (uncertainties never stated up
  front) for the entire next phase, and it costs nothing.

Include the osc-bound vs filter-bound split. If Crystals scales with clock and Duduk does not,
those are two different constraints and the optimisation work afterwards forks accordingly.

---

## 6. After the like-for-like pass — how to attack the latency question

Only once the reference suite has run unchanged:

1. **Re-derive the ladder.** Lowest buffer × periods that holds 0 xruns at confirmed floors.
   This is the actual objective. Everything else is instrumental.
2. **Ask whether the binding constraint moved.** The Pi 4 is single-thread-compute-bound. If
   the Pi 5 clears the compute wall, the next binding term is likely **USB/RP1 transport or
   scheduling jitter** — a different problem needing different instruments, and the archived
   jitter work (`find-600us`, cyclictest harnesses) becomes relevant again rather than refuted.
   **Retired lines can un-retire when the platform changes. Check before assuming.**
3. **Re-derive the core allocation.** New IRQ topology. Redo the IRQ census; do not port
   `irqaffinity=0,1`. E1 (three cores) was refuted on the Pi 4 and should be **re-tested**, on
   one variable this time.
4. **Recalibrate the poly governor.** Thresholds are absolute DSP percentages tied to Pi 4
   costs. They will be wrong. This also finally lets the governor be re-enabled with headroom
   to spare.
5. **Revisit multithreading last.** `MULTITHREADING-ASSESSMENT` recommended against it partly
   because no clean set of free cores existed. If the Pi 5 delivers ~2× on one core, the
   multi-week C++ effort likely stays unjustified — **but the assessment should be re-scored,
   not assumed to hold.**

---

## 7. SR&ED threads this lands

- **U7 (is the platform compute-bound, which levers remain)** — currently open. P7 (§1.3) and
  the Pi 5 comparison close it properly, with evidence from two architectures.
- **New uncertainty U8 — cross-platform portability of the cost model.** Genuine, non-trivial,
  and not answerable by looking anything up: whether findings derived on one ARM
  microarchitecture hold on another for this specific audio graph. Frame it as such *now*,
  before the work, which is exactly what G4 asks for.
- **G2 (prior-art documentation)** — start the "prior art checked" paragraph with the Pi 5
  work. Pi 5 specifications are published and must be *cited, not measured*; what is not
  published is how this graph behaves on it.
- **G5 (eligible vs routine)** — board assembly, OS install, and cable management are routine.
  The measurement, prediction, and re-derivation are not. Mark the boundary as you go.

---

## 8. Ordered checklist

**Before the board arrives:**

1. V11 — Pi 4 latency floor *(queued, in progress)*
2. Freeze the reference suite; run it twice on the Pi 4 for a noise floor
3. P7 — clock-scaling diagnostic, ~13 min, now a Pi 5 forecast instrument
4. P8 reframed as `build-surge.sh --arch` *(build already in progress)*
5. Full Pi 4 state capture (§1.5)
6. G3 raw-log archival; G1 effort walkthrough
7. Platform-stamp the docs; create `config/platform/pi4.env`
8. **Write the predictions table (§5) and commit it**

**Buy alongside the board:** 27 W (5 A) official PSU, active cooler, separate SD card
(**do not reuse the Pi 4's**), and NVMe + HAT if going that route.

**On arrival:** like-for-like config → verify instruments (§4.3) → reference suite → score the
predictions → *then* optimise, one variable at a time.
