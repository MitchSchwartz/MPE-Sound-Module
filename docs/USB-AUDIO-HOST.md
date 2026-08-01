# USB audio to host PC (`usb-host` profile)

*Last updated: 2026-07-31 (America/Toronto)*

When the Pi is tethered to a laptop or desk PC, route Surge output over **USB-C** as a standard UAC2 playback device — no aux cable to the host. **Standalone gig mode** (`MPE_AUDIO_PROFILE=standalone`, default) is unchanged: Sound Blaster → 3.5 mm analog.

Full research and phased plan: **[USB-AUDIO-PASSTHROUGH-PLAN.md](USB-AUDIO-PASSTHROUGH-PLAN.md)**.

---

## One-time Pi boot config (manual)

Edit **`/boot/firmware/config.txt`** on the Pi and add:

```ini
dtoverlay=dwc2,dr_mode=peripheral
```

Reboot once. This enables the USB-C port as a **device (gadget)** port.

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

5. On the **host**, select the gadget as a **capture / input / recording** device — **not** playback/output. The Pi sends audio *out* via UAC2; the host receives it on its **input** side (Passthrough). Linux: `pavucontrol` → Recording; Windows: Sound settings → Input.

### Host capture on Linux

List cards:

```bash
arecord -l
```

Prefer **hardware** device for capture — `plughw:N,0` has been observed **silent** while `hw:N,0` works (tone test peak ~26267):

```bash
arecord -D hw:N,0 -f S16_LE -r 44100 -c 2 -d 5 /tmp/mpe-host-capture.wav
```

**Root-caused 2026-07-31:** if the Pi is powered through a USB-C PD dock plugged into the *same* port used for gadget data, `dwc2` can get stuck `not attached` (check `cat /sys/class/udc/*/state` on the Pi) even with MIDI/OSC correctly reaching Surge. This is a Pi 4 hardware limitation, not a Surge bug — see **[USB-AUDIO-PASSTHROUGH-SPIKE.md](USB-AUDIO-PASSTHROUGH-SPIKE.md)**. Power the Pi via GPIO 5V/GND, independent of the USB-C data cable, to avoid it.

`speaker-test` on the Pi without a host actively capturing the UAC2 stream may report **I/O error -5** — expected when nothing reads the gadget endpoint.

Pi-side checks: `./scripts/usb-host-verify.sh`

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
| `scripts/usb-host-verify.sh` | Pi-side profile/gadget/Surge checks + host `arecord` hints |

Diagnostics:

```bash
./scripts/usb-host-verify.sh
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

**Phase 1 scripts landed — Phase 0 spike documented.** See **[USB-AUDIO-PASSTHROUGH-SPIKE.md](USB-AUDIO-PASSTHROUGH-SPIKE.md)** for measured results (`hw` vs `plughw`, host input vs playback, open capture-during-play issue). Plan doc Phase 0 exit criteria (10+ min stable playback, latency note) still pending soak tests.
