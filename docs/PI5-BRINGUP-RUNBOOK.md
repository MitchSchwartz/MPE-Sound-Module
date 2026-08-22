# Pi 5 bring-up runbook — setup, then an overnight suite with gates

**Companion to [`PI5-TRANSITION-PLAN.md`](PI5-TRANSITION-PLAN.md) (the *why*) and
[`measurements/PROMPT-PI4-CLOSEOUT.md`](measurements/PROMPT-PI4-CLOSEOUT.md) (what must exist
first).** This is the *how*, in order.

**Design goal: Mitch starts a suite in the evening and reads a report in the morning.** He is
woken only for a **major fork** — a result that changes what should be done next, not one that
is merely interesting. Everything else is recorded and continues.

---

## 0. Prerequisites — do not start without these

| | why |
|---|---|
| Pi 4 reference suite run **twice**, noise floor stated | Nothing is comparable without it |
| Pi 4 V11 floor established | The Pi 5 is measured against the real floor, not 1024×2 |
| Pi 4 full state capture committed | The control condition |
| Predictions table committed | Must precede the first boot to count as a prediction |
| `measure-reference-suite.sh` runs unmodified on a new platform | If it needs edits, it was not frozen |
| `build-surge.sh --arch a76` exists | Same Surge revision is the whole ballgame — see §3.1 |
| Pi 4 control binary decided (a72 or stock) and frozen | Pi 5 build must match its tuning posture |

**Do not cannibalise the Pi 4.** Separate SD card, separate PSU, separate everything. It is the
control and the fallback.

---

## 1. Hardware and OS

### 1.1 — Buy list

| item | note |
|---|---|
| **Official 27 W (5 V/5 A) USB-C PSU** | Not optional. This project has undervoltage history, and it presents as unexplained throttling — i.e. it looks exactly like a measurement result. |
| **Active cooler** (official cooler or equivalent) | Pi 5 runs materially hotter; sustained DSP without it will throttle |
| **New SD card** | Do not reuse the Pi 4's |
| **NVMe SSD + PCIe HAT** | Recommended — see §1.3 |

Reuse the **same USB audio interface** and the same patch library. Those must not change.

### 1.2 — OS: Raspberry Pi OS Lite 64-bit, Debian 13 (trixie)

**Yes, Lite is correct** — it matches the Pi 4 exactly (`docs/RESTORE.md`, `BUILD-FROM-ZERO.md`),
it is headless, and a desktop image adds background load that would confound every reading.

Match the Pi 4's OS release as closely as the imager allows. **Record the exact release, kernel
version, and JACK version on both boards** — §3.1 depends on it.

> **Known platform delta, found in `docs/LATENCY-SPIKE.md:194`:** the trixie archive ships
> `linux-image-rpi-v8-rt` (PREEMPT_RT) for the Pi 4's `v8` kernel, but **there is no
> `linux-image-rpi-2712-rt`** for the Pi 5. If RT ever becomes a lever, it is available on the
> Pi 4 and **not** on the Pi 5. Verify this is still true at bring-up rather than assuming —
> but do not let it block anything now; RT is currently retired.

### 1.3 — NVMe: buy it, but boot from SD first

**Do both, in this order.** Storage is not in the audio path — JACK touches no disk inside a
measurement window — so NVMe can only affect audio through IRQ/DMA contention, and the Pi 5's
IRQ topology is being re-derived from scratch regardless. So it is nearly free as a confound and
substantially valuable: it removes the SD/SDIO-WiFi interaction that IRQ 41 forced on the Pi 4,
it cuts the Surge build dramatically, and it ends the G3 durability problem.

But NVMe bring-up can consume a night — HAT compatibility, `dtparam=pciex1`, boot order, PCIe
gen 3 being out of spec. If it fails on day one you lose the overnight window *and* cannot tell
whether a bad result is the board or the disk.

**So:** SD card for Suite 1 (like-for-like). Then migrate to NVMe and **re-run the identical
suite as Suite 3.** The delta between them is a measured answer to "does storage affect audio
here" — a genuine finding for ~30 minutes, and it de-risks the migration.

---

## 2. Bring-up — like-for-like first

**Configure the Pi 5 as close to the Pi 4 as the hardware permits.** Resist every improvement
until Suite 1 has run.

1. Flash Lite 64-bit. Hostname distinct from `raspberrypi2` (e.g. `raspberrypi5`) — two boards
   on one network with one name is a genuinely dangerous ambiguity.
2. Install the appliance per `BUILD-FROM-ZERO.md`.
3. Build Surge with `build-surge.sh --arch a76` at **the same source revision the Pi 4 runs.**
   The Pi 4 control is arch-tuned too (closeout §A2), so this compares best-achievable to
   best-achievable rather than hardware-plus-a-flag. If closeout §A2 found a72 within noise and
   reverted to stock, build the Pi 5 **generic** as well — match the control, whatever it is,
   and state which in the result file.
4. Same USB audio device, same patch library, same JACK settings.
5. **Stock clock. Governor `performance`. Poly governor OFF.** No overclock, no core pinning
   yet, no `irqaffinity` — the Pi 4 values are meaningless here and guessing new ones before
   measuring is exactly the mistake E1 made.
6. Create `config/platform/pi5.env` with unknowns left as `?`. They get filled in by measurement.

---

## 3. Confounds to record at bring-up

### 3.1 — Software version is the confound that will ruin the comparison

