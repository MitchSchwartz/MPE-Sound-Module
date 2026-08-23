# Pi 5 player setup log — checklist from 2026-08-23 bringup

*Last updated: 2026-08-23 (America/Toronto)*

**Purpose:** Every step needed to go from a fresh Pi 5 SD card to a **working touch player** that matches the live Pi 4 — not the measurement suite track ([`PI5-BRINGUP-RUNBOOK.md`](PI5-BRINGUP-RUNBOOK.md)).

**Worked example:** `raspberrypi5` · user `pi` · Pi 4 remains `raspberrypi2` · user `mitch`.

Canon for generic steps: [`BUILD-FROM-ZERO.md`](BUILD-FROM-ZERO.md). Touch UI: [`TOUCH_PATCH_BROWSER.md`](TOUCH_PATCH_BROWSER.md).

---

## A. Before flash (laptop)

| Step | Do | Verify |
|------|-----|--------|
| A1 | **Pi Imager ≥ 2.0.10** (Trixie cloud-init needs it; 1.8.x silently breaks customization) | `rpi-imager --version` |
| A2 | Flash **Lite 64-bit Trixie**. Hostname **`raspberrypi5`** (must differ from Pi 4) | — |
| A3 | Imager: enable SSH, set user (Imager may still create `pi` — note actual user) | First boot: `whoami` |
| A4 | Imager: add laptop SSH public key | `ssh pi@raspberrypi5.local` |
| A5 | **27 W USB-C PSU** + active cooler on Pi 5 | `vcgencmd get_throttled` → `0x0` |
| A6 | Separate Sound Blaster + separate SD from Pi 4 (do not cannibalize control board) | — |

### Laptop SSH (`~/.ssh/config`)

```sshconfig
Host pi4 surge raspberrypi2 raspberrypi2.local
    HostName raspberrypi2.local
    User mitch
    IdentityFile ~/.ssh/surge_pi_key
    IdentitiesOnly yes

Host pi5 raspberrypi5 raspberrypi5.local
    HostName raspberrypi5.local
    User pi
    IdentityFile ~/.ssh/surge_pi5_key
    IdentitiesOnly yes
```

Use **mDNS** (`.local`), not a hardcoded DHCP IP.

### Laptop `mpe` CLI — two boards

`mpe` reads one target per invocation. Split configs:

| File | Contents |
|------|----------|
| `~/.config/mpe/mpe.env.pi4` | `PI_HOST=raspberrypi2.local` · `PI_USER=mitch` · `SSH_KEY=~/.ssh/surge_pi_key` |
| `~/.config/mpe/mpe.env.pi5` | `PI_HOST=raspberrypi5.local` · `PI_USER=pi` · `SSH_KEY=~/.ssh/surge_pi5_key` |

```bash
alias mpe4='MPE_CLI_CONFIG=~/.config/mpe/mpe.env.pi4 mpe'
alias mpe5='MPE_CLI_CONFIG=~/.config/mpe/mpe.env.pi5 mpe'
```

Default `~/.config/mpe/mpe.env` → whichever board is the daily player.

---

## B. Pi — base software

Run on the **new Pi** (SSH as its user).

| Step | Command / action | Verify |
|------|------------------|--------|
| B1 | `sudo apt update && sudo apt install -y git rsync` | — |
| B2 | Clone repo: `git clone https://github.com/MitchSchwartz/MPE-Sound-Module.git ~/MPE-Module && cd ~/MPE-Module && git checkout dev` | No deploy key on appliance — anonymous HTTPS pull only ([`PI-GITHUB-ACCESS.md`](PI-GITHUB-ACCESS.md)) |
| B3 | **Deploy from laptop** (Surge binary + factory patches + MPE-Library): `MPE_PERSONAL_REPO=/path/to/MPE-Library ./scripts/deploy-all.sh` with `config/mpe.env` pointing at Pi 5 | `~/surge/build/surge_xt_products/surge-xt-cli --version` |
| B4 | `echo 'MPE_UI_MODE=touch' >> ~/MPE-Module/config/mpe.env` | — |
| B5 | `./scripts/setup-touch-pi.sh` | Installs pygame/SDL, pip deps, udev, systemd |
| B6 | **`pip3 install --break-system-packages -r requirements.txt`** if setup skipped or failed | `pip3 show python-rtmidi` → must exist |
| B7 | **Extra apt packages** (Trixie Lite gaps hit on 2026-08-23 — not all pulled in by setup-touch-pi): | — |
| | `sudo apt install -y jackd2 libegl1 libegl-mesa0 libgles2 libgl1-mesa-dri mesa-vulkan-drivers` | `jackd --version`; touch UI starts without `EGL not initialized` |
| B8 | `./scripts/configure-pi-paths.sh --local --force` | `systemctl list-unit-files \| grep mpe` |

