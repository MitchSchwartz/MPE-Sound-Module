# Touch Patch Browser (SmartiPi / 5" landscape)

Fullscreen touch UI for the second Pi + **SmartiPi touch screen** (~5", **landscape**). Same Surge patch library and OSC loading as the encoder/OLED build — touch replaces every hardware control.

**Target resolution:** 800×480 landscape (typical for 5" DSI and HDMI panels). The UI auto-sizes if your panel reports something else — confirm on the Pi with:

```bash
fbset -s 2>/dev/null || cat /sys/class/graphics/fb0/virtual_size
```

## Local setup (SmartiPi Pi)

Run **on the Pi** after flashing Trixie Lite, cloning the repo, and building Surge ([`BUILD-FROM-ZERO.md`](BUILD-FROM-ZERO.md) step 3 — skip the OLED wiring step):

```bash
cd ~/MPE-Module
git checkout feature/touch-patch-browser-ui   # until merged to main
cp config/mpe.env.example config/mpe.env
echo 'MPE_UI_MODE=touch' >> config/mpe.env
./scripts/setup-touch-pi.sh
sudo reboot
```

After reboot, the touch browser should start fullscreen. Verify:

```bash
systemctl is-enabled touch-patch-browser patch-browser
systemctl status surge-xt-cli touch-patch-browser
journalctl -u touch-patch-browser -n 30
```

**One-time sudoers** (power menu + start script stopping other services): add to `sudo visudo`:

```
your-user ALL=(ALL) NOPASSWD: /sbin/poweroff, /sbin/reboot, /bin/systemctl
```

See [`docs/POWER_BUTTON_SETUP.md`](POWER_BUTTON_SETUP.md) for the encoder Pi pattern.

## Design goals

- **Browser is home:** patch list + detail on one screen; no separate playing mode
- **Non-disruptive:** dark theme, minimal chrome
- **Every feature on-screen:** browse, settings, brightness, power (no encoder)
- **Borrowed patterns:** `PatchScanner`, `PatchLoader`, `SurgeMonitor`, favorites folder, last-patch restore from `patch_browser_ui.py`

Interaction model:

| **Left nav (expanded)** | **Patches** in current folder, or **Folders** after Up | Tap patch → load + detail on main |
| **Up** | Switch left nav to folder list | Does not load anything |
| **Current** | When browsing another folder — jump to loaded patch's folder + patch list | |
| **< collapse** | Nav hides; `>` tab remains to expand | Main detail gets full width |
| **Main (right)** | Selected patch: vertical fader strip (Vol + future params) | No back button — list is always on the left |

## Hardware

- Raspberry Pi 5 (or 4) in a SmartiPi (or similar) case with ~5" **landscape** touch panel
- Most panels: **800×480** via DSI or HDMI+USB touch
- Same USB audio + MPE MIDI stack as the reference build
- **No encoder required** on this test rig

## Software prerequisites

On the touch Pi:

```bash
sudo apt update
sudo apt install -y python3-pygame libsdl2-2.0-0 libsdl2-image-2.0-0 libsdl2-mixer-2.0-0 libsdl2-ttf-2.0-0
cd ~/MPE-Module
pip3 install -r requirements.txt
```

Ensure Surge CLI and patch symlinks are already set up ([`BUILD-FROM-ZERO.md`](BUILD-FROM-ZERO.md)).

### Display / KMS

The touch browser uses **pygame + SDL KMS** (no full desktop required):

```bash
# Test manually (windowed — useful over SSH with desktop)
MPE_TOUCH_WINDOWED=1 ./scripts/start-touch-patch-browser.sh

# Fullscreen on the Pi console
./scripts/start-touch-patch-browser.sh
```

If the screen stays black, check:

```bash
ls /dev/fb* /dev/dri/*
journalctl -u touch-patch-browser -n 50
```

On Trixie (and recent Pi OS releases), your `config.txt` may need the vendor overlay for your specific panel (DSI) or HDMI `config.txt` timings. For sysfs brightness, add `dtoverlay=rpi-backlight` when supported. **HDMI 5" kits** sometimes have no software backlight — the slider will show unavailable; use the panel's physical control if present.

## Brightness

Controlled via Linux backlight sysfs:

```bash
# Quick test
echo 128 | sudo tee /sys/class/backlight/*/brightness
ls /sys/class/backlight/
```

The UI slider writes the same path and persists percent to `~/.patch_browser_brightness.json`.

**Permission fix (recommended once):**

```bash
# Allow the Pi user to set brightness without sudo for every drag
echo 'SUBSYSTEM=="backlight", RUN+="/bin/chmod 666 /sys/class/backlight/%k/brightness"' | \
  sudo tee /etc/udev/rules.d/99-backlight-permissions.rules
sudo udevadm control --reload-rules
sudo udevadm trigger
```

Without this rule, the slider may show “Brightness control unavailable” — power actions still use `sudo` like the encoder build.

## systemd service

Both browser units are installed by `configure-pi-paths.sh`. Which one **boots** is controlled by **`MPE_UI_MODE`** in `/etc/mpe/mpe.env`:

| `MPE_UI_MODE` | Enabled | Disabled |
|---------------|---------|----------|
| `oled` (default) | `patch-browser.service`, `boot-animation.service` | `touch-patch-browser.service` |
| `touch` | `touch-patch-browser.service` | `patch-browser.service`, `boot-animation.service` |

On the SmartiPi Pi, set in `config/mpe.env` then reconfigure:

```bash
MPE_UI_MODE=touch
cd ~/MPE-Module
./scripts/configure-pi-paths.sh --local --force
systemctl is-enabled patch-browser touch-patch-browser
```

Only one browser UI should be enabled — both talk to Surge over OSC.

## Config

Same `/etc/mpe/mpe.env` as the encoder build:

| Variable | Purpose |
|----------|---------|
| `MPE_UI_MODE` | `oled` or `touch` — which patch browser systemd enables at boot |
| `MPE_FAVORITES_NAME` | Quick-access folder under Surge user patches |
| `MPE_TOUCH_WINDOWED` | Set `1` for windowed dev mode |

## Development on this repo

```bash
python3 -m py_compile touch_patch_browser.py patch_browser/backlight.py
MPE_TOUCH_WINDOWED=1 python3 touch_patch_browser.py
```

Spec: [`Documents/specs/touch-patch-browser-spec.md`](../Documents/specs/touch-patch-browser-spec.md)

## Mixer faders

The patch detail pane uses a **vertical fader strip** (mixing-board style) instead of a thin horizontal slider:

- **Vol** — active; drag the handle up/down (top = louder). Persists to `~/.patch_browser_volume.json` and sends OSC via `PatchLoader.set_volume`.
- **Cut / Res / Snd** — dim placeholders for future Surge parameters.
- Touch **down + drag** on a fader; release does not trigger nav taps underneath.

Brightness in **System settings** still uses a horizontal slider (one-off control, not live mixing).

## Known gaps (v0)

- Surge error screen is toast-only; **Restart Surge** in System settings when the service is down
- Search/filter across 3000+ patches not implemented (scroll lists first)
- Portrait panels are unsupported for this rig (yours is landscape)
- Boot/shutdown animations still target the 1.3" OLED service
