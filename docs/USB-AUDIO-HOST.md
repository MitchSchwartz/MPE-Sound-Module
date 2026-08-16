# USB audio to host PC (`usb-host` profile)

*Last updated: 2026-08-05 (America/Toronto)*

When the Pi is tethered to a laptop or desk PC, route Surge output over **USB-C** as a standard UAC2 playback device — no aux cable to the host. **Standalone gig mode** (`MPE_AUDIO_PROFILE=standalone`, default) is unchanged: Sound Blaster → 3.5 mm analog.

Full research and phased plan: **[USB-AUDIO-PASSTHROUGH-PLAN.md](USB-AUDIO-PASSTHROUGH-PLAN.md)**.

**Reference hardware:** **Pi 4 Model B** (live unit). Pi 5 notes in the plan doc are for future/alternate BOM only.

---

## Pi 4 quirks (read first)

| Quirk | Symptom | Fix |
|-------|---------|-----|
| **Boot with USB-C tethered** | Four raspberries hang, no SSH | Power on with **USB-C data unplugged** from host; plug in after Pi is up ([PI-BOOT-RECOVERY.md](PI-BOOT-RECOVERY.md)) |
| **PD power on same USB-C as data** | `udc` stuck `not attached`, host capture silent | **Split power:** GPIO 5V/GND or official PSU — **not** a PD dock on the gadget port ([USB-AUDIO-PASSTHROUGH-SPIKE.md](USB-AUDIO-PASSTHROUGH-SPIKE.md)) |
| **`[pi4]` overlay under `[all]`** | USB-A host ports dead (Roli, Sound Blaster) | Use **`[pi4]`** section only for `dr_mode=peripheral` |
| **Host capture left open** | Aux silent while badge still **USB** | Host-gated routing: Surge → UAC2 while host streams @ 48000. **Disarm Reaper**, remove stray PipeWire links (qpwgraph), don't set ALSA **capture default** to the gadget (see §Host quirks) |
| **`plughw` on Linux host** | Silent capture | Use **`hw:N,0`** in `arecord` / REAPER ALSA |

**Cable (Pi 4 desk):** prefer **USB-A (host PC) → USB-C (Pi)** data cable. Power the Pi from **GPIO or official PSU**, not from the same USB-C port that carries gadget data.

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

3. Plug a **data-capable USB cable** from the host PC to the Pi **USB-C** port. On **Pi 4**, use **USB-A (host) → USB-C (Pi)** with **split power** (see quirks table). Avoid PD docks on the gadget port.

4. Start the gadget and Surge:

   ```bash
   sudo systemctl start usb-audio-gadget.service
   sudo systemctl restart surge-xt-cli.service
   ```

   Or toggle **USB host audio** in touch settings (⋯) — writes `/etc/mpe/mpe.env` and restarts Surge.

5. On the **host**, select the gadget as a **capture / input / recording** device — **not** playback/output. The Pi sends audio *out* via UAC2; the host receives it on its **input** side (Passthrough). Linux: `pavucontrol` → Recording; Windows: Sound settings → Input.

### Profile persistence (Pi)

`MPE_AUDIO_PROFILE` lives in **`/etc/mpe/mpe.env`** and survives reboot:

- Touch settings toggle → `set-audio-profile.sh` updates the file and matching systemd units
- **`mpe-audio-profile-sync.service`** runs at boot (before Surge) and re-enables gadget + stall watchdog to match the file
- **`surge-xt-cli.service` `ExecStartPost`** starts the host-route watcher after Surge is up (usb-host only)
- **`configure-pi-paths.sh --force`** rewrites paths but **preserves** `MPE_AUDIO_PROFILE`, `MPE_SURGE_BUFFER_SIZE`, and `MPE_SURGE_SAMPLE_RATE` from the existing file

After a git pull on the Pi: `./scripts/configure-pi-paths.sh --local --force` — your audio mode is kept.

### Host capture on Linux

List cards:

```bash
arecord -l
```

