# P8 — rebuild Surge with `-mcpu=cortex-a72` and measure

**Expected gain: 5-15%.** On the worst patch that is roughly 3 -> 4 voices. Worth doing,
not worth breaking anything for.

---

## HARD GATE — sequencing

This is **third in the queue**, not first. Ahead of it:

1. **P7 overclock diagnostic** (~15 min) — tells you whether the ceiling scales with clock
   at all, which is what makes a 5-15% compute win worth a 45-minute build. If DSP does not
   scale with clock, **this task may not be worth doing** — check the P7 result first.
2. **8 h instrument soak at 1024x2** — after P7 revert to stock 1800. **Do not run P7 while soak is in flight.**

**Run NO command against `raspberrypi2` while the soak is in flight — including read-only
ones.** Not `ssh pi uptime`, not `cat /proc/cpuinfo`. Every SSH command forks processes and
adds scheduler load to a box whose entire measurement is about scheduling. An 8-hour soak
invalidated at hour 6 costs far more than you save.

`make -j4` takes all four cores for ~40 minutes. During a soak it would not perturb the
measurement, it would **end** it.

Phase A below is entirely offline — do it whenever. Start Phase B only when the Pi is
confirmed idle and `SENTINEL soak-complete` is in the soak log, checked **once**, not polled.

---

## RUN NOW — build-only pass (unattended, ~1 h, 2026-08-22)

**Mitch is out for about an hour. The Pi is idle. Use it to compile — nothing else.**

This is not out of order. The A1b gate below governs whether to **install and measure**, not
whether to **compile**. A build is reversible and costs only disk and otherwise-idle cores.
If P7 later shows clock does not move DSP, the artifact gets thrown away and no attention
was spent on it.

**Do, in this order:**

1. **A1 — establish the installed Surge revision.** The Pi is idle, so query it directly.
   **If you cannot establish it, stop and report — do not guess a tag.** A build from a
   different revision measures version + flag together and is worthless. This gates
   everything else in this pass.
2. **Check free disk.** A Surge build tree is multiple GB on the SD card.
3. A2 build script, A3 revert path, A4 measurement plan, A5 pre-registration.
4. **Run the build.** ~40 min, all four cores.

**Do NOT, while unattended:**

- **Do not install the new binary.** Building and installing are separate steps and only
  the build is safe to do unsupervised.
- **Do not measure anything.** Baseline and after-measurement both belong in Phase B.
- **Do not run P7.** It needs all-cores-quiet, and it needs a *cool* board — see below.
- **Do not edit `config.txt` and do not reboot.** If the Pi does not come back up, recovery
  means physically pulling the SD card and editing it on another machine. That requires
  someone in the room. This is the one genuinely hard-to-reverse action in the queue.

**Thermal interaction — this is the part that is easy to get wrong.** The build heats the
SoC hard for 40 minutes. Running P7 during or shortly after means measuring 2000 MHz on a
hot board: it throttles, DSP shows no gain, and the run reads as *"clock does not help"* —
the exact false negative P7 exists to avoid. **Leave the board to cool. Record
`vcgencmd measure_temp` and `get_throttled` when the build finishes so the next task knows
what state it inherited.**

**Hand back:** the revision you built, whether the build succeeded, free disk before/after,
final temperature and `get_throttled`, and the path to the uninstalled artifact. Then stop.

---

## Phase A — offline prep (do this now, no Pi contact)

**A1. Establish which Surge revision is installed.** From the repo, `docs/SURGE_ARM_BUILD.md`,
build notes, or any provenance stamp in the measurement logs already pulled. The rebuild must
be **the same source revision** as the running binary, with `-mcpu=cortex-a72` as the *only*
difference.

If you cannot establish the installed revision offline, **say so and stop** — do not guess a
tag. A rebuild from a different Surge version measures version + flag together and the number
is worthless. Resolving it is the first thing to do in Phase B, before building.

**A1b. Read the P7 result first and decide whether this is still worth doing.**
P7 measures whether DSP cost scales inversely with clock, on both an oscillator-dominated
patch and a filter-dominated one.

- Both scale ~10% with a +11% clock -> uniformly compute-bound. Proceed; a 5-15% compute win
  converts to headroom 1:1.
- Neither scales -> **clock is not the binding constraint. Say so and stop.** A compiler flag
  is a smaller version of the same lever P7 just showed does not move. Report it rather than
  spending 45 minutes confirming it.
- **They diverge** (osc-bound scales, filter-bound does not, or vice versa) -> proceed, but
  this is the most interesting case: the two cost centres have different constraints, and P8
  should report them separately rather than as one aggregate percentage.

