# Build from zero

A greenfield walkthrough: blank Raspberry Pi → working MPE sound module. Follow in order. You do **not** need a separate private assets repo for any of this — that pattern is optional for backing up custom patches, not a build dependency.

**What this repo is:** a **bootstrap / reference design**, not a finished product or installer. Expect SSH, git, CMake, and systemd — or an AI assistant walking you through those steps. There is no one-click setup yet.

**Who it's for:** comfortable Linux/Pi builders, synth-DIY people, or technical users with AI guidance. Not aimed at plug-and-play Surge users who don't want a terminal.

## Tested Surge XT version (reference device)

The maintainer's Pi was last smoke-tested with:

| Item | Value |
|------|--------|
| **CLI version** | `1.4.main.253f8d86` (`surge-xt-cli --version`) |
| **Source commit** | `253f8d86` on Surge `main` (2025-12-24) |
| **Latest stable release** | Surge XT **1.3.4** ([surge-synthesizer.github.io](https://surge-synthesizer.github.io/downloads/)) |

This build is **newer than stable 1.3.4** (pre-1.4 nightly), not an old pinned release. Other Surge versions may work; they are not validated here. If you want the conservative path, checkout `release_xt/1.3.4` before building and smoke-test MPE + OSC patch loading the same way.

## Planned (not shipped yet)

These would lower the bar for less technical builders — tracked as follow-ups, not launch blockers:

- **Prebuilt `surge-xt-cli` for aarch64** — GitHub Release tarball + install script (~2–4 hrs to package from a known-good build); skips the 30–45 min Pi compile
- **Patch library bundle or shallow clone script** — factory + third-party data without a full Surge build
- **Non-git patch sync** — drag-and-drop or one-command push from PC (today: git + SSH scripts)

## 0. What you need

- Raspberry Pi 4 (4GB+) or Pi 5, running headless
- The exact reference hardware and wiring: **[`REFERENCE_BOM.md`](../REFERENCE_BOM.md)** + **[`docs/HARDWARE_WIRING.md`](HARDWARE_WIRING.md)**
- An MPE controller (Roli Seaboard/Lightpad, etc.) — only needed once you get to testing
- A PC/Mac/Linux machine to flash the SD card and SSH in from

## 1. Flash the OS

On Linux (Kubuntu/Ubuntu): install **[Raspberry Pi Imager](https://www.raspberrypi.com/software/)** — `sudo apt install rpi-imager` or download the AppImage from the site.

Flash **Raspberry Pi OS (64-bit) Lite** (Imager currently ships **Trixie**-based releases). In the imager's **gear icon / advanced options**: set hostname, enable SSH, and set your username and password (there is no default `pi` user anymore).

- **Encoder/OLED Pi:** Lite is correct (headless appliance).
- **SmartiPi touch Pi:** Lite is also fine — the touch browser uses pygame + KMS, no desktop required.

Boot the Pi, then confirm you can reach it (replace hostname and user with yours):

```bash
ssh <your-user>@<hostname>
```

## 2. Clone this repo on the Pi

```bash
cd ~
git clone https://github.com/MitchSchwartz/MPE-Sound-Module.git
cd MPE-Sound-Module
```

## 3. Install OS packages and build Surge XT from source

Official Surge XT releases do **not** include a prebuilt ARM64 CLI in this repo yet (a release tarball is planned — see **Planned** above). For now you build once on the Pi. Follow **[`docs/SURGE_CLI_HEADLESS_SETUP.md`](SURGE_CLI_HEADLESS_SETUP.md)** for the build steps (CMake target `surge-xt-cli` under `~/surge`).

Building Surge also gives you the **3,192 bundled factory + third-party patches** — they ship inside the Surge source tree. Alternative: install Surge XT on a PC and copy `resources/data/patches_*` to the Pi if you already have them.

After building, confirm version matches or note what you used:

```bash
~/surge/build/surge_xt_products/surge-xt-cli --version
```

Also install the Python dependencies for the on-device UI:

```bash
sudo apt update && sudo apt install -y python3-pip python3-pygame \
  libsdl2-2.0-0 libsdl2-image-2.0-0 libsdl2-mixer-2.0-0 libsdl2-ttf-2.0-0
pip3 install --break-system-packages -r requirements.txt 2>/dev/null || pip3 install -r requirements.txt
```

On **Trixie**, prefer `apt install python3-pygame` over pip alone (PEP 668 externally-managed Python).

## 4. Wire the hardware

**Encoder/OLED build:** wire the OLED + encoder per **[`docs/HARDWARE_WIRING.md`](HARDWARE_WIRING.md)**. Verify the OLED:

```bash
sudo apt install -y i2c-tools
sudo i2cdetect -y 1   # should show 3c
```

**SmartiPi touch build:** skip OLED wiring. Assemble the case, connect the panel, plug USB audio + MPE controller when ready. Follow **[`docs/TOUCH_PATCH_BROWSER.md`](TOUCH_PATCH_BROWSER.md)** for UI setup (`MPE_UI_MODE=touch`, `./scripts/setup-touch-pi.sh`). **Pi 5 with an existing Pi 4 reference:** use **[`docs/PI5-PLAYER-SETUP-LOG.md`](PI5-PLAYER-SETUP-LOG.md)** — it lists extra Trixie packages, env keys, and Pi 4 state to copy that generic setup scripts still miss.

## 5. Configure paths and systemd services

Back on your PC (not the Pi), point the deploy tooling at your Pi:

```bash
cd MPE-Sound-Module
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

**OLED Pi** (default `MPE_UI_MODE=oled`):

```bash
./scripts/configure-pi-paths.sh --local --force
```

**SmartiPi touch Pi** — set mode first, then run the touch setup script (installs deps + udev + services):

```bash
echo 'MPE_UI_MODE=touch' >> config/mpe.env
./scripts/setup-touch-pi.sh
```

Check Surge and the correct browser are running:

```bash
systemctl status surge-xt-cli patch-browser touch-patch-browser
```

(Only one of the two browser units should be **enabled**.)

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

After boot (~25s):

- **OLED Pi:** patch browser on the 1.3" display. Hold ~1s to toggle category/patch mode — see **[`docs/PATCH_BROWSER_UI.md`](PATCH_BROWSER_UI.md)**.
- **Touch Pi:** fullscreen patch browser on the SmartiPi panel. Tap patches to load; **…** for brightness and power.

## Optional: editing/adding patches later

Once the base system works, patches are edited on a PC running the normal Surge XT GUI and pushed to the Pi with `scripts/deploy-patches.sh`. Full workflow: **[`docs/PATCH-EDITING-WORKFLOW.md`](PATCH-EDITING-WORKFLOW.md)**. Optional: use a private git repo for your custom patches, or skip that and use only the bundled factory/third-party library from step 3.
