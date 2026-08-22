# P8 — rebuild Surge with `-mcpu=cortex-a72` and measure

**Expected gain: 5-15%.** On the worst patch that is roughly 3 -> 4 voices. Worth doing,
not worth breaking anything for.

---

## HARD GATE — sequencing

This is **third in the queue**, not first. Ahead of it:

1. **P7 overclock diagnostic** (~15 min) — tells you whether the ceiling scales with clock
   at all, which is what makes a 5-15% compute win worth a 45-minute build. If DSP does not
   scale with clock, **this task may not be worth doing** — check the P7 result first.
2. **8 h instrument soak at 1024x2** — scheduled for tonight (Cloud Horn @ 5, condition A).

**Run NO command against `raspberrypi2` while the soak is in flight — including read-only
ones.** Not `ssh pi uptime`, not `cat /proc/cpuinfo`. Every SSH command forks processes and
adds scheduler load to a box whose entire measurement is about scheduling. An 8-hour soak
invalidated at hour 6 costs far more than you save.

`make -j4` takes all four cores for ~40 minutes. During a soak it would not perturb the
measurement, it would **end** it.

Phase A below is entirely offline — do it whenever. Start Phase B only when the Pi is
confirmed idle and `SENTINEL soak-complete` is in the soak log, checked **once**, not polled.

---

## Phase A — offline prep (do this now, no Pi contact)

**A1. Establish which Surge revision is installed.** From the repo, `docs/SURGE_ARM_BUILD.md`,
build notes, or any provenance stamp in the measurement logs already pulled. The rebuild must
be **the same source revision** as the running binary, with `-mcpu=cortex-a72` as the *only*
difference.

If you cannot establish the installed revision offline, **say so and stop** — do not guess a
tag. A rebuild from a different Surge version measures version + flag together and the number
is worthless. Resolving it is the first thing to do in Phase B, before building.

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
- Patches and counts: **Crystals @ 3** and **Cloud Horn @ 5** — both confirm-verified floors.
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
5. Build. ~40 min, all four cores, nothing else running.
6. **Thermals.** The build heats the SoC hard. Check `vcgencmd get_throttled` and let the
   board return to idle temperature **before measuring**. Measuring a hot Pi against a cold
   baseline invents a regression. `0x0` expected — anything else, report it and wait.
7. Install, verify the binary changed, measure per A4.
8. If the result is no-effect or negative, **revert** and say so plainly. A null result here
   is a real finding and closes a lever.

---

## Constraints

- **No subprocess churn in anything that runs during a measurement window.** Bash is fine;
  per-second forking in a polling loop is not.
- Report what you **eliminated**, not only what you found.
- If a reading could look identical whether the instrument is working or blind, fix the
  instrument before trusting the reading. That failure has now happened three times on this
  appliance (V8-b auto-pick, peak-meter shutdown, V10-b ramp probe).
- Do not bundle other changes into this branch. `-mcpu` alone.