**A2. Write the build script** (`scripts/build-surge-a72.sh`), based on
`docs/SURGE_ARM_BUILD.md:45-105`. Current build is `-DCMAKE_BUILD_TYPE=Release` and nothing
else — no `-mcpu`. Add exactly one thing:

```
-DCMAKE_CXX_FLAGS="-mcpu=cortex-a72" -DCMAKE_C_FLAGS="-mcpu=cortex-a72"
```

Keep every existing flag. Use the reduced target (`-DSURGE_BUILD_TESTRUNNER=FALSE`,
`make surge-xt-standalone`) to save build time. Script must be idempotent and must not
install on its own — building and installing are separate steps.

**A3. Write the revert path.** This is the **first change to the audio binary** in this
entire arc; everything before it has been config. Before install, copy the running binary to
`~/surge-xt-cli.pre-a72` and record its checksum. A one-line documented rollback must exist
before the new binary is in place.

**A4. Write the measurement plan** into the script, not into your head:

- **Confirm harness only** — `measure-confirm-at-voices.sh` or `measure-latency-run.sh`.
  **Never `measure-capacity-ramp.sh`**, even post-V10-b, for a before/after comparison.
- Patches and counts, all confirm-verified, chosen to span both cost centres:
  - **Crystals @ 3** — 3x Twist/Plaits, oscillator-dominated
  - **Cloud Horn @ 5** — 2x String, oscillator-dominated
  - **Duduk @ 3** — **1** unmuted Wavetable oscillator, filters 11/20: **filter-dominated**
  The 53-patch census found `filter1 >= 10` on 12/53 patches (23%) versus any Twist on 2/53
  (4%). Expensive filters are ~5x more common than exotic oscillators, and Duduk is floor-3
  class on a single oscillator — **oscillator count does not predict cost on this library.**
  A patch set that is entirely oscillator-dominated would not generalise.
- **Report the three patches separately, not as one averaged percentage.** `-mcpu` tunes
  scheduling and instruction selection; there is no reason oscillator inner loops and filter
  inner loops must benefit equally. A split result is a finding, not noise.
- Same buffer/periods for before and after. Use whatever the soak establishes as default.
- **Hold clock constant at stock 1800 MHz for both halves.** The 2000 MHz test is a separate
  experiment; do not let the two overlap. One variable.
- Record `dsp_p99`, `dsp_max`, and xrun counts. The gain shows up as **headroom at a fixed
  voice count**, which is the sensitive reading. Re-deriving the ceiling is a second,
  optional step, not the primary measurement.

**A5. Pre-register the result.** Write down, before building, what counts as a win and what
counts as no-effect — including the noise floor from repeated runs of the same config. A
3% difference against 5% run-to-run spread is not a result.

---

## Phase B — after `SENTINEL soak-complete` only

1. Confirm the soak actually completed and record its outcome. If it aborted, **stop** —
   Gate 1 is unresolved and that matters more than this.
2. Resolve A1 if it is still open.
3. Baseline measurement on the **current** binary, per A4. Do this even if older numbers
   exist — they were taken under different conditions.
4. Back up the binary (A3).
5. **Check free disk before building.** A Surge build tree is multiple GB. Running the SD
   card to full mid-build is a bad failure on the box that also holds the audio binary — and
   IRQ 41 is shared between the SD card and SDIO WiFi, so heavy writes are not free.
6. Build. ~40 min, all four cores, nothing else running.
7. **Thermals.** The build heats the SoC hard. Check `vcgencmd get_throttled` and let the
   board return to idle temperature **before measuring**. Measuring a hot Pi against a cold
   baseline invents a regression. `0x0` expected — anything else, report it and wait.
8. Install, verify the binary changed, measure per A4.
9. If the result is no-effect or negative, **revert** and say so plainly. A null result here
   is a real finding and closes a lever.
10. **Ear test before keeping it.** This is the first binary change in the arc. Numbers
   improving is necessary but not sufficient — confirm the instrument still sounds correct
   at the patches above before the new binary stays installed.

---

## Constraints

- **No subprocess churn in anything that runs during a measurement window.** Bash is fine;
  per-second forking in a polling loop is not.
- Report what you **eliminated**, not only what you found.
- If a reading could look identical whether the instrument is working or blind, fix the
  instrument before trusting the reading. That failure has now happened three times on this
  appliance (V8-b auto-pick, peak-meter shutdown, V10-b ramp probe).
- Do not bundle other changes into this branch. `-mcpu` alone.
- **`-mcpu=cortex-a72` is correct for this Pi 4 and wrong for a Pi 5** (Cortex-A76). If the
  binary is ever meant to be portable across appliance hardware, that is a build-variant
  decision to record now, not a flag to set once and forget.
- Keep `~/surge-xt-cli.pre-a72` until the change has survived a soak. Do not clean it up as
  tidiness.
