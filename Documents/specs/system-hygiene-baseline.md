# System hygiene baseline — go wide before going deep (2026-08-21)

**Why this exists.** Two days of measurement went depth-first on a machine nobody had
surveyed. A three-minute wide pass found a service crash-looping 617 times, a 3D graphics
driver generating 857 k interrupts on a headless appliance, and seven maintenance timers
armed on a realtime audio box. **None of this was known while every latency conclusion was
being drawn.** This doc is the survey, the prune list, and the rule that comes out of it.

**Standing rule (new).** Before measuring a system, enumerate it. Services, timers,
interrupts, modules. A wide pass is minutes; a wrong conclusion from a dirty baseline costs
days.

---

## Finding 1 — `mpe-pressure-remap` has restarted 617 times

**192 restarts in the last hour alone**, continuously, through every measurement taken
today.

```
Error: no physical MIDI inputs opened
mpe-pressure-remap.service: Main process exited, code=exited, status=1/FAILURE
Scheduled restart job, restart counter is at 617.
```

Root cause: the Roli is not connected. The unit waits 15 s for it, gives up, exits 1, and
`Restart=on-failure` restarts it. Forever.

**Cost per cycle:** ~1.19 s CPU (systemd's own accounting), a process spawn, journald
writes, and — most relevant — **MIDI device enumeration, which issues USB control transfers
on the same bus as the audio device.** Every 19 seconds. Roughly 6% of a core continuously,
plus a direct interference path into the transport under investigation.

**Fix:** the unit should not run when its hardware is absent. Either gate it on device
presence (udev, or a `ConditionPathExists` on the MIDI node) or set `Restart=no` with the
udev rule bringing it up on plug. **A service that cannot succeed must not retry forever** —
this is the systemd-level form of the no-forks-in-periodic-loops doctrine.

Also flagged: **`mpe-peak-meter.service` NRestarts=16.** That is the meter the entire
measurement rig trusts. Not a loop, but not clean either. Investigate.

## Finding 2 — seven maintenance timers armed on a realtime appliance

`apt-daily`, `apt-daily-upgrade`, `dpkg-db-backup`, `logrotate`, `man-db`, `e2scrub_all`,
`fstrim`, `systemd-tmpfiles-clean`, `rpi-zram-writeback`.

`apt-daily-upgrade`, `dpkg-db-backup`, `logrotate` and `e2scrub_all` all fired at
**10:03:39 today**. T11's 256x3 cell started at **10:08:06** — four minutes later, so this
particular burst is *not* the explanation for T11's 12.10 vs T13's 1.53. **But it is luck,
not design.** `apt-daily` carries a randomised delay of up to 12 hours; `fstrim` issues
TRIM to the SD card; `man-db` rebuilds an index. Any of them landing inside a 60 s window
corrupts that run silently.

**Fix:** mask them on the appliance. This is not a measurement-only concern — a stage
instrument should not run `apt` while someone is playing.

## Finding 3 — CPU0 is doing everything

`irqaffinity=0,1` makes CPU0 the lowest core in the mask, so every movable interrupt lands
there alongside the one that cannot move.

| IRQ | count | source | movable |
|---:|---:|---|---|
| 11 | 4.19 M | arch_timer | no — per-CPU |
| **30** | **3.29 M** | **xhci_hcd** | **no** — empty `effective_affinity`, the signature of a chip with no `set_affinity` |
| 41 | 1.71 M | mmc0/mmc1 (SD + SDIO WiFi) | yes |
| 42 | 878 k | fe205000.i2c | yes |
| 43 | 857 k | **v3d — 3D graphics, on a headless audio appliance** | yes |

~6.7 M interrupts beyond the timer, **one of which has to be there.**

**Fix:** move the movable ones to CPU1; leave CPU0 for xhci and the timer. And for v3d,
prefer removing the driver over relocating its interrupt — an interrupt that does not exist
costs nothing. Identify what is polling i2c at 878 k.

## Finding 4 — services running that an appliance does not need

Running now: `bluetooth`, `avahi-daemon`, `cron`, `udisks2` (enabled), `polkit`,
`usb-audio-gadget`, plus **four `cloud-init` units enabled** on a Raspberry Pi.

Each is a scheduler entry, a wakeup source, and in the case of avahi and bluetooth a
periodic network/radio activity generator feeding the very IRQ line identified above.

**Keep:** ssh, NetworkManager/wpa_supplicant (needed for remote work), tailscaled (user's
call), journald, udevd, the `mpe-*` / `surge-*` stack.
**Prune:** bluetooth, avahi-daemon, cloud-init (all four), udisks2, cron, console-setup,
keyboard-setup, `usb-audio-gadget` **if the usb-host profile is not in use** (it currently
registers ALSA card 5).

---

## Finding 5 — ship-critical kernel config lives only on the appliance

`irqaffinity=0,1` is on the Pi's cmdline and **is not tracked in this repo**. It is
referenced in unit-file comments and measurement docs, but no file in git sets it, and
`/boot/firmware/cmdline.txt` is not under version control. E1 changed it to `0` outside git;
the partial revert left the machine at `0,1` while `plan/t7-sequence` still encoded E1's
`CPUAffinity=1 2 3` in the service units until 2026-08-21.

**A setting that is required for the product to hit its numbers, that exists on exactly one
SD card, is not a configuration — it is a liability.** Reflashing loses it silently, and
the loss looks like a performance regression with no cause.

Fix: bring the cmdline under management (a script that asserts the required flags and fails
loudly if they are absent, in the manner of `jackd-prestart.sh`). The IRQ affinity work in
Phase 0 will add more of these — do not repeat the pattern.

## The clean path

Everything above is **Phase 0**, and it comes before any further measurement. Numbers taken
before it are not wrong so much as **taken on a different machine than the one that will
ship**.

| phase | work | why it is in this order |
|---|---|---|
| **0. Hygiene** | Fix the crash loop · mask the timers · IRQ consolidation · prune services · investigate v3d and i2c | Cheap, high-impact, and every later number depends on it. One commit, one reboot. |
| **1. Re-baseline** | 512x3 and 256x3, condition A, n=15, **separate harness invocations** | Establishes the real baseline *and* settles the 256x3 discrepancy (12.10 vs 1.53). Separate invocations so run order cannot explain the result. |
| **2. Scarlett** | T11 ladder at defaults | Gated on hardware. MSD mode off, Sound Blaster unplugged (Tier 1 outranks it), confirm 480M enumeration before trusting a number. |
| **3. Deep levers** | T12 alignment · `threadirqs` · `lowlatency=N` | Only meaningful against a clean baseline on the device that ships. |

**Phase 0 changes one thing at a time is not required** — these are not competing
hypotheses, they are defects. Fix them together, re-baseline once, and treat the result as
the new zero. The one-variable rule applies to Phase 3, where we are testing hypotheses.

## What Phase 0 does to existing conclusions

**Survives** — it is structural or relative-within-one-run:
- Callbacks never miss their deadline; the drain is below JACK (T11)
- Period size binds, not total buffer — 466x at identical runway (T13)
- Xrun arrivals are Poisson; clock drift is ruled out (T5)
- 512 is not shippable with the looper; loop count is irrelevant there (T4a)

**Provisional** — absolute rates that a dirty baseline could have moved:
- Every xruns/min figure from 2026-08-18 to 2026-08-21, including the shipping claim
  (1024x3, cond D, 8 loops, 0.00) and the soak's 0.93/min
- The 512 A/B/C/D ladder

The shipping claim is likely to **improve**, not degrade — the crash loop and the timers
were noise added to it. But it needs re-taking before it is quoted.

---

## Corrections and resolutions from the 2026-08-21 top-down review

These override earlier suggestions in this doc and in the census:

| earlier suggestion | resolution |
|---|---|
| Remove vc4 / display stack | **WRONG** — DSI-1 drives the touch panel. Keep vc4 + DSI overlay. |
| Blacklist v3d immediately | **Defer** — pygame touch UI does not use GL; disable **HDMI outputs only** first (`video=HDMI-A-1:d video=HDMI-A-2:d`). |
| Poll i2c 878k — delete driver | **Partial** — IRQ 42 is **touchscreen** (edt_ft5x06). Move IRQ, do not remove bus. |
| pressure-remap crash = no Roli | **Detection bug** — USB can enumerate before ALSA MIDI port; wait for both. |
| Meter restart re-baseline in harness | **VOID the run** — backwards xrun count mid-window invalidates the window. |

---

## Phase 0 execution log (2026-08-21)

Branch: `yolo/system-hygiene-baseline`

| step | artifact | status |
|---|---|---|
| 0 | [`step0-restore-jack-2026-08-21.md`](../docs/measurements/step0-restore-jack-2026-08-21.md) | done — 1024×3 read back |
| 1 | [`step1-unknowns-2026-08-21.md`](../docs/measurements/step1-unknowns-2026-08-21.md) | done |
| 2 | [`step2-hygiene-applied-2026-08-21.md`](../docs/measurements/step2-hygiene-applied-2026-08-21.md) | done — reboot + verify |
| 3 | [`step3-rebaseline-2026-08-21.md`](../docs/measurements/step3-rebaseline-2026-08-21.md) | done |

### Re-baseline numbers (Step 3 — within-stream only)

| config | mean xruns/60 s | clean /15 | notes |
|---|---:|---:|---|
| 512×3 A | **0.13** | 14/15 | 1 stream — delta vs pre-hygiene **not interpretable** |
| 256×3 A | **7.80** | 0/15 | 1 stream — T11/T13/hyg are **3 different streams** |
| 1024×3 D8 | **0.20** | 12/15 | 1 stream — **not** a shipping claim |

**Stream-start variance:** [`stream-start-variance-2026-08-21.md`](../docs/measurements/stream-start-variance-2026-08-21.md)

**Phase 0:** defects fixed (measured on device). **Delta vs baseline unevaluated** until
`measure-stream-sample.sh` (N streams × k runs). **T12** (192×3 vs 256×3, 10 streams) is
the primary next experiment.

**Survives:** structural conclusions in §What Phase 0 does to existing conclusions (period binds, below-JACK drain, Poisson).

**Withdrawn:** every absolute xruns/min figure and all shipping claims until stream sampling.

### What Phase 0 changed (code)

- `mpe-peak-meter`: exit 0 on jack shutdown; `CPUAffinity=2 3`
- `measure-latency-run.sh`: VOID window if meter xruns go backwards; probe on 2–3
- `MeterXrunCounter`: `None` on mid-run restart (not silent re-baseline)
- `mpe-pressure-remap`: wait USB **and** ALSA MIDI; `Restart=no`; udev hot-plug restart
- `mpe-irq-affinity.service` + movable IRQs → CPU1
- `boot-assert-cmdline.sh` on jackd prestart
- `apply-appliance-hygiene.sh`: timers masked, services pruned, USB PM on, WiFi PS off, HDMI disabled in cmdline; **DefaultTimeoutStopSec=10s** via `config/systemd/mpe-appliance.conf`
- Shutdown: [`shutdown-timeout-fix-2026-08-21.md`](../docs/measurements/shutdown-timeout-fix-2026-08-21.md) — peak-meter `TimeoutStopSec=5`, interruptible SIGTERM

*Last updated: 2026-08-21 (America/Toronto)*

