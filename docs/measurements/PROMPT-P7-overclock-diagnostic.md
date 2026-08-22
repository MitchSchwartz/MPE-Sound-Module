# P7 — 2000 MHz overclock, as a diagnostic

**This is not a shipping change.** It is a ~15-minute experiment that answers one question:

> **Does DSP cost scale inversely with clock?**

If yes, the appliance is cleanly compute-bound and every future percent of compute converts
to headroom 1:1 — which is what justifies spending 45 minutes on the `-mcpu=cortex-a72`
rebuild (P8) and what makes the multithreading estimate meaningful. If no, something other
than raw compute is binding, and **that is a far more interesting result than the 11%.**

Revert to stock when done. Shipping 2000 MHz is a separate decision gated on an enclosure
soak ending at `throttled=0x0`.

---

## State this must start from

- Pi idle. **No soak or measurement in flight.** An 8 h soak is scheduled for tonight — this
  must be finished and reverted before it starts.
- **Poly governor OFF** (it is, left off from V9). Keep it off. One variable.
- Buffer/periods: whatever is current. Hold identical across both halves.
- Confirm `scaling_governor=performance` and `arm_boost=1` are actually in effect — this is
  the 1800 MHz baseline, and it is already an overclock relative to the Pi 4 stock 1500.

---

## The change

`/boot/firmware/config.txt`. **Back it up first**, and know the recovery path before you
edit: if the Pi does not boot, the SD card must be edited from another machine. Confirm you
can do that before touching the file.

```
arm_freq=2000
```

`arm_boost=1` already asserts 1800; `arm_freq` overrides it. **Start with no `over_voltage`
change.** Many Pi 4 boards reach 2000 at stock voltage. Only if it is unstable, add
`over_voltage=2` and re-verify — voltage is where heat and long-term risk actually come from,
so do not raise it speculatively. Reboot required.

---

## Verify the clock is real before measuring anything

**This is the step that decides whether the result means anything.**

Setting `arm_freq=2000` does not mean the core runs at 2000. If the board throttles, it
silently runs slower, DSP shows no improvement, and the run reads as *"clock does not help"*
— a false negative that would kill the P8 and multithreading lines on bad evidence. This is
the same failure shape as the V8-b auto-pick and the V10-b ramp probe: **a broken
instrument that looks like a clean result.**

Before and after each measurement window:

- `vcgencmd measure_clock arm` — must read ~2000 MHz, not 1800, not 1500
- `cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq`
- `vcgencmd get_throttled` — must be `0x0`. Anything else invalidates the run
- `vcgencmd measure_temp` — record it; 80 C is soft throttle, 85 C hard

**Do not poll any of these during a measurement window** — each is a fork on a box being
measured for scheduling behaviour. Read them immediately before and immediately after.

If `get_throttled` is non-zero at any point, **report it and stop.** Do not average it away.

---

## Measurement

**Confirm harness only** — `measure-confirm-at-voices.sh` or `measure-latency-run.sh`.
**Never `measure-capacity-ramp.sh`** for a before/after comparison, even post-V10-b.

Patches and counts, all confirm-verified floors:

- **Crystals @ 3** — 3× Twist/Plaits, oscillator-dominated
- **Cloud Horn @ 5** — 2× String, oscillator-dominated
- **Duduk @ 3** — **1** unmuted Wavetable oscillator, filters 11/20: **filter-dominated**
  floor-3 class. The 53-patch census found `filter1 >= 10` on 12/53 (23%) versus any Twist
  on 2/53 (4%). Oscillator count does not predict cost on this library; a patch set that is
  entirely oscillator-dominated would not tell you whether filter-bound work responds to
  clock at all.

**Report the three patches separately, not as one averaged percentage.** If filter-bound
and oscillator-bound work scale together, the appliance is uniformly compute-bound and the
result generalises. **If they diverge, that is a more valuable finding than the 11%** — it
means one cost centre is bound by something other than clock, which changes what P8 and the
multithreading assessment are worth.

**Primary reading is `dsp_p99` and `dsp_max` at a fixed voice count** — continuous and
sensitive. Voice ceiling is quantised at these small integers: +11% will very likely not add
a whole voice at 3, and reading "still 3 voices" as "no effect" would be wrong.

**Baseline first, at 1800.** Do not reuse older numbers — they were taken under different
conditions. **Run the baseline at least 3 times** to establish run-to-run spread before
changing anything.

---

## Pre-register the prediction

Write this down **before rebooting**, and record whether it held:

- 1800 -> 2000 MHz is **+11.1% clock**
- If purely compute-bound, `dsp_p99` should fall by **~10%** on **each** patch (Crystals,
  Cloud Horn, Duduk) — compare against baseline spread, not against each other
- **Within noise** on all three (compare against the baseline spread measured above) -> clock
  is not the binding constraint; report it, because it changes the whole compute roadmap
- **Divergence across patches** (e.g. Crystals improves, Duduk flat) -> report the split;
  do not average it away
- **Falls by much more than 11%** on any patch -> something is wrong with the comparison,
  not a windfall

---

## Finish

1. Record results and the achieved clock alongside them — a number without its verified
   clock is not evidence.
2. **Revert `config.txt` to the backup and reboot.** Confirm 1800 and `throttled=0x0` before
   handing back. The Pi must be at stock for tonight's soak.
3. Write the finding to `docs/measurements/`, including the pre-registered prediction and
   whether it held.
4. Report what you **eliminated**, not only what you found. A null result here is a real
   finding: it closes the frequency lever and reprioritises P8.
