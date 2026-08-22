# Pi 4 closeout — ordered test plan before the Pi 5 arrives

**Purpose.** Leave the Pi 4 as a *calibrated control*: a frozen reference suite, a known
latency floor, a clock-scaling forecast, and a restorable state capture. Everything the Pi 5
gets compared against is produced here. Context: [`../PI5-TRANSITION-PLAN.md`](../PI5-TRANSITION-PLAN.md).

**Platform:** Raspberry Pi 4B / BCM2711 / Cortex-A72 @ 1800 MHz (`arm_boost=1`), Raspberry Pi
OS Lite 64-bit, Debian 13 (trixie).

**Design principle: Mitch's input is a scarce resource.** Track A below is fully autonomous —
no reboots, no config.txt edits, no ear tests. Track B needs him reachable, and is batched
into **one** window so he is interrupted once, not five times.

---

## Track A — fully autonomous (no reboot, no gate)

Run in this order. Each step hands back a one-paragraph result. **Stop and report** on any
instrument failure rather than continuing — a suite that runs to completion on a blind
instrument is worse than one that halts.

### A0. Instrument pre-flight (~5 min) — **gates everything below**

Before any measurement, prove the instruments are live:

- Force a deliberate overrun (any patch well above its floor, 8 s) and confirm the xrun
  counter **moves**. A zero here is the failure mode that has bitten this project four times.
- Confirm strict mode engages (`MPE_JACK_SOFTMODE=0`) and stays engaged per probe.
- Confirm `mpe_meter_assert_live` passes and the peak meter is running.
- Confirm the DSP sampler reads the audio thread.

**If any check fails, fix it and re-run A0 before proceeding.** Report what you checked.

### A1. V11 — the latency floor (~15 min)

`PROMPT-V11-512-256-confirm.md`, already queued and in progress. 512×2 and 256×3 at confirmed
counts (Crystals 3, Cloud Horn 5, Duduk 3, Brave New World 3), confirm harness, governor off,
stock 1800.

**This is the single most important number in the closeout.** It defines the Pi 4's real
latency floor, which is what the Pi 5 gets measured against. Comparing a Pi 5 to 1024×2 — a
config already known not to be the floor — would overstate the gain.

### A2. Freeze the reference suite (~30 min to write, ~30 min to run)

Write `scripts/measure-reference-suite.sh`. **It must run unmodified on a Pi 5.** No
hardcoded core lists, clocks, IRQ numbers, or `-mcpu` values — read them from a platform
profile or detect them.

**Contract:**
- Takes a platform label; emits **one structured result file** (JSON or TSV) with the platform,
  kernel, Surge revision, JACK version, governor, clock, and every cell's result.
- **Platform is a field in the data, not just the filename.** Files get renamed; fields don't.
- Confirm harness only. Condition A, strict mode, governor off.

**Cells — keep it this tight:**

| block | cells | time |
|---|---|---|
| Silence @ 0 voices | 1024, 512, 256 × 25 s × 2 runs | ~4 min |
| Four patches at confirmed floors | Crystals 3, Cloud Horn 5, Duduk 3, Brave New World 3 × {1024×2, 512×2, 256×3} × 25 s × 2 runs | ~20 min |

Record `dsp_med` (primary), `dsp_p99`, `dsp_max`, xruns, and achieved clock per cell. Total
~25–30 min per pass including jackd restarts.

Rationale for the cell choice: silence isolates the fixed cost `a`; Crystals and Cloud Horn
are oscillator-bound; **Duduk is filter-bound** — the finding that oscillator count does not
predict cost — and Brave New World is multi-oscillator but simply constructed. Three buffer
configs span the ladder without re-measuring what V9 already settled.

### A3. Run the reference suite twice, on separate days (~30 min each)

Two passes give the **run-to-run noise floor**. Without it, a Pi 5 "improvement" cannot be
distinguished from spread and the §5 predictions in the transition plan cannot be scored.

