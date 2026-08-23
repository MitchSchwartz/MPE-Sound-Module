# Pi 5 player session closeout — 2026-08-23

*Last updated: 2026-08-23 (America/Toronto)*

**Purpose:** Progress continuity for the Pi 5 player track and SR&ED evidence under **U10**
(cross-architecture replication). This session brought the board from fresh clone to a **playable
daily instrument**; measurement Suite 1 remains **blocked** on hardware.

**Host:** `raspberrypi5` · user `pi` · **192.168.1.106** (SSH `HostName` pinned; `.local` flaky)

Canon: [`PI5-PLAYER-SETUP-LOG.md`](../PI5-PLAYER-SETUP-LOG.md) · [`PI5-TRANSITION-PLAN.md`](../PI5-TRANSITION-PLAN.md) ·
[`pi5-predictions-2026-08-23.md`](pi5-predictions-2026-08-23.md) · [`SRED-EVIDENCE-2026.md`](../SRED-EVIDENCE-2026.md) §U10

---

## Executive summary

| Area | Status |
|------|--------|
| **Playable instrument** | Yes — touch UI, Quick Select, LUMI chain, 128×2 @ 48 kHz, OUT meter |
| **Core / IRQ model** | Pi 4 map **partially** preserved; Phase 1 verdict written |
| **Hygiene** | v3d blacklisted, BT off, avahi **on**, Wi‑Fi powersave off, performance governor |
| **JACK RT / FIFO** | Live — jackd **SCHED_FIFO 70**, Surge audio thread **65** |
| **Suite 1 / reference suite** | **Blocked** — no active cooler; **5 V / 3 A PSU** (below 27 W / 5 A spec) |
| **Predictions table** | Pre-registered; **Pi 5 actual** column still blank |

---

## What was done (chronological)

### Player bringup

1. Cloned `MPE-Module` (`dev`) on Pi 5; rsynced Surge binary, MPE-Library, user calibration/theme from Pi 4.
2. Ran touch setup, `configure-pi-paths.sh`, enabled services.
3. Laptop `mpe` config split (`mpe.env.pi4` / `mpe.env.pi5`); Pi 5 SSH pinned to LAN IP after mDNS failures.
4. Restored Quick Select (71 patches), theme, normalization, pressure curves.

### Configuration locked

| Item | Value / action |
|------|----------------|
| cmdline | `irqaffinity=0,1 threadirqs` + HDMI disabled for DSI |
| JACK buffer | **128×2** @ 48 kHz (64×2 broke ALSA/USB; 1024 parity script overwrote tuning — **fixed** to preserve appliance values) |
| CPU affinity | jackd / surge / looper / peak-meter → **2–3**; poly governor + touch UI → **0–1** |
| Governor | `MPE_CPU_GOVERNOR=performance` via `mpe-cpu-governor.service` |
| MIDI | apt `python3-rtmidi`; **no** `RTMIDI_API`; LUMI → remapper → Midi Through → Surge |

### Hygiene pass (repo + Pi)

- `config/modprobe.d/blacklist-v3d-mpe.conf` + `apply-appliance-hygiene.sh` (v3d install path).
- Fixed hygiene prune bug (`avahi-daemon.service.service`); **avahi no longer pruned** (reachability).
- Bluetooth disabled; Wi‑Fi stays (not Ethernet-only).
- `touch-patch-browser.service`: `CPUAffinity=0 1`.

### IRQ investigation — Phase 0 + Phase 1

- Idle + **loaded** census: `appliance-state/pi5-irq-census-2026-08-23/` (committed `d99d6db`).
- Loaded run: 24-voice `midi-load-hold` × 60 s @ 128×2.
- Verdict: [`pi5-irq-phase1-2026-08-23.md`](pi5-irq-phase1-2026-08-23.md).

### RT verification (end of session)

- `verify-jack-rt-limits.sh pi` → pass (`ulimit -r=95`, audio group, `audio.conf`).
- Live: jackd tid **3729** `SCHED_FIFO 70`; Surge tid **3853** `SCHED_FIFO 65`.
- **No** PREEMPT_RT kernel on 2712 (apt measured); **no** IRQ-thread RT (`chrt` on Pi 4 E-step).

---

## Key findings (SR&ED-relevant)

### F1 — Pi 4 IRQ map is only partially portable (U10 / platform)

On BCM2712 + RP1, Sound Blaster sits on **RP1 usb1 IRQ 131**; touch **i2c IRQ 111**; Wi‑Fi **mmc1 IRQ 162**.
All sampled lines are **CPU0-pinned and not writable** via `/proc/irq/*/smp_affinity_list`.
Pi 4 movable-IRQ script (41/42/43/…) is a **no-op**.

Under 24-voice load (60 s), usb1 + i2c each ~**410 IRQ/s** on CPU0; DSI + Wi‑Fi secondary.
**CPUAffinity=2–3** for audio still works; **irqaffinity=0,1** does not spread load evenly —
GIC still targets CPU0 heavily.

**Consequence:** Pi 5-specific risk is **CPU0 contention** (usb + i2c + Wi‑Fi), not fixable in
userland today. Do not enable `mpe-irq-affinity.service` until a Pi 5 IRQ map exists.

