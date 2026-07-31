# Pi boot recovery (DSI / USB gadget / cmdline)

*Last updated: 2026-07-31 (America/Toronto)*

Use this when the SmartiPi panel stays on the **four raspberries** splash for several minutes, SSH never comes up, or boot loops after USB-host or DSI cmdline changes.

## Fastest path (no SD card edit)

1. **Power off** the Pi completely.
2. **Unplug the USB-C data cable** to the laptop/PC (gadget/peripheral mode). Leave power and normal USB-A devices (Roli, DAC) as they were.
3. Power on and wait ~90s, then try SSH:

   ```bash
   ssh -i ~/.ssh/surge_pi_key mitch@192.168.1.143
   ```

4. If SSH works, temporarily return to analog audio until stable:

   ```bash
   sudo sed -i 's/^MPE_AUDIO_PROFILE=.*/MPE_AUDIO_PROFILE=standalone/' /etc/mpe/mpe.env
   sudo systemctl disable --now usb-audio-gadget.service
   sudo systemctl restart surge-xt-cli touch-patch-browser
   ```

## SD card rollback order (Pi still stuck)

Mount the **boot** partition on another machine (`/boot/firmware` on Pi OS Bookworm).

### Step 1 — cmdline (safest first)

File: **`cmdline.txt`** (same folder as `config.txt`).

- Restore from backup if present: `cmdline.txt.bak.*` (created by `apply-dsi-cmdline.sh`).
- Or manually:
  - Ensure **`console=tty1`** is present (single line, space-separated tokens).
  - Remove **`fbcon=map:0`**, **`logo.nologo`**, extra **`console=serial0,115200`** if you do not need serial.

Reboot and test SSH.

### Step 2 — USB dwc2 peripheral overlay

File: **`config.txt`**.

Remove or comment:

```ini
dtoverlay=dwc2,dr_mode=peripheral
```

Reboot. This returns the USB-C port to **host** mode; USB-host audio profile will not work until you re-add the overlay (see [USB-AUDIO-HOST.md](USB-AUDIO-HOST.md)).

### Step 3 — disable boot splash unit (if userspace hangs)

Only after the kernel boots (SSH works) or from a chroot:

```bash
sudo systemctl disable touch-boot-animation.service
```

## Root causes we have seen

| Symptom | Likely cause |
|---------|----------------|
| Four raspberries, no SSH, USB-C tethered | `dr_mode=peripheral` + host PC connected during **early** boot |
| Kernel text flash then hang | Rare cmdline combos; keep `console=tty1` unless using `--strip-tty1` with serial recovery |
| SSH up, panel black | `touch-boot-animation` or DRM handoff — check `journalctl -u touch-boot-animation` |

## After recovery — safer re-apply

```bash
cd ~/MPE-Module
git pull
sudo ./scripts/apply-dsi-cmdline.sh          # keeps console=tty1; adds fbcon + quiet flags
sudo ./scripts/apply-dsi-cmdline.sh --strip-tty1   # only if serial console attached
```

For USB-host: add `dtoverlay=dwc2,dr_mode=peripheral`, reboot **with USB-C unplugged**, then plug the cable after `multi-user.target` is up.
