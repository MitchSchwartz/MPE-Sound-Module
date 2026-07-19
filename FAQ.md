# Frequently Asked Questions

## General Questions

### Q: Why build this instead of using Zynthian?

**A:** Zynthian is a great multi-engine workstation, but persistent, always-on MPE through its generalized preset architecture is a known friction point (confirmed on Zynthian's own forum as recently as 2025) — preset switching and MPE settings don't always survive a patch change. This project is narrow on purpose: one synth (Surge XT), MPE hardcoded on, nothing to reconfigure.

### Q: Can I use a different synth instead of Surge XT?

**A:** Not with this codebase as-is. The boot service, patch browser, and OSC control are all built specifically around Surge XT's CLI and OSC protocol. Swapping synths would mean rewriting `patch_browser_ui.py` and the systemd services. If you want to try, the synth needs to run on ARM64 Linux headless, support MPE natively, and expose some form of remote patch-load control.

### Q: Will this work on Pi 3?

**A:** Not tested, not recommended. The reference build is Pi 4 (4GB+) or Pi 5. Surge XT plus the patch browser UI is more CPU than a Pi 3 comfortably has to spare.

### Q: Can I add more encoders/buttons?

**A:** The UI code (`patch_browser_ui.py`) is built around exactly one encoder + button. Adding more is a real code change, not a config flag — GPIO is available if you want to extend it yourself.

### Q: What about using a MIDI controller with knobs instead of the encoder?

**A:** Not currently how this project works — the encoder drives the on-device patch browser (GPIO), not MIDI CCs. You could route a MIDI controller's CCs into Surge XT for sound-shaping (filter cutoff, etc.) independently of the browser; that's normal Surge MIDI mapping, not something this repo adds.

## Hardware Questions



### Q: What's the actual audio interface?

**A:** A **Creative Sound Blaster Play! 3** USB dongle (SB1730) — not a DAC HAT, no GPIO audio wiring. See `[REFERENCE_BOM.md](REFERENCE_BOM.md)` for the exact part and ASIN. Any USB audio interface with solid ALSA support should work if you adapt the device-detection script in `scripts/detect-audio-device.sh`.

### Q: Can I use a different display?

**A:** The reference build is a 1.3" I2C OLED (128×64, SH1106/SSD1306). The patch browser UI code assumes that display and one encoder — a different display means adapting `patch_browser_ui.py`, not just swapping hardware.

### Q: My encoder is noisy/jumping values

**A:** Poor — on the reference KY-040, scrolling is unreliable (missed and double steps are normal). The software debounce stack mostly **eliminated false button presses** (ghost scrolls, accidental taps); it did **not** make browsing feel good. See [`docs/ENCODER_NO_VCC.md`](docs/ENCODER_NO_VCC.md) for the no-VCC wiring trick and [`ENCODER_BUTTON_REVIEW.md`](ENCODER_BUTTON_REVIEW.md) for known UX debt.

**There is no normal click.** Releases under ~0.5s are ignored on purpose. Mode changes require a **~0.5–2s hold** (aim ~1s in practice). **Next major upgrade:** separate **enter (~1s)** and **back (~3s)** holds; **second encoder** down the road — see [`docs/PATCH_BROWSER_UI.md`](docs/PATCH_BROWSER_UI.md).

### Q: Can I add patches to favorites from the device?

**A:** **Yes — but it's unreliable.** Hold **2s+** → `Copy to <name>?` dialog → bold hold (~1s) to confirm. Folder name is **`MPE_FAVORITES_NAME`** (default **`!Quick Access`** — include the `!` in the folder name so it sorts first). Set in `/etc/mpe/mpe.env` on the Pi.

**Recommended:** curate the same folder on your PC in Surge XT and deploy ([`docs/PATCH-EDITING-WORKFLOW.md`](docs/PATCH-EDITING-WORKFLOW.md)). Full gesture + config table: [`docs/PATCH_BROWSER_UI.md`](docs/PATCH_BROWSER_UI.md).

### Q: Can I use this with a different MPE controller?

**A:** Yes, though I've only tested this with Roli Lumi Keys and Seaboard Block, anything that sends standard MPE over USB MIDI (Roli suite, Haken Continuum, LinnStrument, Sensel Morph, etc.) should be compatible. MIDI should auto-connect with no controller-specific config needed.

### Q: What's the total latency?

**A:** Not formally benchmarked end-to-end. The signal path is USB MIDI in → Surge XT CLI → direct ALSA → USB audio dongle out, with no JACK layer in between — fewer buffering stages than a JACK-based setup, but treat any specific number as unverified until measured on your own hardware.  
  
From experience there's no significant latency when playing.

## Software Questions



### Q: Where do I get the Surge XT ARM binary?

**A:** You build it from source on the Pi — see `[docs/SURGE_CLI_HEADLESS_SETUP.md](docs/SURGE_CLI_HEADLESS_SETUP.md)` and `[docs/BUILD-FROM-ZERO.md](docs/BUILD-FROM-ZERO.md)`. Expect it to take a while on a Pi (build from source, not a prebuilt CLI package).

### Q: How do I update Surge XT?

**A:** Rebuild from source in the same `~/surge` checkout on the Pi, then restart the service:

```bash
ssh <pi-user>@<hostname>
cd ~/surge && git pull && cd build && cmake --build . --target surge-xt-cli
sudo systemctl restart surge-xt-cli
```



### Q: Can I run other plugins alongside Surge?

**A:** Not in this setup — no plugin host is included, by design. Adding one (Carla, etc.) defeats the "no tech in the way" goal this project is built around.

### Q: How do I add custom patches?

**A:** Edit/save patches in Surge XT's normal GUI on your PC, then push them to the Pi with `scripts/deploy-patches.sh` (fast, patches only) or `scripts/deploy-all.sh` (full deploy). Full walkthrough: `[docs/PATCH-EDITING-WORKFLOW.md](docs/PATCH-EDITING-WORKFLOW.md)`.

Note that these scripts are only tested on my setup and may need adjustment since all local paths were recently updated from absolute to relative.

### Q: Does this run headless with no GUI at all?

**A:** Only a custom patch selector and folder navigator is included, which usies the oLED and encoder, and runs via CLI (`surge-xt-cli`), not  GUI. It auto-starts on boot via systemd.   
  
No X11, no VNC, no Xvfb needed on the Pi. You only ever see the Surge GUI when editing patches on your own PC.

## MPE Questions



### Q: Per-note pitch bend works, but not pressure/timbre

**A:**

- Check your controller's own settings (e.g. Roli Dashboard) — make sure all expression axes are enabled and being sent
- Confirm MPE is enabled in Surge (it's hardcoded on in this build's config, but worth verifying via logs), and verify parameter / channel mapping
- Some presets simply don't map pressure/timbre to anything audible — try a preset built for MPE (Pads/Leads generally respond better than Drums)



### Q: How do I know if MPE is working?

**A:**

```bash
# On the Pi, monitor raw MIDI
aseqdump -p <controller-port>

# Play notes — you should see:
# - Note On on channels 2-15 (not just channel 1)
# - Pitch Bend messages per note
# - Poly/Channel Aftertouch (pressure)
# - CC74 (timbre/Y-axis)
```



### Q: Some presets don't respond to MPE

**A:** Normal — not every Surge preset is built with MPE modulation targets. Pads and leads tend to respond well; drums and simple leads often don't need or use it.

### Q: Can I use this with a regular (non-MPE) MIDI keyboard?

**A:** Yes. Surge XT handles regular MIDI fine; you just won't get per-note expression since a normal keyboard doesn't send it.

## Performance Questions



### Q: Boot time is longer than ~30 seconds

**A:**

```bash
# Check what's slow
systemd-analyze blame

# Common culprits: WiFi/Bluetooth init, unnecessary services
# Disable in /boot/firmware/config.txt if not needed:
# dtoverlay=disable-wifi
# dtoverlay=disable-bt
```



### Q: CPU usage / temperature is too high

**A:**

1. Check for thermal throttling first: `vcgencmd measure_temp`
2. Reduce Surge polyphony (Settings > Max Voices) or disable expensive effects (reverb, delay) on the preset
3. Make sure nothing else is running on the Pi competing for CPU



## Networking Questions



### Q: How do I access the Pi remotely?

**A:**

```bash
ssh <pi-user>@<hostname>   # or your configured PI_USER@PI_HOST
```

No VNC/GUI access is set up or needed for normal operation — everything runs headless.

### Q: Can I use this with a DAW?

**A:** Not directly — this is a standalone instrument, not a plugin host. You could route MIDI from a DAW into your MPE controller and audio back out via the USB dongle, but that adds latency and isn't the intended use case.

## Troubleshooting



### Q: Nothing works after reboot

**A:**

```bash
ssh <pi-user>@<hostname> 'systemctl status surge-xt-cli patch-browser'
ssh <pi-user>@<hostname> 'journalctl -u surge-xt-cli -e'
```



### Q: Surge XT won't launch

**A:**

```bash
ssh <pi-user>@<hostname> 'journalctl -u surge-xt-cli -e --no-pager'
```

Check for missing shared libraries or an audio device that isn't where the config expects it (`scripts/detect-audio-device.sh` can help re-detect it).

### Q: USB devices not detected

**A:**

```bash
ssh <pi-user>@<hostname> 'lsusb'
ssh <pi-user>@<hostname> 'dmesg | tail -50'
```

Try a different USB port, or a powered hub if you're chaining multiple USB devices off the Pi.

## Contributing



### Q: How do I contribute to this project?

**A:** Areas that could use work:

- Non-git patch sync (drag-and-drop or one-click push — see the "known rough edge" note in the [README](README.md))
- Alternative display/encoder support
- Latency benchmarking
- Encoder debouncing / reliability
- Housing options that account for the OLED 

See GitHub issues for current tasks.

## Still Having Issues?

1. Check logs: `journalctl -u <service-name> -f` (services: `surge-xt-cli`, `patch-browser`)
2. Check `[docs/SURGE_CLI_HEADLESS_SETUP.md](docs/SURGE_CLI_HEADLESS_SETUP.md)` for the full technical setup
3. Search/file a GitHub issue with logs and Pi model/OS version included