Prefer **hardware** device for capture — `plughw:N,0` has been observed **silent** while `hw:N,0` works (tone test peak ~26267):

```bash
arecord -D hw:N,0 -f S16_LE -r 48000 -c 2 -d 5 /tmp/mpe-host-capture.wav
```

**Root-caused 2026-07-31:** if the Pi is powered through a USB-C PD dock plugged into the *same* port used for gadget data, `dwc2` can get stuck `not attached` (check `cat /sys/class/udc/*/state` on the Pi) even with MIDI/OSC correctly reaching Surge. This is a Pi 4 hardware limitation, not a Surge bug — see **[USB-AUDIO-PASSTHROUGH-SPIKE.md](USB-AUDIO-PASSTHROUGH-SPIKE.md)**. Power the Pi via GPIO 5V/GND, independent of the USB-C data cable, to avoid it.

`speaker-test` on the Pi without a host actively capturing the UAC2 stream may report **I/O error -5** — expected when nothing reads the gadget endpoint.

Pi-side checks: `./scripts/usb-host-verify.sh`

### Return to analog (gig / couch)

With **`MPE_USB_GADGET_PERSIST=1`** (default), switching to analog **does not disconnect** the USB gadget from the host — only Surge's output route changes to the Sound Blaster. PipeWire and REAPER keep the same capture device; you should not need to restart the DAW. The host input goes silent while in analog mode (badge shows **Analog**).

```bash
# Touch UI: System settings → USB host audio (toggle off)
# Or in /etc/mpe/mpe.env:
MPE_AUDIO_PROFILE=standalone
sudo ./scripts/set-audio-profile.sh standalone
```

To fully remove the gadget from the host (old behavior):

```bash
# In /etc/mpe/mpe.env:
MPE_USB_GADGET_PERSIST=0
sudo ./scripts/set-audio-profile.sh standalone
# Or one-shot teardown:
sudo ./scripts/setup-usb-audio-gadget.sh destroy
```

---

## How it works (Approach C)

```
[Roli] → Surge XT CLI → ALSA → UAC2 gadget card → USB-C → [Host speakers/DAW]
```

- **No** `alsaloop`, **no** dual analog+USB mirror.
- Sample rate: **48000 Hz** stereo (matches Surge tuning — see `PATCH_NORMALIZATION.md`).
- Switching profiles **restarts Surge** (same pattern as USB DAC hot-plug).

### Host-gated routing (usb-host)

Surge **must not** keep UAC2 PCM open unless the laptop/DAW is **actively capturing** (`Playback Rate != 0` on the gadget).

| Host capture | Surge output |
|--------------|--------------|
| **Idle** (disarmed, rate 0) | Sound Blaster if plugged, else Pi headphone (inaudible sink) |
| **Active** (armed, rate 48000) | UAC2 gadget → host |

`uac2-stall-watchdog.service` watches host stream rate and **restarts Surge on transitions** (~3–5 s, **Sync** badge). No stall heuristics, no Sound Blaster requirement for Reaper.

- **Disarm / re-arm:** brief Surge restart gap; **Reaper session stays open** (gadget stays bound via `MPE_USB_GADGET_PERSIST=1`).
- **Analog profile toggle:** Surge → Sound Blaster; USB input on host goes silent; **no Reaper restart**.
- **No external DAC:** idle sink is Pi headphone; Reaper path unchanged.

---

## Plug-and-play (host — no special routing)

On the **host**, use the gadget like any USB audio interface:

1. Plug in the Pi (usb-host profile, cable to USB-C).
2. Open your DAW.
3. Select **USB Audio Passthrough** / **MPE Sound Module** as a **capture / input** device (48000 Hz stereo).
4. Arm a track and record — or enable input monitoring to hear yourself.

**No host-side install required.** No PipeWire loopback, no per-DAW routing rules.

On Linux the gadget appears in `arecord -l` and in REAPER's ALSA input list as `hw:Passthrough` (card name varies). On Windows/macOS it appears under Sound settings → **Input**.

