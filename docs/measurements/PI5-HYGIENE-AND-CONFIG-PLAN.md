# Pi 5 hygiene and platform config — investigation plan

*Created: 2026-08-23 (America/Toronto)*

**Goal:** Bring Pi 5 player hygiene to Pi 4 parity where it still applies, apply safe
module blacklists, close service gaps, and document Pi 5–specific levers before Suite 1.

**Tracks:** Player (touch UI, Wi‑Fi on) vs day‑0 measurement (headless, minimal UI). This
plan covers both; mark each item **player**, **measure**, or **both**.

Canon: [`Documents/specs/system-hygiene-baseline.md`](../../Documents/specs/system-hygiene-baseline.md) ·
[`PI5-IRQ-INVESTIGATION-PLAN.md`](PI5-IRQ-INVESTIGATION-PLAN.md) ·
[`PROMPT-PI5-DAY0.md`](PROMPT-PI5-DAY0.md).

---

## Pi 4 vs Pi 5 — what transfers

| Area | Pi 4 (BCM2712 predecessor / v8) | Pi 5 (BCM2712 + RP1) | Action |
|------|--------------------------------|----------------------|--------|
| PREEMPT_RT kernel | `linux-image-rpi-v8-rt` available | **No `linux-image-rpi-2712-rt`** — measured 2026-08-23 | Record asymmetry; no RT lever on Pi 5 |
| USB audio path | SoC xhci, IRQ 30, CPU0 pinned | **RP1 xhci usb1**, IRQ **131**, CPU0 pinned, **not writable** | Census + accept; see IRQ plan |
| Movable IRQ script | IRQs 41/42/43/28/57 → CPU1 | **All skip** — wrong numbers + RP1 not writable | No-op on Pi 5; do not enable `mpe-irq-affinity` until Pi 5 map exists |
| Touch / DSI | vc4 + `vc4-kms-dsi-7inch`, edt_ft5x06 | **drm_rp1_dsi** + same overlay; touch on **RP1 i2c** IRQ **111** (~2.8M) | Keep display stack; investigate i2c load in Phase 1 |
| v3d 3D GPU | Blacklisted Step 3 | **Was loaded** (~625k IRQ 166) | **Blacklist now** — repo + hygiene script |
| Wi‑Fi | SDIO mmc1 | **mmc1** IRQ 162, CPU0, not writable | Keep; powersave off; measure load delta (P5-C5) |
| Bluetooth | Disabled on control (varies) | **Disabled** 2026-08-23 | Blacklist BT modules after reboot verify (Phase B) |
| cmdline | `irqaffinity=0,1 threadirqs video=HDMI-A-1:d video=HDMI-A-2:d` | Same + `fbcon=map:0 loglevel=3 logo.nologo` | Assert via `boot-assert-cmdline.sh` |
| `CPUAffinity=2-3` | jackd, surge, looper, peak meter | Same except **touch-patch-browser unset** | Add drop-in for touch UI (Phase B) |

---

## Module blacklist matrix

### Apply now (safe, Pi 4 precedent)

| Module | Why | IRQ / load | Reboot |
|--------|-----|------------|--------|
| **v3d** | Pygame touch UI does not use GL; Pi 4 Step 3 | IRQ 166–167 ~625k on CPU0 | **Yes** |

**Repo:** `config/modprobe.d/blacklist-v3d-mpe.conf` · installed by `apply-appliance-hygiene.sh`.

**Keep** `dtoverlay=vc4-kms-v3d` in `config.txt` — same pattern as Pi 4 (overlay present, module blocked).

### Investigate before blacklist (Phase B — one module per reboot)

| Module | Role on Pi 5 | Risk if removed | Test |
|--------|--------------|-----------------|------|
| **rpi_hevc_dec** | HEVC decode | None for audio | `lsmod`; blacklist; reboot; touch UI + jackd smoke |
| **pisp_be** | Camera ISP | None unless camera added | Same |
| **joydev** | Legacy joystick | None on MPE | Same |
| **snd_hrtimer** | ALSA hrtimer | **Low** — seq may use it | Only after noting `snd_seq` dependency |
| **hci_uart**, **btbcm**, **bnep**, **rfcomm**, **bluetooth** | BT stack | None if BT permanently off | After `systemctl disable bluetooth`; confirm no `rfkill` need |