---

## C. Boot / display (touch Pi)

| Step | Command / action | Verify |
|------|------------------|--------|
| C1 | Append to `/boot/firmware/cmdline.txt`: `irqaffinity=0,1 threadirqs video=HDMI-A-1:d video=HDMI-A-2:d` | `~/MPE-Module/scripts/boot-assert-cmdline.sh` → ok |
| C2 | `./scripts/apply-dsi-cmdline.sh` (moves fbcon off DSI) | backup beside cmdline.txt |
| C3 | `/boot/firmware/config.txt`: `dtoverlay=vc4-kms-dsi-7inch`; comment out `display_auto_detect=1` if Pi 4 uses explicit overlay | `kmsprint \| head` → DSI 800×480 connected |
| C4 | **Reboot** | — |

Without C1, `mpe-jackd` refuses to start (`boot-assert-cmdline: MISSING irqaffinity=0,1`).

Without C7 graphics packages, `touch-patch-browser` dies with `EGL not initialized`.

---

## D. `/etc/mpe/mpe.env` — match Pi 4 player

After `configure-pi-paths.sh`, edit **`/etc/mpe/mpe.env`**. Critical keys discovered during bringup:

| Key | Pi 4 value | Wrong on fresh Pi 5 | Symptom if wrong |
|-----|------------|---------------------|------------------|
| `MPE_FAVORITES_NAME` | `"Quick Select"` | `"!Quick Access"` (template default) | Empty / wrong favorites tab |
| `MPE_PEAK_METER` | `1` | `0` | OUT meter shows **−** |
| `MPE_JACK_BUFFER` | `1024` | `256` | Different latency (optional for player) |
| `MPE_JACK_SOFTMODE` | `0` | — | — |
| `MPE_CPU_GOVERNOR` | `performance` | — | — |
| `RTMIDI_API` | `alsa` | *(unset → RtMidi tries JACK)* | Roli silent; remapper: `Midi Through Port-0 not found in RtMidi outputs: []` |

```bash
sudo systemctl enable mpe-peak-meter.service   # only when MPE_PEAK_METER=1
sudo systemctl restart mpe-jackd surge-xt-cli touch-patch-browser mpe-peak-meter
```

---

## E. User content — copy from Pi 4 (or MPE-Library)

Pi 5 does **not** inherit Pi 4 home-dir state from `deploy-all.sh` alone.

### E1 — Quick Select (71 patches)

Canon on device: `~/MPE-Library/assets/user-data/quick-select/latest/`

```bash
# On Pi 5 (after MPE-Library deployed):
cd ~/MPE-Module
python3 scripts/restore-quick-select.py \
  ~/MPE-Library/assets/user-data/quick-select/latest --rebuild-index
```

Requires `MPE_FAVORITES_NAME="Quick Select"` first.

### E2 — Theme + calibration (from live Pi 4)

```bash
# Laptop:
scp pi4:~/.patch_browser_ui.json pi5:~/.patch_browser_ui.json
scp pi4:~/.patch_browser_brightness.json pi5:~/.patch_browser_brightness.json
scp pi4:~/.patch_browser_normalization.json pi5:~/
scp pi4:~/.patch_browser_pressure.json pi5:~/
scp pi4:~/.patch_browser_metadata.json pi5:~/
ssh pi5 'chmod 600 ~/.patch_browser_*.json && sudo systemctl restart touch-patch-browser surge-xt-cli'
```

| File | What it controls |
|------|------------------|
| `.patch_browser_ui.json` | Theme mode, accent colour, custom palette |
| `.patch_browser_brightness.json` | DSI backlight % |
| `.patch_browser_normalization.json` | Per-patch loudness calibration |
| `.patch_browser_pressure.json` | MPE pressure curves |
| `.patch_browser_metadata.json` | Calibration metadata |

Long-term: `scripts/backup-appliance-state.sh` → commit `appliance-state/calibration/` in repo.

---

## F. ROLI / MIDI chain

**Architecture:** `LUMI USB → mpe-pressure-remap → Midi Through → Surge`

