# Build from zero

*Last updated: 2026-08-23 (America/Toronto)*

A greenfield walkthrough: blank Raspberry Pi → working MPE sound module. Follow in order. You do **not** need a separate private assets repo for the **manual** path — that pattern is optional for backing up custom patches, not a build dependency.

**What this repo is:** a **bootstrap / reference design**, not a finished product or installer. Expect SSH, git, CMake, and systemd — or an AI assistant walking you through those steps. There is no one-click setup yet.

**Who it's for:** comfortable Linux/Pi builders, synth-DIY people, or technical users with AI guidance. Not aimed at plug-and-play Surge users who don't want a terminal.

## Choose your path

| Path | Status | When to use |
|------|--------|-------------|
| **[Path A — Manual bringup](#path-a--manual-bringup)** (§0–§8 below) | **Proven** on reference Pi 4 and Pi 5 | Default today. Follow step by step on the Pi. |
| **[Path B — Laptop provisioning](#path-b--laptop-provisioning-in-testing)** | **In testing** — scripts shipped, first full rehearsal not done | Repeatable deploy from a reference capture + private assets; not a replacement for Path A until rehearsal passes. |

**Between us:** keep Path A. Path B is real tooling on `dev`, but we have not finished an end-to-end restore rehearsal ([`RESTORE.md`](RESTORE.md)). Use Path B to experiment or to rebuild from a known capture; fall back to Path A if anything feels off.

## Tested Surge XT version (reference device)

The maintainer's Pis were last smoke-tested with:


| Item                      | Value                                                                                              |
| ------------------------- | -------------------------------------------------------------------------------------------------- |
| **CLI version**           | `1.4.main.253f8d86` / `1.4.HEAD.253f8d86` (`surge-xt-cli --version`)                             |
| **Source commit**         | `253f8d86` on Surge `main` (2025-12-24)                                                            |
| **Latest stable release** | Surge XT **1.3.4** ([surge-synthesizer.github.io](https://surge-synthesizer.github.io/downloads/)) |
| **Pi 4 runtime binary**   | Generic/stock build — sha256 `c3680d6b…` (no `-mcpu`)                                               |
| **Pi 5 runtime binary**   | `-mcpu=cortex-a76` — sha256 `0ac9456c…` via `build-surge.sh --arch a76` + `install-surge-from-build.sh` |


This build is **newer than stable 1.3.4** (pre-1.4 nightly), not an old pinned release. Other Surge versions may work; they are not validated here. If you want the conservative path, checkout `release_xt/1.3.4` before building and smoke-test MPE + OSC patch loading the same way.

## Planned (not shipped yet)

These would lower the bar for less technical builders — tracked as follow-ups, not launch blockers:

- **Golden Pi 4 clone SD** — [`docs/PI4-CLONE-SD.md`](PI4-CLONE-SD.md) (configured card, no Imager setup)
- **Prebuilt** `surge-xt-cli` **for aarch64** — GitHub Release tarball + install script (~2–4 hrs to package from a known-good build); skips the 30–45 min Pi compile
- **Patch library bundle or shallow clone script** — factory + third-party data without a full Surge build
- **Non-git patch sync** — drag-and-drop or one-command push from PC (today: git + SSH scripts)

**Moved to Path B (in testing, not “planned”):** board-neutral [`build-appliance.sh`](../scripts/image/build-appliance.sh), external-state capture/restore, golden-image docs — see below.

---

## Path B — Laptop provisioning (in testing)

**Status:** Scripts are on `dev` and were used for reference captures (2026-08-23). **First full restore rehearsal is still open** — treat this path as experimental until [`RESTORE.md`](RESTORE.md) has a signed-off row.

**Requires:** laptop with `config/mpe.env` (or `~/.config/mpe/mpe.env.pi4` / `.pi5` via [`docs/LAPTOP-MPE-CLI.md`](LAPTOP-MPE-CLI.md)), optional private **MPE-Library** assets repo beside MPE-Module ([`docs/BACKUP_GUIDE.md`](BACKUP_GUIDE.md)).

**Typical flow:**

1. Flash Lite 64-bit (Imager — SSH on, set username).
2. Capture a working reference unit (once):

   ```bash
   MPE_CLI_CONFIG=~/.config/mpe/mpe.env.pi4 ./scripts/provision/capture-external-state.sh
   # Pi 5: MPE_CLI_CONFIG=~/.config/mpe/mpe.env.pi5 …
   ```

3. Build a fresh appliance from assets + that capture:

   ```bash
   ./scripts/image/build-appliance.sh --platform pi4 --state state/raspberrypi2-2026-08-23
   ./scripts/image/build-appliance.sh --platform pi5 --state state/raspberrypi5-2026-08-23
   ```

4. Add your SSH key, WiFi/Tailscale if needed, reboot, smoke-test.

**Canon:** [`docs/PI4-GOLDEN-IMAGE.md`](PI4-GOLDEN-IMAGE.md) (workflows A–D), [`docs/RESTORE.md`](RESTORE.md), [`docs/PI4-CLONE-SD.md`](PI4-CLONE-SD.md) (master-image clone path). Golden pre-`dd` capture: `capture-golden.sh --platform pi4|pi5|auto` on the reference Pi; laptop verify: `bake-golden.sh --platform pi4|pi5 verify`.

**Surge on Path B:** binary comes from private assets (`deploy-all.sh`) or from an on-Pi build during day0. Pi 5 should use **`build-surge.sh --arch a76`** then **`install-surge-from-build.sh --arch a76`** at commit `253f8d86` — not the Pi 4 generic binary long term. Provenance is recorded in capture `MANIFEST.md` / `surge-provenance.json` (once laptop `dev` with the latest capture scripts is on the Pi).

**If Path B fails or confuses:** stop and use [Path A](#path-a--manual-bringup) below. Nothing in Path B removes or replaces those steps.

---

## Path A — Manual bringup

Follow §0–§8 in order on the Pi (plus laptop `config/mpe.env` where noted).

## 0. What you need

- Raspberry Pi 4 (**4 GB**) or Pi 5 (**4 GB** recommended), running headless
- The exact reference hardware and wiring: `[REFERENCE_BOM.md](../REFERENCE_BOM.md)` + `[docs/HARDWARE_WIRING.md](HARDWARE_WIRING.md)`
- An MPE controller (Roli Seaboard/Lightpad, etc.) — only needed once you get to testing
- A PC/Mac/Linux machine to flash the SD card and SSH in from



## 1. Flash the OS

On Linux (Kubuntu/Ubuntu): install **[Raspberry Pi Imager](https://www.raspberrypi.com/software/)** — `sudo apt install rpi-imager` or download the AppImage from the site.

Flash **Raspberry Pi OS (64-bit) Lite** (Imager currently ships **Trixie**-based releases). In the imager's **gear icon / advanced options**: set wifi credentials, set hostname, enable SSH and create a key, and set your username and password.

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

Official Surge XT releases do **not** include a prebuilt ARM64 CLI in this repo yet (a release tarball is planned — see **Planned** above). For now you build once on the Pi. Follow [`docs/SURGE_CLI_HEADLESS_SETUP.md`](SURGE_CLI_HEADLESS_SETUP.md) for the CMake target `surge-xt-cli` under `~/surge` — **this is the canonical manual process; do not skip it unless you already have a known-good binary.**

Building Surge also gives you the **3,192 bundled factory + third-party patches** — they ship inside the Surge source tree. Alternative: install Surge XT on a PC and copy `resources/data/patches_*` to the Pi if you already have them.

**Optional — platform-tuned build at the same commit (`253f8d86`):** after day0 deps (`install-pi5-day0-tier1.sh` on Pi 5), you can use the parameterised scripts instead of hand-rolled CMake flags:

```bash
# Pi 4 reference / measurement baseline
./scripts/build-surge.sh --arch a72
./scripts/install-surge-from-build.sh --arch a72

# Pi 5 player / U10 replication
./scripts/build-surge.sh --arch a76
./scripts/install-surge-from-build.sh --arch a76
```

Build-only; install copies to `~/surge/build/surge_xt_products/surge-xt-cli` and writes a provenance sidecar. Logs: `~/surge-build-a72.log` / `~/surge-build-a76.log`. Generic (no `-mcpu`) is still valid if you use the headless setup doc as written.

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

**Encoder/OLED build:** wire the OLED + encoder per `[docs/HARDWARE_WIRING.md](HARDWARE_WIRING.md)`. Verify the OLED:

```bash
sudo apt install -y i2c-tools
sudo i2cdetect -y 1   # should show 3c
```

**SmartiPi touch build:** skip OLED wiring. Assemble the case, connect the panel, plug USB audio + MPE controller when ready. Follow [`docs/TOUCH_PATCH_BROWSER.md`](TOUCH_PATCH_BROWSER.md) for UI setup (`MPE_UI_MODE=touch`, `./scripts/setup-touch-pi.sh`). **Pi 5 with an existing Pi 4 reference:** use [`docs/PI5-PLAYER-SETUP-LOG.md`](PI5-PLAYER-SETUP-LOG.md) — it lists extra Trixie packages, env keys, and Pi 4 state to copy that generic setup scripts still miss (Path B capture/restore may reduce copy-paste once rehearsal passes).

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

This templates and installs the `surge-xt-cli` and `patch-browser` systemd services with the right `User=` and repo paths for your machine — see `[docs/PATHS.md](PATHS.md)` if you need to override defaults (non-standard clone location, different Pi username, etc.).

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

- **OLED Pi:** patch browser on the 1.3" display. Hold ~1s to toggle category/patch mode — see `[docs/PATCH_BROWSER_UI.md](PATCH_BROWSER_UI.md)`.
- **Touch Pi:** fullscreen patch browser on the SmartiPi panel. Tap patches to load; **…** for brightness and power.



## Optional: editing/adding patches later

Once the base system works, patches are edited on a PC running the normal Surge XT GUI and pushed to the Pi with `scripts/deploy-patches.sh`. Full workflow: [`docs/PATCH-EDITING-WORKFLOW.md`](PATCH-EDITING-WORKFLOW.md). Optional: use a private git repo for your custom patches, or skip that and use only the bundled factory/third-party library from step 3.

## Related (outside Path A)

| Doc | Use |
|-----|-----|
| [`PI4-GOLDEN-IMAGE.md`](PI4-GOLDEN-IMAGE.md) | Path B workflows, capture layers, platform matrix |
| [`RESTORE.md`](RESTORE.md) | Restore checklist; rehearsal gate for Path B |
| [`PI5-PLAYER-SETUP-LOG.md`](PI5-PLAYER-SETUP-LOG.md) | Pi 5 player bringup log |
| [`PI5-BRINGUP-RUNBOOK.md`](PI5-BRINGUP-RUNBOOK.md) | Measurement / overnight suites on Pi 5 |
| [`LAPTOP-MPE-CLI.md`](LAPTOP-MPE-CLI.md) | Split `mpe.env.pi4` / `mpe.env.pi5` on laptop |