### Never blacklist (player)

| Module / bus | Why |
|--------------|-----|
| **brcmfmac** / **cfg80211** | Wi‑Fi stays (not Ethernet-only) |
| **edt_ft5x06**, **regmap_i2c**, **RP1 i2c** | Touch panel — IRQ 111 is high but required |
| **vc4**, **drm_rp1_dsi**, **tc358762**, **panel_simple** | DSI display |
| **snd_usb_audio** | Sound Blaster |
| **rp1_***, **drm_rp1** | RP1 southbridge — breaking these kills USB/Ethernet/GPIO |

---

## Service and timer hygiene

**Script:** `scripts/apply-appliance-hygiene.sh` (idempotent).

| Item | Pi 5 status (2026-08-23) | Target |
|------|--------------------------|--------|
| apt/daily, fstrim, logrotate, … timers | **Masked** | masked |
| **bluetooth** | disabled | disabled |
| **avahi-daemon** | disabled (hygiene) → **re-enable for mDNS** | **enabled** (player SSH) |
| **cron** | **still enabled** | disabled |
| **udisks2** | **still enabled** | disabled |
| **console-setup / keyboard-setup** | enabled | disabled |
| **cloud-init** | `cloud-init-main` enabled | disable all variants |
| **usb-audio-gadget** | disabled | disabled (no UAC2 card) |
| Wi‑Fi powersave | applied | off (`iw` + NM) |
| USB autosuspend | applied | `power/control=on` on audio bus |
| **DefaultTimeoutStopSec=10s** | installed | keep |
| **v3d blacklist** | pending reboot | install + reboot |

**Action:** Re-run `sudo ./scripts/apply-appliance-hygiene.sh` on Pi 5, then reboot once for v3d + any cmdline gaps.

---

## Cmdline and config.txt (known good)

### Required cmdline (player + measure)

```
irqaffinity=0,1 threadirqs video=HDMI-A-1:d video=HDMI-A-2:d
```

**Asserted** by `boot-assert-cmdline.sh` on every jackd start.

**Pi 5 player extras (keep):** `fbcon=map:0 loglevel=3 logo.nologo cfg80211.ieee80211_regdom=CA`

### config.txt (player)

| Setting | Purpose |
|---------|---------|
| `dtoverlay=vc4-kms-v3d` | KMS stack (v3d module blacklisted separately) |
| `dtoverlay=vc4-kms-dsi-7inch` | SmartiPi touch |
| `disable_fw_kms_setup=1` | Standard Pi OS |
| `dtoverlay=dwc2,dr_mode=host` | USB host (from imager) |

**Do not remove** vc4/DSI overlays — wrong per hygiene baseline correction.

### Investigate later (one token per reboot — IRQ plan Phase 3)

| Candidate | Hypothesis | Falsifier |
|-----------|------------|-----------|
| `irqaffinity=0,1,2,3` | Spread default IRQ mask on 2712 | IRQ counts spread; latency unchanged or worse |
| `irqaffinity=0` only | E1 retest on Pi 5 | xruns worse → reject |
| `isolcpus=2,3 nohz_full=2,3 rcu_nocbs=2,3` | Audiophile Pi 5 pattern (Fedora/DRUP) | Boot/shell issues; measure only after cooler + C0 |
| `kthread_cpus=0-1` | Keep kthreads off audio cores | Document from kernel doc before trial |

**Hard rule:** never change `irqaffinity` and unit `CPUAffinity` in the same experiment.

---

## Unit and process placement

| Unit | Pi 5 today | Target |
|------|------------|--------|
| mpe-jackd | CPUAffinity=2-3 | keep |
| surge-xt-cli | CPUAffinity=2-3 | keep |
| mpe-sooperlooper | CPUAffinity=2-3 | keep |
| mpe-peak-meter | CPUAffinity=2-3 | keep |
| surge-poly-governor | CPUAffinity=0-1 | keep (IRQ-side cores) |
| **touch-patch-browser** | **unset** | **CPUAffinity=0-1** or 2-3 — decide in Phase 1.4 (keep UI off audio cores) |
| mpe-pressure-remap | unset | optional 0-1 (MIDI, not RT) |

**mpe-irq-affinity.service:** leave **disabled** on Pi 5 until a Pi 5 IRQ map replaces Pi 4 numbers.

---

