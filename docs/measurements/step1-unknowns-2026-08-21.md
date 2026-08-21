# Step 1 — unknowns resolved (2026-08-21)

Taken on raspberrypi2 after Step 0 (1024×3). Census reference:
[`cpu-census-2026-08-21.md`](cpu-census-2026-08-21.md).

## 1a — python3 on CPU0 @ 22% and CPU1 @ 6%

| PID | CPU% | core | command |
|---:|---:|---:|---|
| 2196 | 22.8 | 2 | `touch_patch_browser.py` — **touch UI main loop** |
| 719278 | 6.0 | 0 | `mpe-pressure-remap.py` |
| 1097 | 0.9 | 0 | `midi-clock-in.py` |
| 2078 | 0.4 | 1 | `surge-poly-governor.py` |

The census “22% on CPU0” was a snapshot during an active measurement (`midi-load.py` also
present). The dominant steady-state consumer is **touch_patch_browser** (~23% of one core),
not an unidentified daemon. It belongs on a non-audio core (future pin); it is not xhci
competition.

**Confidence:** measured (ps during Step 1 pass).

## 1b — IRQ 42 / fe205000.i2c (touchscreen)

- Device: `10-0038` · driver `edt_ft5x06` · model `generic ft5x06 (79)`
- **Interrupt-driven** (IRQ 42 in `/proc/interrupts`; no userspace pollrate sysfs)
- **Do not remove** i2c/vc4 display stack — DSI-1 is **connected** (SmartiPi panel)

## 1c — CPU1 arch_timer ~2× other cores

| core | arch_timer (IRQ 11) |
|---:|---:|
| CPU0 | 7.23 M |
| CPU1 | **14.42 M** |
| CPU2 | 6.28 M |
| CPU3 | 6.17 M |

CPU1 also hosts `mpe-peak-meter` (unpinned at census time), `watchdogd`, and several
`vc4 hdmi` IRQ threads. The extra timer ticks correlate with **more scheduler/timer
activity on that core**, not a separate hardware timer. Moving movable IRQs off CPU0 and
pinning the meter to 2–3 should rebalance this.

**Confidence:** measured counts; mechanism = experiment until post-hygiene recount.

## 1d — IPI1 ~5.4–8.3 M per core

All four cores show high **Function call interrupts (IPI1)** — typical of cross-core
wakeups (`smp_call_function_single`, RT thread migration, journald, Python GIL, pygame
event loop waking worker threads). Not a single rogue driver; expect reduction after
service prune + IRQ consolidation but not zero on a 4-core Pi.

**Confidence:** measured `/proc/interrupts`; source characterisation = partial (no perf on Pi).

## 1e — no-forks-in-periodic-loops audit

| unit | loop | forks in loop? |
|---|---|---|
| `midi-clock-in.py` | 50 ms sleep + RtMidi callback | **No** — file write only |
| `surge-poly-governor.py` | 150 ms thread; `/proc` + OSC | **No** |
| `touch_patch_browser.py` | pygame `Clock.tick` | **No** subprocess in frame loop |

**Confidence:** code audit (this commit).

## Display / GL (feeds Step 2e)

- Touch UI: **pygame** software blit to DRM/KMS — **no OpenGL** in app code
- HDMI-A-1 / HDMI-A-2: **disconnected** but 8× `vc4 hdmi` + 6× `card1-crtc` threads still run
- Safe fix: `video=HDMI-A-1:d video=HDMI-A-2:d` on cmdline — **not** removing `vc4-kms-v3d`
- v3d IRQ (43) remains until a separate experiment blacklists 3D; not required for pygame

## Pressure remap note (feeds Step 2b)

Journal 12:39–12:43: `wait-for-usb-midi.sh` reported “Roli not detected” while **lsusb
showed 2af4:0e00 LUMI Keys BLOCK** — USB present, **ALSA MIDI port not ready**. Root cause
is **lsusb-only wait**, not absent hardware. Fixed in Phase 0 by waiting for `aconnect -l`
port names too.
