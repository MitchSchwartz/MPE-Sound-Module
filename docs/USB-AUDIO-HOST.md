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

## Plug-and-play (host — no special routing)

On the **host**, use the gadget like any USB audio interface:

1. Plug in the Pi (usb-host profile, cable to USB-C).
2. Open your DAW.
3. Select **USB Audio Passthrough** / **MPE Sound Module** as a **capture / input** device (44100 Hz stereo).
4. Arm a track and record — or enable input monitoring to hear yourself.

**No host-side install required.** No PipeWire loopback, no per-DAW routing rules.

On Linux the gadget appears in `arecord -l` and in REAPER's ALSA input list as `hw:Passthrough` (card name varies). On Windows/macOS it appears under Sound settings → **Input**.

**First time you arm a track after boot:** the Pi stall watchdog may restart Surge once (~4 s) because Surge wedged at boot before any host app opened the input. After that, audio is continuous for the rest of the session.

**Hearing yourself:** monitor through the DAW (input monitoring), not automatically through PC speakers. For playing feel without a DAW, use standalone profile + Sound Blaster headphones — see FAQ.

### Optional host tweak (Linux / PipeWire only)

If you notice extra delay when first arming a track:

```bash
./scripts/setup-host-usb-monitor.sh   # optional WirePlumber no-suspend drop-in
```

Uninstall with `./scripts/setup-host-usb-monitor.sh --uninstall`. This is **not** required for normal DAW use.

### Diagnostics (Linux)

```bash
arecord -l   # find card N — prefer hw:N,0 over plughw (plughw can be silent)
```


## Writer stall (root-caused 2026-08-02)

**Symptom:** Everything verifies green — gadget bound, Tier 0 selected, host enumerates the device — yet the host records pure digital silence while you play. A tone test (`speaker-test`) captures fine.

**Cause:** Surge/JUCE's ALSA output thread blocks **indefinitely** once the USB host stops consuming the gadget stream, and never recovers when the host returns. Signature on the Pi:

```
state: RUNNING
hw_ptr  : 1440217   <- racing
appl_ptr: 1068890   <- frozen
```

plus the Surge process dropping to **~0 CPU ticks** — the audio thread is not running, so nothing is rendered regardless of MIDI. Because the host is normally not capturing when Surge starts at boot, Surge is already wedged by the time a DAW opens the input.

Not a cable, power, MIDI-routing, volume/mute, or DAW problem. `speaker-test` works because it opens its own fresh PCM.

**Mitigation:**

1. **Pi (automatic)** — `uac2-stall-watchdog.service` restarts Surge when the host opens a capture stream but the writer is frozen. Enabled with the `usb-host` profile; no user action.
2. **Host (normal DAW use)** — opening any capture input (REAPER arm, `arecord`, etc.) starts the USB stream; the watchdog completes recovery. No loopback or custom routing needed.

The watchdog only acts when the host **is** streaming (`Playback Rate != 0`) but `appl_ptr` is frozen, so an idle module with nothing plugged in never restart-loops.

---

## Scripts and services

| Path | Role |
|------|------|
| `scripts/setup-usb-audio-gadget.sh` | Create/bind or tear down configfs UAC2 gadget |
| `config/usb-audio-gadget.service` | Start gadget at boot when profile is `usb-host` |
| `scripts/detect-audio-device.sh` | Tier 0: gadget card when profile is `usb-host` |
| `scripts/usb-host-verify.sh` | Pi-side profile/gadget/Surge checks + host `arecord` hints |
| `scripts/uac2-stall-watchdog.sh` | Restart Surge when the gadget writer wedges |
| `config/uac2-stall-watchdog.service` | Runs the stall watchdog (enabled with the `usb-host` profile) |
| `scripts/setup-host-usb-monitor.sh` | **Optional** host WirePlumber drop-in (Linux only) |
| `scripts/lib/uac2-card.sh` | Dynamic gadget card index + stream-state helpers |

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

**Working end-to-end as of 2026-08-02.** Surge → UAC2 → host verified with live playback (peak 0.66 captured on the host at a 512-sample buffer, 0 xruns). The long-standing "silent during play" issue was root-caused to the Surge/JUCE ALSA writer stall documented above, not the link layer.

Current config: `MPE_SURGE_BUFFER_SIZE=512` (~11.6 ms), gadget `req_number=2`, `p_hs_bint` at kernel default.

Remaining latency lever (untried): gadget `p_hs_bint=1` drops the USB service interval from ~1 ms to 125 µs. Lower buffers than 512 were deliberately not adopted — 512 is the conservative setting with CPU headroom for heavy patches.

Still pending: 10+ min soak test and a round-trip latency measurement. See **[USB-AUDIO-PASSTHROUGH-SPIKE.md](USB-AUDIO-PASSTHROUGH-SPIKE.md)** for the `hw` vs `plughw` and host-input findings.