**First arm after boot:** watchdog restarts Surge onto UAC2 while your DAW is already capturing — typically **~3–5 s** (**Sync** badge). **Disarm** moves Surge back to idle output before the writer can wedge.

**Underlying constraint:** Surge/JUCE must not hold UAC2 open without an active host consumer. Host-gated routing enforces that by construction instead of detecting stalls after the fact.

**Hearing yourself:** monitor through the DAW (input monitoring), not automatically through PC speakers. For playing feel without a DAW, use standalone profile + Sound Blaster headphones — see FAQ.

### DAW hotplug (REAPER + PipeWire)

Switching **USB → Analog → USB** used to **destroy** the UAC2 gadget, which made the host drop the USB device and forced a REAPER restart even though `arecord -l` showed a card again (new enumeration / stale DAW handle).

**Default fix (`MPE_USB_GADGET_PERSIST=1`):** analog mode only changes **where Surge plays** (Sound Blaster vs gadget). The gadget stays **bound** — the host keeps one stable PipeWire source. REAPER device reselect often still fails on Linux; persist avoids the disconnect so you should not need a restart.

**PC cost:** negligible — idle USB audio class, no extra Surge load. In analog mode the host capture reads silence until you toggle USB back on.

**Unbind vs destroy:** writing `""` to the gadget UDC (**unbind**) still disconnects from the host the same as destroy. Persist mode skips both on profile switch.

### Host quirks (Linux desk PC)

**Aux vs USB is one route at a time in `usb-host` profile.** While the host keeps a capture stream open (`Playback Rate` 48000 on the gadget), Surge plays to **UAC2** — the Sound Blaster **aux is silent**. That is expected. To hear aux again: disarm all Reaper tracks using the Pi input, remove persistent **PipeWire** links (e.g. qpwgraph → other inputs), then wait for the host-route watcher to restart Surge onto Sound Blaster (~3–5 s).

**Do not** set ALSA `pcm.!default` capture to the gadget on the host. That can leave PipeWire holding `/dev/snd/pcm*D0c` open and block aux even when you think the DAW is idle. Use the named device only:

```bash
cp config/host/asoundrc.mpe-pi ~/.asoundrc   # pcm.mpe_pi — explicit, not default capture
./scripts/setup-host-usb-monitor.sh            # optional WirePlumber no-suspend
```

Uninstall WirePlumber drop-in: `./scripts/setup-host-usb-monitor.sh --uninstall`.

**Analog-only (aux) at the desk:** toggle **USB Audio off** in Pi settings (`standalone`) — Surge stays on Sound Blaster regardless of host capture state.

### Optional host tweak (Linux / PipeWire only)

If you notice extra delay when first arming a track, the WirePlumber no-suspend drop-in above helps. It is **not** required for normal DAW use.

### Diagnostics (Linux)

```bash
arecord -l   # find card N — prefer hw:N,0 over plughw (plughw can be silent)
```


## Writer stall (historical — superseded by host-gated routing)

**Root cause (2026-08-02):** Surge/JUCE ALSA output wedged when UAC2 opened with no host consumer.

**Previous mitigations** (lazy route, appl_ptr watchdog, grace periods) are **removed**. Host-gated routing prevents holding UAC2 unless capture is active; the watcher restarts Surge on capture open/close transitions.

---

## Scripts and services

| Path | Role |
|------|------|
| `scripts/setup-usb-audio-gadget.sh` | Create/bind or tear down configfs UAC2 gadget |
| `config/usb-audio-gadget.service` | Start gadget at boot when profile is `usb-host` |
| `scripts/detect-audio-device.sh` | UAC2 when host capturing; idle output otherwise |
| `scripts/usb-host-verify.sh` | Pi-side profile/gadget/Surge checks + host `arecord` hints |
| `scripts/uac2-stall-watchdog.sh` | Host capture open/close → restart Surge (route gate) |
| `config/uac2-stall-watchdog.service` | Runs the host-route watcher (enabled with `usb-host`) |
| `scripts/lib/uac2-host-route.sh` | Host-streaming flag read by detect + watcher |
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