| Step | Verify |
|------|--------|
| Plug LUMI into **Pi 5 USB** (not Pi 4) | `lsusb \| grep 2af4` |
| `python-rtmidi` installed | `pip3 show python-rtmidi` |
| `RTMIDI_API=alsa` in `/etc/mpe/mpe.env` | remapper journal must **not** show JackClient errors |
| udev rule present | `/etc/udev/rules.d/99-roli-seaboard.rules` |
| Remapper running | `systemctl status mpe-pressure-remap` → **active**; log: `Listening: LUMI Keys BLOCK MIDI 1` |
| Surge input | `grep "Midi Through" ~/surge-cli.log` |
| Hot-plug | udev runs `scripts/roli-connect-debounce.sh`; or `sudo systemctl restart mpe-pressure-remap surge-xt-cli` |

If remapper is down, Surge still opens Midi Through but **hears nothing**.

---

## G. Final verification checklist

Run from laptop (`mpe5` alias):

```bash
mpe5 ping
mpe5 status          # mpe-jackd, surge-xt-cli, touch-patch-browser, mpe-peak-meter → active
mpe5 osc-check       # OSC 53280 listening
```

On Pi 5:

```bash
# Audio
aplay -l | grep -i "Sound Blaster"
cat /run/mpe/meter.state    # wired=1 online=1 (when MPE_PEAK_METER=1)

# Patches
find ~/Documents/Surge\ XT/Patches/Quick\ Select -name '*.fxp' | wc -l   # expect 71

# MIDI (LUMI plugged in)
lsusb | grep 2af4
aconnect -l | grep -iE 'lumi|RtMidi|Through'
journalctl -u mpe-pressure-remap -n 5 --no-pager
```

Human checks: patch browser on DSI, OUT meter moves, LUMI plays with matched loudness/pressure, theme matches Pi 4.

---

## H. Automation gaps (fix in repo when convenient)

Steps that **`setup-touch-pi.sh` / `deploy-all.sh` do not yet cover** — add here when fixed:

1. **`jackd2`** — not installed by setup-touch-pi; JACK fails silently until apt install.
2. **EGL/GLES/Mesa stack** — `libegl1`, `libegl-mesa0`, `libgles2`, `mesa-vulkan-drivers`, `libgl1-mesa-dri` for pygame kmsdrm on Trixie Pi 5.
3. **`requirements.txt` / `python-rtmidi`** — must run pip after setup; remapper hard-fails without it.
4. **`RTMIDI_API=alsa`** — should be in appliance env template or `mpe-pressure-remap.service` Environment=.
5. **`MPE_FAVORITES_NAME`** — template defaults to `!Quick Access`; Pi 4 uses `Quick Select` — align `config/mpe.env.example`.
6. **`MPE_PEAK_METER=1`** — enable unit when flag set (Pi 5 got `0` from a partial pi4 parity paste).
7. **User state sync** — no single script yet for ui + normalization + pressure + quick-select; candidates: extend `deploy-all.sh` step 7 or `scripts/sync-player-state-from-pi4.sh`.
8. **Two-Pi laptop config** — document in `mpe-cli` README (`mpe.env.pi4` / `mpe.env.pi5` pattern).

---

## I. What this log intentionally skips

- **`build-surge.sh --arch a76`** — use Pi 4/laptop binary for player smoke test; a76 build is measurement track.
- **IRQ census / Suite 0–3** — see [`PI5-BRINGUP-RUNBOOK.md`](PI5-BRINGUP-RUNBOOK.md).
- **Renaming `pi` → `mitch`** — optional; paths in `/etc/mpe/mpe.env` must match actual user.
- **Touch sudoers** (power menu NOPASSWD) — one-time, [`TOUCH_PATCH_BROWSER.md`](TOUCH_PATCH_BROWSER.md).

---

## Related paths

| Doc | Use |
|-----|-----|
| [`BUILD-FROM-ZERO.md`](BUILD-FROM-ZERO.md) | Generic greenfield |
| [`PI5-BRINGUP-RUNBOOK.md`](PI5-BRINGUP-RUNBOOK.md) | Measurement / overnight suites |
| [`scripts/backup-appliance-state.sh`](../scripts/backup-appliance-state.sh) | Pull calibration into repo |
| [`scripts/restore-quick-select.py`](../scripts/restore-quick-select.py) | Restore Quick Select folder |
| [`Documents/specs/system-hygiene-baseline.md`](../Documents/specs/system-hygiene-baseline.md) | Roli detection / remapper behaviour |