If the Pi 5 runs newer Surge **and** newer JACK **and** a newer kernel, "the Pi 5 is 2.3× faster"
measures hardware plus three software changes and cannot be decomposed.

- **Same Surge revision** — non-negotiable, only `-mcpu` differs.
- If the OS forces a newer JACK or kernel, **record it as a stated confound in every
  conclusion.** A stated limitation is science; an unstated one is an error.
- Optional but strong: later, build the *newer* Surge on the **Pi 4** to isolate the software
  delta on known hardware.

### 3.2 — Instruments must be re-verified before anything is believed

New kernel, new JACK, new IRQ topology: exactly the conditions that break instruments silently.
This is **Suite 0** below, and it gates everything.

---

## 4. The overnight suites

Each suite is one script, runs unattended, writes a structured result file plus a plain-language
summary, and **halts on a defined fork** rather than continuing on a broken premise.

### Suite 0 — instrument self-test (~10 min) · **HARD GATE**

Same checks as Pi 4 closeout A0: force a deliberate overrun and confirm the counter moves;
strict mode engages per probe; `mpe_meter_assert_live` passes; DSP sampler reads the audio
thread. Additionally on this platform: confirm `jack_get_xrun_delayed_usecs` still returns 0
(if it now returns real magnitudes, that is a **finding** — record it, do not silently use it).

**If any check fails: stop the whole night and report.** A suite that completes on a blind
instrument is worse than one that halts — that failure has happened four times on the Pi 4.

### Suite 1 — reference suite, like-for-like (~30 min) · **the headline number**

`measure-reference-suite.sh --platform pi5`, unmodified. Same cells, same durations, same
harness as the Pi 4 passes.

**Run it twice** for the Pi 5's own noise floor (~60 min total). Compare against the Pi 4
noise floor before believing any delta.

**Auto-score the predictions table.** Per cell: predicted, actual, hit or miss. That scoring is
the most valuable artifact of the night and it is free.

**Fork conditions — wake Mitch only for these:**
- Suite 0 failed.
- The Pi 5 is **not faster**, or is slower, on any cell. Something is badly wrong (thermal,
  undervoltage, wrong build, wrong device) and continuing wastes the night.
- Predictions miss by more than ~2× in either direction — the cost model has a missing term and
  the rest of the plan needs rethinking.

Otherwise: record and continue.

### Suite 2 — the latency ladder (~45 min) · **the objective**

The actual goal. Walk down the buffer ladder at the Pi 4's confirmed floors and find the lowest
config holding **0 xruns**:

`512×2 (21.3 ms) → 256×3 (16.0 ms) → 256×2 (10.7 ms) → 128×3 (8.0 ms) → 128×2 (5.3 ms)`

25 s × 2 runs per cell, four patches, confirm harness. **Stop descending when a config fails and
record where.** Report the lowest clean config and the DSP headroom at it.

Then, at that config, **re-derive the voice floors** — how many voices each patch actually
sustains. That is the polyphony gain, and it is a separate number from the latency gain.

### Suite 3 — NVMe delta (~30 min, after migration)

Identical to Suite 1, booted from NVMe. Any difference is storage-attributable. Expect none;
finding one would be interesting and is worth the half hour.

### Suite 4 — thermal characterisation (~60 min, unattended)

Sustained load at the Suite 2 winner with `measure_temp` and `get_throttled` sampled at low
frequency — **not per second; the CPU subprocess doctrine applies here too.** Establish whether
the active cooler holds under real DSP load, and at what point throttling begins. The Pi 5 runs
hot enough that this is a real constraint, not a formality.

### Deferred until Mitch is present

- **IRQ census and core allocation.** Fully re-derive: `/proc/interrupts` under RP1, which lines
  are movable, where xhci lands. Then re-test core pinning **one variable at a time** — and
  re-test E1 (three cores), which was refuted on the Pi 4 and deserves a clean re-run here.
- **Overclock.** Not until stock is fully characterised.
- **Poly governor recalibration.** Thresholds are Pi 4-absolute and will be wrong.
- **Ear test.** Required before any config ships.
- **8 h soak** at the new default.

---

## 5. Morning report — what the suite must hand back

1. **Headline:** Pi 4 lowest clean config vs Pi 5 lowest clean config, in **milliseconds**.
   That is the project objective and it belongs in the first line.
2. **Predictions scorecard** — per cell, predicted vs actual, hit or miss, and for misses a
   one-line hypothesis about the missing term.
3. Reference suite comparison table, both platforms, with both noise floors stated.
4. New voice floors at the new best config.
5. Whether the binding constraint appears to have **moved** — if the Pi 5 clears the compute
   wall, the next term is likely USB/RP1 transport or scheduling jitter, and the archived
   jitter work (`find-600us`, cyclictest harnesses) **un-retires**. Retired lines were retired
   on a platform where compute bound first.
6. Thermal envelope and whether cooling held.
7. Everything that failed, was skipped, or looked wrong — including anything that halted a suite.

---

## 6. SR&ED capture, in-line

- State **U8 (cross-platform portability of the cost model)** as an uncertainty *before* the
  work, not after. The predictions table is that statement.
- Add the "prior art checked" paragraph to each suite: **Pi 5 specifications are published and
  must be cited, not measured.** What is not published is how this audio graph behaves on it.
- Mark board assembly, OS install, and cabling as **routine** (gap G5). Measurement, prediction,
  and re-derivation are not.
- Log hours as you go. Do not reconstruct twice.
