# CPU census — what runs where, and what floats (2026-08-21)

Taken while `64x8` condition A was running. The point is not the instantaneous placement
but **which things have a declared home and which land wherever the scheduler puts them.**

## Kernel-level policy

| knob | value |
|---|---|
| `isolcpus` | **NOT SET** |
| `nohz_full` | **NOT SET** |
| `rcu_nocbs` | **NOT SET** |
| `threadirqs` | **NOT SET** |
| `irqaffinity` | **`0,1`** |
| systemd `CPUAffinity` (system.conf) | **unset** — every unit defaults to all four cores |

**Only two units in the entire system declare a CPU affinity:**

| unit | CPUAffinity |
|---|---|
| `mpe-jackd.service` | `2-3` |
| `surge-xt-cli.service` | `2-3` |

Everything else floats.

## Per-CPU picture

### CPU0 — the interrupt core, and it is overloaded

| what | counts / detail |
|---|---|
| `xhci_hcd` IRQ 30 | **4.14 M** — `effective_affinity` **empty = unmovable** |
| `mmc0/mmc1` IRQ 41 (SD + SDIO WiFi) | 1.90 M — movable, and **already a threaded handler** (`irq/41-mmc0`, FF 50) |
| `fe205000.i2c` IRQ 42 | 982 k — movable, source unidentified |
| `v3d` IRQ 43 | 957 k — movable; 3D graphics on a headless appliance |
| `crtc` IRQ 57 | 254 k — movable |
| `eth0` IRQ 28 | 59 k — movable, link is DOWN |
| `fe00b880.mailbox` IRQ 14 | 50 k |
| `irq/27-aerdrv`, `irq/44-feb00000` | threaded, FF 50, pinned to 0 |
| **`mpe-xrun-probe`** | **FF 65, affinity `0-3`, observed on CPU0** |
| `python3` | **22.1% CPU, on CPU0** — unidentified |

**Every single one of these except IRQ 30 could be somewhere else.**

### CPU1 — the other IRQ core

Carries the same `irqaffinity=0,1` mask but receives almost nothing, because Linux targets
the **lowest core in the mask** and GICv2 offers no 1-of-N distribution. Its `arch_timer`
count is **7.35 M against ~4.3 M elsewhere** — unexplained, worth a look.

Currently hosting: `mpe-peak-meter` (FF 65, affinity `0-3`), `watchdogd`, a `python3` at
6.1%, several `vc4 hdmi` IRQ threads.

### CPU2 — nominally audio

| what | detail |
|---|---|
| `surge-xt-cli` | FF 65, affinity **`2-3`** — correctly pinned |
| `JUCE MIDI Input`, `JUCE OSC server` | affinity `2-3` — inherit correctly |
| **`card1-crtc0` … `card1-crtc5`** | **6 threads, FF 50, affinity `0-3`, all sitting on CPU2** |
| `irq/51-vc4 hdmi`, `irq/52-vc4 hdmi` | FF 50, affinity `0-3` |

**Eight HDMI/display realtime threads are on an audio core.** They are FF 50 against
surge's FF 65 so they cannot preempt it, but they are contending for the core.

### CPU3 — nominally audio

| what | detail |
|---|---|
| `jackd` | FF 70, affinity **`2-3`** — correctly pinned |
| `irq/46-vc4 hdmi`, `irq/47-vc4 hdmi` | FF 50, affinity `0-3` |

## The list of things with no declared home

Everything here has affinity `0-3` and lands wherever the scheduler decides:

| process | prio | why it matters |
|---|---|---|
| **`mpe-peak-meter`** | FF 65 | **The meter every xrun measurement depends on.** Unpinned, observed on CPU1. |
| **`mpe-xrun-probe`** | FF 65 | **The probe that measures xruns.** Observed on **CPU0**, the same core as the USB interrupt it is trying to measure the effects of. |
| `mpe-pressure-remap` | TS | Crash-looping 617x; enumerates MIDI (USB control transfers) every 19 s |
| `midi-clock-in`, `surge-poly-governor`, `surge-watchdog`, `touch-patch-browser` | TS | Unexamined |
| `card1-crtc0-5` (6 threads) | FF 50 | Display, on CPU2 |
| `irq/46-53-vc4 hdmi` (8 threads) | FF 50 | Display, scattered 0-3 |
| `watchdogd` | FF 50 | — |
| `python3` @ 22.1% on CPU0 | TS | **Unidentified. 22% of the interrupt core.** |
| `python3` @ 6.1% on CPU1 | TS | Unidentified |

## Unknowns to resolve

1. **What is the `python3` using 22% of CPU0?** Largest single unexplained consumer, on the
   most contended core.
2. **What is polling `fe205000.i2c` 982 k times?**
3. **Why is CPU1's `arch_timer` count 70% higher than the others?**
4. **`IPI1` at 5.4-5.9 M per core** — inter-processor interrupts are very high. Cross-core
   wakeups are not free and this is worth understanding.
5. Do `midi-clock-in`, `surge-poly-governor`, `touch-patch-browser` contain periodic loops?
   They have never been audited against the no-forks doctrine.

## The shape of the problem

The audio *producers* are pinned. **Nothing else is** — including both instruments the
measurement rig depends on, one of which is sitting on the interrupt core it is supposed to
be measuring around.

`irqaffinity=0,1` was meant to keep interrupts off the audio cores. It does that. But
because GICv2 targets the lowest core in the mask, it also concentrates **everything
movable onto CPU0 alongside the one interrupt that cannot move.**

The fix is not more masks. It is: **give every realtime thread a declared home, move
everything movable off CPU0, and delete what does not need to run at all.**
