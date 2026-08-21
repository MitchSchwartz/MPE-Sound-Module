# Levers on the USB runway (2026-08-21)

Context: `docs/measurements/t11-condA-ladder-2026-08-21.md`. Condition A falls off a cliff
below 512 while **callbacks never miss their deadline** (917 us worst against 1333 us at 64
frames, with 6% of periods underrunning anyway). The drain is below JACK, in the USB
transport. This doc is the list of things that act on it, what they cost, and what to test.

## The hard floor

A full-speed device transmits **once per millisecond**. USB spec, not a tunable. The
transport has 1 ms granularity whatever we do, and a 64-frame period (1.33 ms) is at war
with it. No software change reaches this.

The xhci interrupt is **IRQ 30, pinned to CPU0** (3.19 M counts, zero elsewhere).
BRCM-PCI-MSI implements no `set_affinity`, so the *hardware* affinity is unmovable --
established previously and re-confirmed. See the `threadirqs` entry for why this is less
final than it sounds.

## Lever 1 -- `snd_usb_audio.lowlatency=N`

**Currently `lowlatency=Y`** (read from `/sys/module/snd_usb_audio/parameters/`).

Introduced in kernel 5.17 replacing `nrpacks`. It changes *when* URBs are submitted: with
`Y` the driver submits **on demand, close to when they are needed**, instead of keeping a
deep queue in flight. It was added to reduce latency, and it does so by **deliberately not
using the runway the buffer already provides**.

Total buffer sets an upper bound on runway. `lowlatency=Y` means we are not filling it.

- **Cost:** the deeper queue adds real latency -- the write pointer runs further ahead. The
  net win is smaller than buffer arithmetic suggests. **Measure end to end, do not assume.**
- **Risk:** low. This is the pre-5.17 behaviour, a decade in production.
- **Blast radius:** all USB audio. The APC MINI is MIDI (different path) and the UAC2
  gadget uses a different driver, so nothing else on this appliance is touched.
- **Confidence:** semantics not verified against this kernel's source. It is a modprobe
  line and a 15 min rerun -- settle it by measuring, not by arguing.

**Not fundamentally better engineering. It is a dial.** `Y` is correct with runway to
spare; `N` is correct when starved. The actual improvement is recognising it should be
**chosen per device** -- which puts it in `detect-audio-device.sh` beside the tier logic.
Full-speed dongle likely `N`, high-speed interface likely `Y`.

## Lever 2 -- `threadirqs`

**Not in cmdline.** Some drivers request threaded handlers anyway (`irq/27-aerdrv`,
`irq/41-mmc0`, the vc4 set, all at FIFO 50) but **there is no `irq/30-xhci`** -- the USB
handler runs in hard interrupt context on CPU0, competing with everything else there.

Booting with `threadirqs` makes it a schedulable thread.

**The unlock is placement, not priority.** IRQ 30's *hardware* affinity is refused by
BRCM-PCI-MSI. A *thread's* affinity is ordinary scheduling. `threadirqs` converts an
unmovable hardware constraint into a placeable, prioritisable, measurable one. That is a
bigger deal than the priority setting.

- **Expected size:** cyclictest floor is 209-320 us, so perfect scheduling still leaves
  ~300 us. Going ~900 -> ~300 roughly triples effective headroom at small buffers. Might
  move the cliff 512 -> 256. **Will not reach 64.**
- **Risk: real.** Global flag -- every threaded handler gains a context switch, not just
  xhci. Applied naively it can regress; network throughput regressions are a known effect.
  Needs a companion step setting priorities on the handlers that matter. Existing threaded
  handlers are at FIFO 50 and jackd is at 70, so the hierarchy is already sane.
- Requires a reboot. Standard practice in Linux audio distros.

**This one is fundamentally better engineering, independent of the numbers.** It converts
unbounded hard-IRQ time into work that can be scheduled, prioritised, placed on a core and
measured. Even a modest win is worth having, because afterwards a currently-opaque term
becomes one we can reason about.

## Lever 3 -- kill the WiFi interrupt load. **The real "disable ethernet" for a Pi 4.**

The linuxaudio wiki's `smsc95xx` advice does **not** apply: it targeted Pi 1-3, where
ethernet was a USB device stealing bus bandwidth. Verified on this Pi:

- `eth0` is `fd580000.ethernet` -- **GENET, a platform device, not on the USB bus at all**
  (`lsusb -t` shows only the two audio devices). Currently **DOWN, no carrier**, 51,993
  interrupts since boot. Essentially free.
- `wlan0` is on `fe300000.mmcnr` -- **SDIO WiFi, sharing IRQ 41 with the SD card
  controller: 1,681,540 interrupts, all on CPU0.**

**CPU0 is servicing 1.68 M SDIO interrupts alongside 3.19 M xhci interrupts.** The same
core that has to service USB audio promptly is also handling every WiFi and SD-card
interrupt on the machine. That is the modern form of the wiki's advice -- different
mechanism (IRQ contention, not bus bandwidth), same shape of fix.

**Test: plug in ethernet, bring `wlan0` down, re-run.** eth0 is on its own platform IRQ and
~30x cheaper. This also removes a standing confound -- **every measurement to date has been
taken over SSH across the WiFi link that is generating those interrupts.**

- **Cost:** needs a cable. For a stage appliance, arguably WiFi should be down anyway.
- **Risk:** low, and reversible in one command.
- **Fundamentally better engineering: yes,** for an appliance whose job is meeting
  deadlines. Do not run an avoidable interrupt source on the core servicing audio.

## Also live, and not what anyone thinks

Cmdline still carries **`irqaffinity=0,1`** -- E1's change, still in place after the
`CPUAffinity` half was reverted. It is arguably correct (IRQs on 0/1, audio on 2/3) and
complementary rather than contradictory, but **the current config is not the pre-E1
baseline.** Know this before the next comparison.

## Test matrix -- 2 tests, not 4

A full device x setting grid is not warranted.

| test | device | why |
|---|---|---|
| **L1 -- `lowlatency=N`** | **dongle** | The dongle is runway-starved; this is where the parameter bites. Decides whether 256 is reachable on cheap hardware -- i.e. whether the Scarlett is *necessary* or merely *better*. A product decision, not just a tuning one. |
| **L2 -- WiFi down, ethernet up** | **dongle** | Removes 1.68 M interrupts from CPU0 *and* a standing confound. Cheapest of the three. |
| L3 -- `threadirqs` | **whichever device ships** | Device-independent in mechanism but the win size differs. Defer until the device is settled; no point tuning hardware that may be replaced. |
| -- | Scarlett | **Baseline at defaults first.** Only tune if it does not already clear the bar. If the Scarlett needs `lowlatency=N` too, that says something worrying about the Pi rather than about the device. |

**One variable per test.** E1 is the cautionary tale -- two variables at once, every
condition regressed, nothing learned about either.

**Run order:** L2 (free, removes a confound) -> L1 -> Scarlett baseline -> L3 on the winner.