### F2 — Thermal / PSU headroom not yet characterized under ceiling load

Loaded census @ 24 voices: `throttled=0x0`, temp **63.7–66.4°C**, ARM **2400 MHz** entire window.
Operator noted **no cooler** and **3 A PSU** — flags expected under 64-voice soak or compile.
**Suite 1 and latency claims blocked** until active cooler + **27 W PSU**.

### F3 — Buffer ladder re-derived on Pi 5 (not ported from Pi 4)

64×2 caused ALSA/driver collapse; **128×2** is the current player sweet spot (ear-validated).
This is **platform-specific tuning**, not a contradiction of Pi 4 V11 floors — replication suite
must still run at **frozen Pi 4 cells** once hardware allows.

### F4 — PREEMPT_RT asymmetry confirmed

`linux-image-rpi-2712-rt` not in apt. Pi 4 RT-kernel lever **does not transfer**. JACK
`-R -P70` + unit `LimitRTPRIO=95` is the live scheduling stack on Pi 5.

---

## Current appliance checklist

Run after reboot or config change:

```bash
# Laptop
mpe5 ping && mpe5 status && mpe5 osc-check

# Pi 5
~/MPE-Module/scripts/verify-jack-rt-limits.sh pi
~/MPE-Module/scripts/boot-assert-cmdline.sh
lsmod | grep -v '^$' | grep v3d || echo "v3d not loaded (good)"
pgrep -a jackd    # expect -R -P70 -p 128 -n 2
vcgencmd get_throttled
```

Human: patches load, OUT meter moves, LUMI plays, no crackle at 128×2.

---

## Blocked / deferred

| Gate | Why | Unblock |
|------|-----|---------|
| Reference suite / Suite 1 | Thermal + PSU; predictions unscored | Cooler mounted + 27 W PSU |
| Loaded census @ **64** voices | Same | After cooler/PSU |
| Poly governor Pi 5 tuning | **Preliminary Gate B pass** — `always_on` + jack baseline 96; see [`poly-governor-v2-always-on-pi5-2026-08-23.md`](poly-governor-v2-always-on-pi5-2026-08-23.md) | Full Gate B + parity env promote |
| cmdline experiments (`isolcpus`, `irqaffinity=0`) | E1 rule; one variable per reboot | Phase 3 plan only |
| Pi 5 `-mcpu=cortex-a76` Surge build | Player uses Pi 4 binary for smoke; build is measurement track | When suite needs matched revision |

---

## Next steps (ordered)

1. **Daily use** — validate 128×2 under real patches; note any throttle or crackle (`vcgencmd get_throttled`).
2. **Hardware** — mount ordered cooler; swap to 27 W PSU.
3. **64-voice loaded census** — repeat `capture-pi5-irq-loaded.sh` with throttle series.
4. **Frozen reference suite** — `measure-reference-suite.sh` unchanged from Pi 4; fill [`pi5-predictions-2026-08-23.md`](pi5-predictions-2026-08-23.md) **Pi 5 actual** column.
5. **Poly governor** — daily play at baseline 96; complete Gate B B3/B4; promote to parity env if stable.

---

## Repo anchors (this session)

| Commit | Topic |
|--------|-------|
| `615441c` | Keep avahi for mDNS reachability |
| `1a7c5d0` | Player 128×2 default; parity script preserves tuned buffer |
| `d99d6db` | Loaded IRQ census + Phase 1 verdict |
| `b5b61c0` | Investigation plan checkboxes |

| `e66260e` | Poly governor always-on jack + linear baseline (Pi 5 ear tune) |

Docs: [`poly-governor-v2-always-on-pi5-2026-08-23.md`](poly-governor-v2-always-on-pi5-2026-08-23.md) · [`PI5-HYGIENE-AND-CONFIG-PLAN.md`](PI5-HYGIENE-AND-CONFIG-PLAN.md) · [`PI5-IRQ-INVESTIGATION-PLAN.md`](PI5-IRQ-INVESTIGATION-PLAN.md) · [`pi5-irq-phase1-2026-08-23.md`](pi5-irq-phase1-2026-08-23.md)

---

## SR&ED framing

**Uncertainty (U10):** Do Pi 4 findings (`a`, filter-over-osc cost, xrun class, buffer floors)
hold on Cortex-A76 @ 2400 MHz with RP1 USB/IRQ topology?

**Advancement this session:** Second platform **instrumented and partially characterized** before
any optimisation — IRQ census shows **different binding surface** (RP1, non-movable IRQs) than
Pi 4 (xhci on CPU0, partial levers). Player path proves the **software stack runs**; replication
measurements not yet run (correct per transition plan: *replication before optimisation*).

**Eligible labour categories:** platform bringup (meas), IRQ/hygiene investigation docs (review),
loaded census instrument run (meas), RT verification (meas).

**Not yet eligible as results:** Suite 1 predictions — no scored cells.

---

*Hands-on: ~3 h (Mitch, 2026-08-23) — [`SRED-DAILY-LOG.md`](../SRED-DAILY-LOG.md).*
