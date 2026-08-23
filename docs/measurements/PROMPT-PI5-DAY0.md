# Pi 5 — day 0, while the Pi 4 is busy

**Context.** Pi 5 is up with the Sound Blaster attached. The Pi 4 is mid-queue (G2, then V12) and
**must not be touched** — no commands against `raspberrypi2`, including read-only.

**Rule for today: nothing that produces a number to compare.** Suite 1 requires the same Surge
revision and a C0-conformant instrument set; measuring a half-configured board produces a figure
that will be wrong and will anchor expectations. Everything below is either **setup** or **free
platform fact-gathering.**

---

## 1. Start the Surge build first — it is the long pole (~20–30 min, unattended)

**Do this before anything else so it compiles while you do the rest.**

```
scripts/build-surge.sh --arch a76
```

**Source revision must be `253f8d86`** — the same commit the Pi 4 control runs. This is the whole
basis of the platform comparison: **hardware must be the only difference.** The script asserts the
checkout; if it cannot reach that commit, **stop and report** rather than building HEAD.

**Do not install it yet.** Build only.

**The build doubles as a free thermal and PSU stress test** — four cores at 100% for ~25 minutes
is the sustained load you need anyway. Sample every 30 s and record: `vcgencmd measure_temp`,
`vcgencmd get_throttled`, `vcgencmd measure_clock arm`.

**`get_throttled` must be `0x0` throughout.** Anything else means the cooler or the PSU is
inadequate, and **on this project undervoltage has historically presented as unexplained
throttling that reads like a measurement result.** Catch it now, before it contaminates Suite 1.

**Confirm before starting:** official 27 W (5 A) PSU, and an active cooler fitted. If either is
missing, say so — do not run a 25-minute all-core build on an unknown supply.

---

## 2. Platform state capture — the mirror of A5

Same shape as the Pi 4 control capture, so the two are diffable:

- OS release, kernel version, architecture
- JACK version, ALSA version, `jackd` build
- **Sound Blaster**: `lsusb -t`, `aplay -l`, **resolved card index** (it moves between boots —
  never hardcode it), supported rates and buffer sizes, USB speed and which controller it landed on
- `/boot/firmware/config.txt`, kernel cmdline
- CPU governor, available governors, `arm_freq`, idle and loaded clocks
- Memory, storage (SD or NVMe), free disk
- Any appliance services already installed

Commit it as `appliance-state/pi5-<date>/`, matching the Pi 4 layout.

---

## 3. IRQ census from scratch — genuinely new knowledge

**The Pi 4 census is entirely void here.** RP1 moves USB, Ethernet and GPIO behind a PCIe-attached
southbridge, so `irqaffinity=0,1`, the xhci-pinned-to-CPU0 constraint, and the SD/SDIO IRQ 41
sharing are all Pi 4 facts.

Capture, at idle and again during the build:

- Full `/proc/interrupts`
- **Which IRQ serves the USB controller the Sound Blaster is on**, and whether its
  `effective_affinity` is writable — the Pi 4's xhci was pinned to CPU0 and unmovable; **is it
  here?** That single fact determines whether core allocation is even available as a lever.
- Which lines are movable vs fixed
- Whether storage and network still share a line

**Do not change any affinity.** Census only. Guessing new values before measuring is the mistake
E1 made.

---

## 4. Free checks worth having on record

- **RT kernel availability.** `docs/LATENCY-SPIKE.md:194` records that trixie ships
  `linux-image-rpi-v8-rt` for the Pi 4 but **no `linux-image-rpi-2712-rt`**. Verify with
  `apt-cache search linux-image-rpi` — one command. If still absent, RT is a Pi 4-only lever and
  that asymmetry should be recorded before anyone assumes otherwise.
- **Smoke test only:** does `jackd` start against the Sound Blaster at `1024x3`, and does it stay
  up for 60 s? This is *"is the platform viable"*, **not** a measurement — report up/down and any
  errors, **no xrun counts, no DSP figures, no comparisons.**
- Prior-art paragraph (gap G2): note explicitly that **Pi 5 specifications are published and are
  being cited, not measured.** What is not published is how this audio graph behaves on it.

---

## 5. Do NOT do today

- **No reference suite, no Suite 1.** Requires same-revision Surge installed and C0 passing.
- **No tuning of any kind** — no `irqaffinity`, no `CPUAffinity`, no overclock, no governor
  changes, no NVMe migration. Bring-up is like-for-like; optimisation comes after the first
  comparable number.
- **No audio measurements**, and no numbers that could be read as a Pi 4 comparison.
- **Nothing against `raspberrypi2`.**
- **Do not edit the predictions table** (`pi5-predictions-2026-08-23.md`). It is pre-registered.

---

## 6. Hand back

Build outcome and the revision built; the thermal/throttle series across the build; PSU and
cooler confirmation; the platform state capture path; the IRQ census with **an explicit answer on
whether the Sound Blaster's USB IRQ is movable**; RT kernel availability; jackd smoke-test result;
and anything that looked different from the Pi 4 in a way that will matter later.

**Then stop.** Suite 1 needs C0 green on this board first, and C0 needs the binary installed.