**Report the spread explicitly.** If cells disagree by more than a few percent between passes,
that number is the threshold below which no future result means anything — say so.

### A4. Full state capture (~10 min)

Run `scripts/backup-appliance-state.sh`, commit the output, and add anything it misses:
`/boot/firmware/config.txt`, kernel cmdline, every `CPUAffinity` value, `irqaffinity`,
governor, **exact Surge revision and build flags**, JACK version, kernel version, USB audio
device and its URB configuration.

This is the control condition. Without it, "the Pi 5 is faster" is unfalsifiable.

### A5. G3 — archive the raw logs (~30 min)

`PROMPT-G3-archive-raw-logs.md`. Do it now, while the Pi 4's SD card is still the machine you
care about. Once attention moves to the Pi 5 it becomes a neglected object holding the only
copy of every number in 74 documents.

### A6. P8 reframed — parameterised build (~1 h build, offline)

`PROMPT-P8-mcpu-cortex-a72.md`, with the deliverable changed: not "measure a flag" but
**`scripts/build-surge.sh --arch {a72|a76|generic}`**, one build path, measurement as a side
effect. The Pi 5 needs `-mcpu=cortex-a76`, so this infrastructure is now required regardless
of whether the flag helps. Build only — **do not install unattended.**

### A7. Platform-stamp the docs (~20 min, offline)

Add a platform line to the first two lines of every live measurement doc. Add the stamp to
`archive/README` collectively rather than editing 53 files. The standard-conditions table in
`docs/measurements/README.md` already has its Platform row.

### A8. Write the predictions table (~20 min, offline — **do before the Pi 5 boots**)

Per §5 of the transition plan: for every reference-suite cell, what the Pi 4 model predicts for
the Pi 5, with the reasoning, committed **before** the board is powered on. Include the
oscillator-bound vs filter-bound split — if Crystals scales with clock and Duduk does not,
those are different constraints and the optimisation work forks.

---

## Track B — needs Mitch reachable (batch into ONE window, ~45 min)

These require reboots. A failed `config.txt` edit means pulling the SD card and editing it on
another machine, which needs someone in the room. **Do not run any of this unattended.**

### B1. P7 — clock-scaling diagnostic (~13 min)

`PROMPT-P7-overclock-diagnostic.md`, shortest useful form: **`dsp_med` primary**, 25 s windows,
3 runs, **Crystals @3 and Duduk @3 only**.

**This is now a forecast instrument, not a performance lever** — it predicts whether the Pi 5's
2.4 GHz will convert. If DSP scales with clock, expect the clock advantage plus A76 IPC on top.
If it does not, the workload is bound by memory/cache/dependency stalls and the **Pi 5's memory
advantage is the operative one** — which changes what to optimise first. Either answer is worth
13 minutes.

Back up `config.txt` and confirm the SD-card recovery path **before** editing. `arm_freq=2000`,
no `over_voltage` initially. Verify achieved clock and `get_throttled == 0x0` before and after
each window, never during. Board must be **cool** — do not run this after a build.

**Revert to stock 1800 and reboot before anything else.**

### B2. Overnight soak at the V11 winner (8 h)

Whatever V11 leaves as the best clean config. Gate 1 (shipping default) depends on it. Start it
and leave — but **nothing else may touch the Pi while it runs.**

### B3. Ear test (~10 min, Mitch only)

Confirm the instrument still sounds correct at the new buffer config before it becomes the
shipping default. Numbers improving is necessary, not sufficient.

---

## Explicitly deferred

- **Multithreading.** Re-score after the Pi 5 baseline, do not start.
- **Governor fade and re-enable.** Thresholds are Pi 4-absolute and will be wrong on the Pi 5;
  recalibrate there rather than twice.
- **Percussive rate metric.** Gate 3, still deferred.

---

## Hand back after Track A

The reference suite result files (both passes) with the noise floor stated, the V11 floor, the
state capture path, the log archive manifest, the predictions table, and a list of anything
that failed or was skipped and why.
