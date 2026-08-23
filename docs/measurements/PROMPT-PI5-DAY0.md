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

## 1a. Packages to install first (`sudo apt` is a Mitch-only gate per `AGENTS.md`)

**RT kernel: settled.** `apt-cache search` on the Pi 5 returns `linux-image-rpi-2712` and
`linux-image-rpi-2712-dbg` — **no `-rt` variant.** `linux-image-rpi-v8-rt` exists but is the Pi 4
(v8) kernel and will not boot a 2712. **PREEMPT_RT is a Pi 4-only lever.** Record this as measured,
not inherited — it is a real asymmetry in the platform comparison and must be stated in any Pi 4
vs Pi 5 result: the Pi 4 has a scheduling lever the Pi 5 does not.

### Tier 1 — needed today (build + smoke test)

**Script (on Pi):** `scripts/install-pi5-day0-tier1.sh` — debconf pre-seeds jackd2 RT=yes, installs
Tier 1 + `rt-tests`, adds user to `audio`, runs `scripts/verify-jack-rt-limits.sh`.

Manual equivalent:

```
sudo apt update
sudo apt install -y build-essential cmake git jackd2 alsa-utils
sudo apt install -y \
  libcairo2-dev libxkbcommon-x11-dev libxkbcommon-dev \
  libxcb-cursor-dev libxcb-keysyms1-dev libxcb-util-dev \
  libxrandr-dev libxinerama-dev libxcursor-dev \
  libasound2-dev libjack-jackd2-dev libfreetype6-dev libglu1-mesa-dev
```

That library list is `docs/SURGE_ARM_BUILD.md:157-169` — the same set `watch-build-a72.sh:90`
installs when cmake reports missing deps. Installing it up front avoids a cmake failure 10 minutes
into the build.

**`jackd2` asks whether to enable realtime priority. Answer YES.** It writes
`/etc/security/limits.d/audio.conf` and creates the `audio` group; **your user must be in
`audio`** (`sudo usermod -aG audio $USER`, then log out and back in). Under a non-interactive
install this prompt can be auto-answered *no*, and then **JACK runs without RT priority and every
latency number is quietly wrong** — the appliance still works, still makes sound, and reports
plausible figures. **Verify explicitly** rather than assuming: check `ulimit -r` is non-zero and
that `audio.conf` exists.

### Tier 2 — needed before any measurement

**`libjack-jackd2-dev` is in Tier 1 above and it is not optional** — `build-mpe-peak-meter.sh` and
`build-mpe-xrun-probe.sh` both hard-require it, and **those two binaries are the instruments.**
Without them the peak meter degrades and xrun counts come from nowhere. `MPE_PEAK_METER != 1` is
fatal in `measure-latency-run.sh` by design; **that guard exists precisely because a missing
instrument used to read as a clean result.** Build both explicitly before C0:

```
scripts/build-mpe-peak-meter.sh --required
scripts/build-mpe-xrun-probe.sh
```

`--required` makes a missing library exit 1 instead of skipping. Use it.

Also: `sudo apt install -y rt-tests` for `cyclictest` (`measure-cyclictest-floor.sh`) — cheap, and
a Pi 5 cyclictest floor is worth having as a scheduling baseline given there is no RT kernel.

### Tier 3 — NOT needed, skip for now

`python3-pygame`, the SDL libs, `i2c-tools`, the OLED/encoder stack, and `requirements.txt`. That
is the touch/patch-browser UI. **It is not on the latency path**, it adds background processes to
a box whose entire measurement is about scheduling, and installing it now would mean the Pi 5's
first numbers come from a busier machine than the Pi 4's control. Add it after the platform
comparison lands, not before.

### Memory / swap

`SURGE_ARM_BUILD.md` documents adding swap for the build on a 4 GB Pi 4. **Check `free -h` first.**
On an 8 GB Pi 5, `make -j4` should fit without swap and you do not want swap on an audio box —
if it is an 8 GB board, skip the swapfile. If 4 GB, either add swap for the build **and remove it
afterwards**, or build with `-j3`.

### Confirm before building

- `nproc` = 4, `free -h`, free disk (Surge build tree is multiple GB)
- `ulimit -r` non-zero and user in `audio`
- `git --version`, `cmake --version`

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

- **RT kernel availability: RESOLVED** — see §1a. No `linux-image-rpi-2712-rt`. Pi 4-only lever.
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
