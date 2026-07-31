# USB audio to host PC (`usb-host` profile)

*Last updated: 2026-07-31 (America/Toronto)*

When the Pi is tethered to a laptop or desk PC, route Surge output over **USB-C** as a standard UAC2 playback device — no aux cable to the host. **Standalone gig mode** (`MPE_AUDIO_PROFILE=standalone`, default) is unchanged: Sound Blaster → 3.5 mm analog.

Full research and phased plan: **[USB-AUDIO-PASSTHROUGH-PLAN.md](USB-AUDIO-PASSTHROUGH-PLAN.md)**.

---

## One-time Pi boot config (manual)

Edit **`/boot/firmware/config.txt`** on the Pi.

**Pi 4 Model B** — add a **`[pi4]`** section (do not put peripheral mode under `[all]`; that breaks USB-A host ports for Roli/DAC):

```ini
[pi4]
dtoverlay=dwc2,dr_mode=peripheral
```

**Other boards** — follow the plan doc; Pi 5 / CM5 images often ship `dtoverlay=dwc2,dr_mode=host` under `[cm5]` only. Leave that as-is unless you are on Pi 4.

Reboot once with **USB-C unplugged from the host**. This enables the USB-C port as a **device (gadget)** port.

### Boot checklist (`usb-host` first enable)

1. Unplug **USB-C data** from the laptop/dock.
2. Add the `[pi4]` overlay (Pi 4) or your board’s peripheral overlay.
3. Set `MPE_AUDIO_PROFILE=usb-host` in `/etc/mpe/mpe.env`.
4. Run `./scripts/configure-pi-paths.sh --local --force` and `sudo systemctl enable usb-audio-gadget.service`.
5. Reboot; wait for SSH (~90s).
6. Verify: `ls /sys/class/udc/` non-empty, `./scripts/setup-usb-audio-gadget.sh status` → bound, `aplay -l` shows **UAC2Gadget**.
7. **Then** plug **USB-A (host) → USB-C (Pi)**; on the host run `lsusb` (expect **1d6b:0104** / “USB Audio Passthrough”).

**Important:** On Pi 4, boot with the **USB-C data cable unplugged** from the host PC. A connected host during early firmware/kernel init can hang the boot splash (four raspberries) before SSH starts. Plug the cable in after the Pi is up (or after `usb-audio-gadget.service` is active). See [PI-BOOT-RECOVERY.md](PI-BOOT-RECOVERY.md). Do not enable legacy `g_audio` / `g_midi` modules alongside configfs — the setup script uses **configfs UAC2** only.

**Pi 4 / Pi 5 only** — Pi 3 has no OTG. Roli and Sound Blaster stay on **USB-A host ports**.

---

## Enable `usb-host` profile

1. On the Pi, edit **`/etc/mpe/mpe.env`**:

   ```bash
   MPE_AUDIO_PROFILE=usb-host
   ```

2. Refresh systemd units (installs `usb-audio-gadget.service`):

   ```bash
   cd ~/MPE-Module
   ./scripts/configure-pi-paths.sh --local --force
   ```

3. Plug a **data-capable USB cable** from the host PC to the Pi **USB-C** port. Prefer **USB-A (host) → USB-C (Pi)** on Pi 5 + Mac; USB-C ↔ USB-C can have PD quirks.

4. Start the gadget and Surge:

   ```bash
   sudo systemctl start usb-audio-gadget.service
   sudo systemctl restart surge-xt-cli.service
   ```

5. On the **host**, select the new playback device (often **“USB Audio Passthrough”** or **“MPE Sound Module”**). Linux: `pavucontrol`; Windows: Sound settings.

### Return to analog (gig / couch)

```bash
# In /etc/mpe/mpe.env:
MPE_AUDIO_PROFILE=standalone

sudo systemctl stop usb-audio-gadget.service
sudo systemctl restart surge-xt-cli.service
```

---

## How it works (Approach C)

```
[Roli] → Surge XT CLI → ALSA → UAC2 gadget card → USB-C → [Host speakers/DAW]
```

- **No** `alsaloop`, **no** dual analog+USB mirror.
- Sample rate: **44100 Hz** stereo (matches Surge tuning — see `PATCH_NORMALIZATION.md`).
- Switching profiles **restarts Surge** (same pattern as USB DAC hot-plug).

---

## Scripts and services

| Path | Role |
|------|------|
| `scripts/setup-usb-audio-gadget.sh` | Create/bind or tear down configfs UAC2 gadget |
| `config/usb-audio-gadget.service` | Start gadget at boot when profile is `usb-host` |
| `scripts/detect-audio-device.sh` | Tier 0: gadget card when profile is `usb-host` |

Diagnostics:

```bash
./scripts/setup-usb-audio-gadget.sh status
./scripts/test-audio-detection.sh
aplay -l
```

Dry-run (no root changes):

```bash
sudo MPE_AUDIO_PROFILE=usb-host ./scripts/setup-usb-audio-gadget.sh start --dry-run
```

---

## Touch UI

System settings (⋯) shows a read-only **Audio profile** line when running the touch browser.

---

## Status

**Phase 1 scripts landed — Pi hardware spike still required.** See plan doc for Phase 0 exit criteria (10+ min stable playback, latency note, cable matrix).
