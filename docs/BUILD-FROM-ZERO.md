# Build from zero

A greenfield walkthrough: blank Raspberry Pi → working MPE sound module. Follow in order. You do **not** need access to the private `MPE-Library` repo for any of this — that repo is just Mitch's personal patch backup, not a dependency.

## 0. What you need

- Raspberry Pi 4 (4GB+) or Pi 5, running headless
- The exact reference hardware and wiring: **[`REFERENCE_BOM.md`](../REFERENCE_BOM.md)** + **[`docs/HARDWARE_WIRING.md`](HARDWARE_WIRING.md)**
- An MPE controller (Roli Seaboard/Lightpad, etc.) — only needed once you get to testing
- A PC/Mac/Linux machine to flash the SD card and SSH in from

## 1. Flash the OS

Use Raspberry Pi Imager → **Raspberry Pi OS Lite (64-bit)**. In the imager's advanced options, set a hostname (this repo's scripts default to `surge.local`), enable SSH, and set a username/password.

Boot the Pi, then confirm you can reach it:

```bash
ssh <your-user>@surge.local
```

## 2. Clone this repo on the Pi

```bash
cd ~
git clone https://github.com/M-Ferda/MPE-Module.git
cd MPE-Module
```

## 3. Install OS packages and build Surge XT from source

Surge XT doesn't ship a prebuilt ARM64 CLI, so you build it once on the Pi. Follow **[`docs/SURGE_CLI_HEADLESS_SETUP.md`](SURGE_CLI_HEADLESS_SETUP.md)** for the build steps (CMake build of `surge-xt-cli` under `~/surge`). This also gives you the **3,192 bundled factory + third-party patches** — they ship inside the Surge source tree, so building Surge is how you get the patch library, no separate download needed.

Also install the Python dependencies for the on-device UI:

```bash
sudo apt update && sudo apt install -y python3-pip i2c-tools
pip3 install -r requirements.txt
```

## 4. Wire the hardware

Wire the OLED + encoder per **[`docs/HARDWARE_WIRING.md`](HARDWARE_WIRING.md)** (exact GPIO pins, and which pins are reserved for the case fan). Verify the OLED is detected:

```bash
sudo i2cdetect -y 1   # should show 3c
```

## 5. Configure paths and systemd services

Back on your PC (not the Pi), point the deploy tooling at your Pi:

```bash
cd MPE-Module
cp config/mpe.env.example config/mpe.env
# edit config/mpe.env: set PI_HOST and PI_USER — PI_USER is REQUIRED, there's
# no default. It must match the username you set in Raspberry Pi Imager.
```

Then, on the Pi, generate the systemd units for your actual username/paths:

```bash
cd ~/MPE-Module
./scripts/configure-pi-paths.sh --local --force
```

This templates and installs the `surge-xt-cli` and `patch-browser` systemd services with the right `User=` and repo paths for your machine — see **[`docs/PATHS.md`](PATHS.md)** if you need to override defaults (non-standard clone location, different Pi username, etc.).

## 6. Enable and start the services

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now surge-xt-cli
sudo systemctl enable --now patch-browser
```

Check both are running:

```bash
systemctl status surge-xt-cli patch-browser
```

## 7. Plug in your MPE controller and play

Plug your controller into a Pi USB port. It should auto-connect. Confirm MPE is flowing:

```bash
aseqdump -p <controller-port>
```

You should see Note On messages on channels 2–15 (not just channel 1), per-note pitch bend, and CC74 (timbre) as you play.

## 8. Reboot and confirm it survives

```bash
sudo reboot
```

After boot (~25s), the OLED should show the patch browser. **There is no normal button click** — short taps do nothing. Hold ~1s to toggle category/patch mode; stop scrolling ~1.25s to load a patch. Full honest model: **[`docs/PATCH_BROWSER_UI.md`](PATCH_BROWSER_UI.md)**.

## Optional: editing/adding patches later

Once the base system works, patches are edited on a PC running the normal Surge XT GUI and pushed to the Pi with `scripts/deploy-patches.sh`. Full workflow: **[`docs/PATCH-EDITING-WORKFLOW.md`](PATCH-EDITING-WORKFLOW.md)**. This needs its own git remote for your patches (`MPE-Library` is Mitch's private one — set up your own, or skip this and just use the bundled factory/third-party library from step 3).