## Sound Blaster / JACK (platform facts)

| Check | Pi 5 action |
|-------|-------------|
| Device | USB **Bus 001 Port 001** → RP1 **usb1** → IRQ **131** |
| Card index | Resolve at boot — never hardcode (`detect-jack-device.sh`) |
| JACK RT | debconf `jackd2` RT=yes; user in `audio`; `verify-jack-rt-limits.sh` |
| Buffer | Player parity: `MPE_JACK_BUFFER=1024` in `/etc/mpe/mpe.env` |
| USB | Class-compliant; `-n 3` periods (wiki.linuxaudio.org); already appliance default |
| PSU / thermal | **27 W PSU + active cooler** before trusting compile or Suite 1 numbers |

**No Pi 5–specific JACK patch** needed (Pi 1 packed-struct bug is historical).

---

## External Pi 5 audio knowledge (sanity check)

| Source | Claim | MPE relevance |
|--------|-------|---------------|
| [linux-rpi #7301](https://github.com/raspberrypi/linux/issues/7301) | RP1 IRQ threads may not follow affinity without kernel patch | Explains writable-but-useless affinity; **no userland fix** |
| [Fedora Pi audiophile](https://github.com/cometdom/fedora-rpi-audiophile-setup) | `isolcpus=1-3 nohz_full=1-3 irqaffinity=0` on 4-core Pi 5 | Late-phase candidate; high risk |
| [RTOS Pi 5 build notes](https://github.com/ShekharShwetank/RTOS) | Community **2712 RT** builds exist; **not in Raspberry Pi OS apt** | Asymmetry vs Pi 4 is real; optional future spike, not day-0 |
| [linuxaudio.org RPi wiki](https://wiki.linuxaudio.org/wiki/raspberrypi) | USB: `-n 3`, softmode for stability | Already aligned |

---

## Phased execution

### Phase A — immediate (2026-08-23)

- [x] Document this plan
- [x] Add `config/modprobe.d/blacklist-v3d-mpe.conf` + hygiene installer
- [ ] Run `apply-appliance-hygiene.sh` on Pi 5 (services + v3d)
- [ ] **Reboot Pi 5**
- [ ] Post-reboot verify: `lsmod | grep v3d` empty; `grep v3d /proc/interrupts` empty; touch UI + jackd up
- [ ] Re-run `capture-pi5-irq-census.sh` (idle) — compare IRQ 166 gone

### Phase B — after v3d verified (one change per reboot)

1. Disable remaining services (if any survive hygiene)
2. Blacklist BT kernel modules (if BT stays off)
3. Blacklist **rpi_hevc_dec** + **pisp_be** (media stack)
4. Add **touch-patch-browser** `CPUAffinity=0-1`
5. Optional: **joydev** blacklist

Each step: smoke test (touch, MIDI, jackd 60 s) before next.

### Phase C — tied to IRQ investigation plan

- Phase 0 **loaded** census (Surge @64 ~60 s)
- Phase 1 write-up: Pi 4 copy valid or misleading?
- Phase 3 candidates P5-C0…C5 (cooler mounted first)
- Promote winners to `config/platform/pi5.env` (create on first validated map)

### Phase D — day-0 measurement profile

- Headless: stop `touch-patch-browser` for cells only (P5-C4)
- Same Surge SHA as Pi 4 (`253f8d86`)
- C0 instruments: peak meter + xrun probe built
- No Tier 3 pygame until platform comparison done (per PROMPT-PI5-DAY0)

---

## Open questions (ranked)

1. **RP1 i2c IRQ 111 (~2.8M)** — touch poll rate vs driver bug? Compare idle vs UI idle vs UI active.
2. **Does v3d removal change CPU0 margin** before usb1/mmc1 contention? Re-census after Phase A.
3. **Pi 5 `mpe-irq-affinity` replacement** — worth a script that only logs, or skip entirely?
4. **Community 2712 RT kernel** — out of scope for player; note in Suite 1 report only.

---

## Immediate commands (Pi 5)

```bash
cd ~/MPE-Module && git pull
sudo ./scripts/apply-appliance-hygiene.sh
sudo reboot
# after boot:
lsmod | grep v3d || echo "v3d absent OK"
./scripts/capture-pi5-irq-census.sh
mpe status
```

*Last updated: 2026-08-23 (America/Toronto)*
